"""LA-level calibration -> learned proxy weights (the methodological core).

The one hard constraint: suicide data is reliable only at Local Authority level.
So we LEARN the proxy weights here, at LA grain, then apply them to small-area
proxies in score.py.

Specification (design.md §4):

    deaths_LA ~ Poisson/NegBin( exp(b0 + b1*dep + b2*occ + b3*iso),
                                offset = log(male_working_age_pop) )

  * COUNTS with a population offset, not rates — respects the Poisson nature of
    rare-event data and weights LAs by population.
  * Proxies are standardised (z-scored) across LAs so coefficients are
    comparable in magnitude (effect per 1 SD) and usable as relative weights.
  * Negative binomial if residuals are over-dispersed (tested & reported),
    Poisson otherwise.
  * Persist coefficients, confidence intervals, dispersion, goodness-of-fit.
    A coefficient not distinguishable from zero is flagged, not quietly kept.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import statsmodels.api as sm

from .config import Config

PROXY_COLS = {
    "deprivation": "deprivation_proxy",
    "occupation": "occupation_proxy",
    "isolation": "isolation_proxy",
}


def _aggregate_to_la(cfg: Config) -> pd.DataFrame:
    """Population-weighted mean of each small-area proxy, up to LA."""
    interim = cfg.path("interim")
    geo = pd.read_parquet(interim / "dim_geography.parquet")[["area_code", "la_code"]]
    pop = pd.read_parquet(interim / "dim_population.parquet")[["area_code", "male_working_age_pop"]]
    dep = pd.read_parquet(interim / "fact_deprivation.parquet")[["area_code", "deprivation_proxy"]]
    occ = pd.read_parquet(interim / "fact_occupation.parquet")[["area_code", "occupation_proxy"]]
    iso = pd.read_parquet(interim / "fact_isolation.parquet")[["area_code", "isolation_proxy"]]

    df = (geo.merge(pop, on="area_code").merge(dep, on="area_code")
             .merge(occ, on="area_code").merge(iso, on="area_code"))
    # Population-weighted mean per LA, computed as Σ(proxy·pop)/Σ(pop) without a
    # groupby.apply (vectorised, and free of the pandas apply deprecation).
    w = df["male_working_age_pop"]
    weighted = pd.DataFrame({"la_code": df["la_code"], "_w": w})
    for short, col in PROXY_COLS.items():
        weighted[short] = df[col] * w
    g = weighted.groupby("la_code", as_index=False).sum()
    for short in PROXY_COLS:
        g[short] = g[short] / g["_w"]
    g = g.rename(columns={"_w": "male_working_age_pop"})
    return g[["la_code", *PROXY_COLS.keys(), "male_working_age_pop"]]


def run(cfg: Config) -> dict:
    interim = cfg.path("interim")
    la_proxies = _aggregate_to_la(cfg)
    suicide = pd.read_parquet(interim / "fact_suicide_la.parquet")[["la_code", "deaths"]]

    df = la_proxies.merge(suicide, on="la_code", how="inner")
    if len(df) < 10:
        print(f"[calibrate] WARNING: only {len(df)} LAs — weights will be noisy.")

    shorts = list(PROXY_COLS.keys())
    # Standardise proxies (z-score) so coefficients are per-1-SD effects.
    means = df[shorts].mean()
    stds = df[shorts].std(ddof=0).replace(0, 1.0)
    Zstd = (df[shorts] - means) / stds
    Z = sm.add_constant(Zstd)
    y = df["deaths"].to_numpy()
    offset = np.log(df["male_working_age_pop"].clip(lower=1).to_numpy())

    conf = float(cfg["calibration"].get("confidence_level", 0.95))
    alpha_ci = 1.0 - conf

    def _fit(X):
        """Fit the chosen family (set below) with the population offset."""
        if family == "negbin":
            try:
                return sm.NegativeBinomial(y, X, offset=offset).fit(disp=0, maxiter=100)
            except Exception:
                return sm.GLM(y, X, family=sm.families.Poisson(), offset=offset).fit()
        return sm.GLM(y, X, family=sm.families.Poisson(), offset=offset).fit()

    # 1) Poisson fit + dispersion test.
    pois = sm.GLM(y, Z, family=sm.families.Poisson(), offset=offset).fit()
    dispersion = float(pois.pearson_chi2 / pois.df_resid)

    requested = cfg["calibration"].get("family", "auto")
    use_negbin = (requested == "negbin") or (requested == "auto" and dispersion > 1.5)
    family = "negbin" if use_negbin else "poisson"
    res = _fit(Z) if use_negbin else pois

    # Univariate (single-predictor) rate ratios — exposes collinearity: a proxy
    # that is positive on its own but flips sign in the multivariable model is
    # sharing variance with a stronger correlated proxy, not protective.
    univariate = {}
    univariate_fit = {}
    for short in shorts:
        u = _fit(sm.add_constant(Zstd[[short]]))
        univariate[short] = float(np.exp(u.params[short]))
        uci = u.conf_int(alpha=alpha_ci)
        uci.index = list(u.params.index)
        univariate_fit[short] = {"coef": float(u.params[short]),
                                 "ci": [float(uci.loc[short, 0]), float(uci.loc[short, 1])]}

    ci = res.conf_int(alpha=alpha_ci)
    ci.index = list(res.params.index)  # ensure named index

    components: dict[str, dict] = {}
    for short in shorts:
        coef = float(res.params[short])
        lo, hi = float(ci.loc[short, 0]), float(ci.loc[short, 1])
        pval = float(res.pvalues[short])
        flip = (coef < 0) and (univariate[short] > 1.0)   # collinearity sign-flip
        components[short] = {
            "coef": coef,
            "rate_ratio": float(np.exp(coef)),
            "univariate_rate_ratio": univariate[short],
            "ci_low": lo,
            "ci_high": hi,
            "rate_ratio_ci": [float(np.exp(lo)), float(np.exp(hi))],
            "pvalue": pval,
            "significant": bool((lo > 0) or (hi < 0)),
            "collinearity_signflip": bool(flip),
            "proxy_mean": float(means[short]),
            "proxy_std": float(stds[short]),
        }

    # --- Three weighting schemes (see config scoring.weighting_scheme) ------
    def _norm(d: dict) -> dict:
        tot = sum(d.values()) or 1.0
        return {k: v / tot for k, v in d.items()}

    # A) multivariable: positive part of each partial coefficient.
    scheme_multi = _norm({k: max(components[k]["coef"], 0.0) for k in shorts})

    # B) univariate: each proxy's own log rate-ratio (positive part).
    scheme_uni = _norm({k: max(np.log(univariate[k]), 0.0) for k in shorts})

    # C) composite: merge the collinear deprivation+occupation into one
    #    standardised "economic disadvantage" factor (equal mean of the two
    #    z-scores), calibrate that vs isolation, then split the disadvantage
    #    weight equally back to deprivation and occupation.
    disadvantage = (Zstd["deprivation"] + Zstd["occupation"]) / 2.0
    Zc = sm.add_constant(pd.DataFrame(
        {"disadvantage": disadvantage, "isolation": Zstd["isolation"]}))
    cres = _fit(Zc)
    cci = cres.conf_int(alpha=alpha_ci)
    cci.index = list(cres.params.index)
    w_dis = max(float(cres.params["disadvantage"]), 0.0)
    w_iso_c = max(float(cres.params["isolation"]), 0.0)
    scheme_comp = _norm({"deprivation": w_dis / 2, "occupation": w_dis / 2,
                         "isolation": w_iso_c})
    composite_fit = {
        "disadvantage": {"coef": float(cres.params["disadvantage"]),
                         "rate_ratio": float(np.exp(cres.params["disadvantage"])),
                         "ci_low": float(cci.loc["disadvantage", 0]),
                         "ci_high": float(cci.loc["disadvantage", 1]),
                         "pvalue": float(cres.pvalues["disadvantage"])},
        "isolation": {"coef": float(cres.params["isolation"]),
                      "rate_ratio": float(np.exp(cres.params["isolation"])),
                      "ci_low": float(cci.loc["isolation", 0]),
                      "ci_high": float(cci.loc["isolation", 1]),
                      "pvalue": float(cres.pvalues["isolation"])},
    }

    schemes = {"multivariable": scheme_multi, "univariate": scheme_uni,
               "composite": scheme_comp}

    # Unified "factors" representation per scheme, so sensitivity.py can perturb
    # any scheme within its CIs the same way: sample each factor's coef, take the
    # positive part as the factor weight, distribute it over components by the
    # loadings, then renormalise.
    scheme_fits = {
        "multivariable": [
            {"coef": components[k]["coef"], "ci": [components[k]["ci_low"],
             components[k]["ci_high"]], "loadings": {k: 1.0}} for k in shorts],
        "univariate": [
            {"coef": univariate_fit[k]["coef"], "ci": univariate_fit[k]["ci"],
             "loadings": {k: 1.0}} for k in shorts],
        "composite": [
            {"coef": composite_fit["disadvantage"]["coef"],
             "ci": [composite_fit["disadvantage"]["ci_low"],
                    composite_fit["disadvantage"]["ci_high"]],
             "loadings": {"deprivation": 0.5, "occupation": 0.5}},
            {"coef": composite_fit["isolation"]["coef"],
             "ci": [composite_fit["isolation"]["ci_low"],
                    composite_fit["isolation"]["ci_high"]],
             "loadings": {"isolation": 1.0}},
        ],
    }
    active = cfg["scoring"].get("weighting_scheme", "composite")
    if active not in schemes:
        raise ValueError(f"Unknown weighting_scheme {active!r}; choose one of {list(schemes)}")
    for k in shorts:
        components[k]["weight"] = schemes[active][k]   # active weights for score.py

    weights = {
        "family": family,
        "dispersion": dispersion,
        "n_las": int(len(df)),
        "confidence_level": conf,
        "intercept": float(res.params["const"]),
        "llf": float(res.llf),
        "aic": float(res.aic),
        "components": components,
        "active_scheme": active,
        "schemes": schemes,
        "scheme_fits": scheme_fits,
        "composite_fit": composite_fit,
        "suicide_signal_weight": float(cfg["scoring"]["suicide_signal_weight"]),
    }

    out = cfg.path("weights")
    with open(out, "w") as fh:
        json.dump(weights, fh, indent=2)

    # --- Console report ----------------------------------------------------
    print("\n[calibrate] ===== LA-level calibration =====")
    print(f"  model: {family} | LAs: {weights['n_las']} | dispersion: {dispersion:.2f} "
          f"| AIC: {weights['aic']:.1f}")
    print(f"  {'component':<13} {'RR(multi)':>10} {'RR(uni)':>9} "
          f"{f'{int(conf*100)}% CI':>18} {'p':>7}  sig  weight")
    for k in shorts:
        c = components[k]
        ci_str = f"[{c['rate_ratio_ci'][0]:.2f}, {c['rate_ratio_ci'][1]:.2f}]"
        sig = " * " if c["significant"] else "   "
        print(f"  {k:<13} {c['rate_ratio']:>10.3f} {c['univariate_rate_ratio']:>9.3f} "
              f"{ci_str:>18} {c['pvalue']:>7.3f}  {sig} {c['weight']:.3f}")
    print(f"  suicide_signal carried separately at weight "
          f"{weights['suicide_signal_weight']:.2f}")
    flipped = [k for k in shorts if components[k]["collinearity_signflip"]]
    if flipped:
        print(f"  NOTE: {', '.join(flipped)} positive alone but negative in the "
              f"multivariable fit (collinearity) -> weight 0 under 'multivariable';\n"
              f"        the 'composite' scheme restores it. sensitivity.py tests if it matters.")
    # Scheme comparison table.
    print(f"\n  weighting schemes  {'deprivation':>12} {'occupation':>11} {'isolation':>10}")
    for name, sc in schemes.items():
        mark = " (active)" if name == active else ""
        print(f"    {name:<15} {sc['deprivation']:>12.3f} {sc['occupation']:>11.3f} "
              f"{sc['isolation']:>10.3f}{mark}")
    print(f"  -> {out}\n")
    return weights
