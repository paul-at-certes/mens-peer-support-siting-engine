"""LA-level calibration — a CHECK on the declared weights, not their source.

The one hard constraint: suicide data is reliable only at Local Authority level.
The original design fitted an LA-level model and promoted its coefficients to
the scoring weights. That does not survive contact with the data:

  * With 331 LAs and three mutually collinear proxies (deprivation correlates
    0.72 with isolation and 0.63 with occupation at LA level) the model does not
    identify the weights. Deprivation cannot be shown to be positive once the
    other two are in the model: its multivariable coefficient is negative in
    every specification tested, significantly so in the pooled one we ship.
    Collinearity, not epidemiology — on its own the proxy is positive.
  * Measured on the real output, equal weights disagrees with each fitted scheme
    about as much as the fitted schemes disagree with each other, while the
    choice still moves up to 12 of the top 20 areas. That is a consequential
    choice made on non-identifying evidence.

So `need_index` is an allocation index with declared weights (config.yaml
`scoring.component_weights`), and this module's job is to VETO a declared weight
the data actively contradicts. See docs/adr/0001-calibration-as-veto.md.

    deaths_LA ~ Poisson/NegBin( exp(b0 + b1*dep + b2*occ + b3*iso),
                                offset = log(at_risk_population) )

  * COUNTS with a population offset. The offset is the outcome dataset's OWN
    denominator, so numerator and denominator cover the same population.
  * Proxies z-scored across LAs, so coefficients are per-1-SD and comparable.
  * Negative binomial if over-dispersed (tested & reported), Poisson otherwise.
  * The veto is tested on the UNIVARIATE fits: under collinearity a partial
    coefficient does not answer "is this proxy associated with the outcome",
    which is the question the veto asks.

Non-blocking by design: if this cannot run (no outcome data, no network) the
pipeline still scores, and the veto is reported as "not run".
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

# The composite scheme merges the most-collinear PAIR. Measured at LA level:
# deprivation~isolation 0.72, deprivation~occupation 0.63, occupation~isolation
# 0.25. So deprivation+isolation merge and occupation — the least entangled
# proxy — stands alone. (The original code merged deprivation+occupation, which
# is not the tighter pair.)
COMPOSITE_MERGE = ("deprivation", "isolation")
COMPOSITE_SOLO = "occupation"


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


def _normalise(d: dict) -> dict:
    tot = sum(d.values()) or 1.0
    return {k: v / tot for k, v in d.items()}


def _veto(cfg: Config, declared: dict, univariate_fit: dict,
          components: dict) -> dict:
    """Does the LA fit contradict any declared weight?

    Tested on the univariate coefficient's confidence interval:
      contradicted -> CI entirely below zero: the data says this proxy is
                      associated with FEWER deaths, yet we weight it upward.
      unsupported  -> CI spans zero and we lean on it anyway (declared weight
                      above `unsupported_weight_floor`).
    A multivariable sign flip against a positive univariate is recorded as
    collinearity — informational, never a veto, since that is the artefact this
    whole design exists to route around.
    """
    floor = float(cfg["calibration"].get("unsupported_weight_floor", 0.15))
    findings = []
    for name, w in declared.items():
        lo, hi = univariate_fit[name]["ci"]
        if hi < 0:
            findings.append({
                "component": name, "severity": "contradicted", "declared_weight": w,
                "univariate_ci": [lo, hi],
                "message": (f"{name}: the council-level fit associates this factor with "
                            f"FEWER deaths, not more, yet it carries a weight of {w:.2f} "
                            f"(95% confidence interval {lo:.3f} to {hi:.3f}, entirely "
                            f"below zero)."),
            })
        elif lo <= 0 <= hi and w > floor:
            findings.append({
                "component": name, "severity": "unsupported", "declared_weight": w,
                "univariate_ci": [lo, hi],
                "message": (f"{name}: no association can be distinguished from zero at "
                            f"council level (95% confidence interval {lo:.3f} to "
                            f"{hi:.3f}), yet it carries a weight of {w:.2f}, above the "
                            f"{floor:.2f} threshold at which we ask for evidence."),
            })
    for name in declared:
        if components[name]["collinearity_signflip"]:
            findings.append({
                "component": name, "severity": "collinearity",
                "declared_weight": declared[name],
                "message": (f"{name}: on its own it points the expected way, but flips "
                            f"when the factors are fitted together, because it overlaps "
                            f"with another one. Noted for information; not a veto."),
            })
    severities = {f["severity"] for f in findings}
    status = ("contradicted" if "contradicted" in severities
              else "unsupported" if "unsupported" in severities
              else "collinearity" if severities else "pass")
    return {"status": status, "findings": findings, "unsupported_weight_floor": floor}


def run(cfg: Config) -> dict:
    interim = cfg.path("interim")
    la_proxies = _aggregate_to_la(cfg)
    suicide = pd.read_parquet(interim / "fact_suicide_la.parquet")

    # Offset = the outcome dataset's OWN denominator where it has one, so the
    # numerator (deaths in the published age band) and the denominator cover the
    # same population. Falling back to male working-age population would mix an
    # age-10+ numerator with a 16-64 denominator, and that ratio varies with
    # local age structure — which correlates with deprivation.
    if "population" in suicide.columns and suicide["population"].notna().any():
        suicide = suicide[["la_code", "deaths", "population"]]
        offset_source = "outcome dataset denominator"
    else:
        suicide = suicide[["la_code", "deaths"]]
        offset_source = "male working-age population (outcome denominator absent)"

    df = la_proxies.merge(suicide, on="la_code", how="inner")
    if "population" not in df.columns:
        df["population"] = df["male_working_age_pop"]
    if len(df) < 10:
        print(f"[calibrate] WARNING: only {len(df)} LAs — the check will be weak.")

    shorts = list(PROXY_COLS.keys())
    means, stds = df[shorts].mean(), df[shorts].std(ddof=0).replace(0, 1.0)
    Zstd = (df[shorts] - means) / stds
    Z = sm.add_constant(Zstd)
    y = df["deaths"].to_numpy()
    offset = np.log(df["population"].clip(lower=1).to_numpy())

    conf = float(cfg["calibration"].get("confidence_level", 0.95))
    alpha_ci = 1.0 - conf

    # Poisson first, to test dispersion; then the chosen family.
    pois = sm.GLM(y, Z, family=sm.families.Poisson(), offset=offset).fit()
    dispersion = float(pois.pearson_chi2 / pois.df_resid)
    requested = cfg["calibration"].get("family", "auto")
    family = "negbin" if (requested == "negbin"
                          or (requested == "auto" and dispersion > 1.5)) else "poisson"

    def _fit(X):
        if family == "negbin":
            try:
                return sm.NegativeBinomial(y, X, offset=offset).fit(disp=0, maxiter=100)
            except Exception:
                return sm.GLM(y, X, family=sm.families.Poisson(), offset=offset).fit()
        return sm.GLM(y, X, family=sm.families.Poisson(), offset=offset).fit()

    res = _fit(Z) if family == "negbin" else pois

    def _ci(fit):
        c = fit.conf_int(alpha=alpha_ci)
        c.index = list(fit.params.index)
        return c

    # Univariate fits — the basis of the veto.
    univariate_fit = {}
    for short in shorts:
        u = _fit(sm.add_constant(Zstd[[short]]))
        uci = _ci(u)
        univariate_fit[short] = {
            "coef": float(u.params[short]),
            "rate_ratio": float(np.exp(u.params[short])),
            "ci": [float(uci.loc[short, 0]), float(uci.loc[short, 1])],
            "pvalue": float(u.pvalues[short]),
        }

    ci = _ci(res)
    components: dict[str, dict] = {}
    for short in shorts:
        coef, (lo, hi) = float(res.params[short]), (float(ci.loc[short, 0]), float(ci.loc[short, 1]))
        components[short] = {
            "coef": coef,
            "rate_ratio": float(np.exp(coef)),
            "ci_low": lo, "ci_high": hi,
            "rate_ratio_ci": [float(np.exp(lo)), float(np.exp(hi))],
            "pvalue": float(res.pvalues[short]),
            "significant": bool((lo > 0) or (hi < 0)),
            "univariate_rate_ratio": univariate_fit[short]["rate_ratio"],
            "collinearity_signflip": bool(coef < 0 and univariate_fit[short]["coef"] > 0),
            "proxy_mean": float(means[short]), "proxy_std": float(stds[short]),
        }

    # --- Comparison schemes -------------------------------------------------
    # These no longer drive scoring. sensitivity.py ranks under each so the
    # question "would a different defensible weighting change the shortlist?"
    # is answered with evidence rather than asserted.
    a, b = COMPOSITE_MERGE
    merged = (Zstd[a] + Zstd[b]) / 2.0
    Zc = sm.add_constant(pd.DataFrame({"merged": merged, COMPOSITE_SOLO: Zstd[COMPOSITE_SOLO]}))
    cres = _fit(Zc)
    cci = _ci(cres)
    composite_fit = {
        "merged": {"components": list(COMPOSITE_MERGE),
                   "coef": float(cres.params["merged"]),
                   "rate_ratio": float(np.exp(cres.params["merged"])),
                   "ci": [float(cci.loc["merged", 0]), float(cci.loc["merged", 1])],
                   "pvalue": float(cres.pvalues["merged"])},
        COMPOSITE_SOLO: {"components": [COMPOSITE_SOLO],
                         "coef": float(cres.params[COMPOSITE_SOLO]),
                         "rate_ratio": float(np.exp(cres.params[COMPOSITE_SOLO])),
                         "ci": [float(cci.loc[COMPOSITE_SOLO, 0]), float(cci.loc[COMPOSITE_SOLO, 1])],
                         "pvalue": float(cres.pvalues[COMPOSITE_SOLO])},
    }
    w_merged = max(float(cres.params["merged"]), 0.0)
    scheme_comp = _normalise({a: w_merged / 2, b: w_merged / 2,
                              COMPOSITE_SOLO: max(float(cres.params[COMPOSITE_SOLO]), 0.0)})

    schemes = {
        "multivariable": _normalise({k: max(components[k]["coef"], 0.0) for k in shorts}),
        "univariate": _normalise({k: max(univariate_fit[k]["coef"], 0.0) for k in shorts}),
        "composite": scheme_comp,
    }

    # Factor representation, so sensitivity.py can perturb any scheme uniformly.
    scheme_fits = {
        "multivariable": [{"coef": components[k]["coef"],
                           "ci": [components[k]["ci_low"], components[k]["ci_high"]],
                           "loadings": {k: 1.0}} for k in shorts],
        "univariate": [{"coef": univariate_fit[k]["coef"], "ci": univariate_fit[k]["ci"],
                        "loadings": {k: 1.0}} for k in shorts],
        "composite": [
            {"coef": composite_fit["merged"]["coef"], "ci": composite_fit["merged"]["ci"],
             "loadings": {a: 0.5, b: 0.5}},
            {"coef": composite_fit[COMPOSITE_SOLO]["coef"],
             "ci": composite_fit[COMPOSITE_SOLO]["ci"], "loadings": {COMPOSITE_SOLO: 1.0}},
        ],
    }

    declared = {c: float(cfg["scoring"]["component_weights"][c]) for c in shorts}
    veto = _veto(cfg, declared, univariate_fit, components)

    report = {
        "role": "diagnostic — scoring weights come from config.yaml, not this file",
        "family": family, "dispersion": dispersion, "n_las": int(len(df)),
        "confidence_level": conf, "offset_source": offset_source,
        "intercept": float(res.params["const"]), "llf": float(res.llf), "aic": float(res.aic),
        "components": components,
        "univariate_fit": univariate_fit,
        "composite_fit": composite_fit,
        "declared_weights": declared,
        "veto": veto,
        "schemes": schemes,
        "scheme_fits": scheme_fits,
        "suicide_signal_weight": float(cfg["scoring"]["suicide_signal_weight"]),
    }

    out = cfg.path("weights")
    with open(out, "w") as fh:
        json.dump(report, fh, indent=2)
    _print_report(report, out)
    return report


def _print_report(r: dict, out) -> None:
    shorts = list(PROXY_COLS.keys())
    conf = int(r["confidence_level"] * 100)
    print("\n[calibrate] ===== LA-level check on the declared weights =====")
    print(f"  model: {r['family']} | LAs: {r['n_las']} | dispersion: {r['dispersion']:.2f} "
          f"| AIC: {r['aic']:.1f}")
    print(f"  offset: {r['offset_source']}")
    print(f"  {'component':<13} {'declared':>9} {'RR(uni)':>9} {f'{conf}% CI (uni)':>20} "
          f"{'RR(multi)':>10}")
    for k in shorts:
        u, c = r["univariate_fit"][k], r["components"][k]
        ci_s = f"[{np.exp(u['ci'][0]):.3f}, {np.exp(u['ci'][1]):.3f}]"
        print(f"  {k:<13} {r['declared_weights'][k]:>9.2f} {u['rate_ratio']:>9.3f} "
              f"{ci_s:>20} {c['rate_ratio']:>10.3f}")
    print(f"  suicide_signal carried separately at weight {r['suicide_signal_weight']:.2f}")

    v = r["veto"]
    banner = {"pass": "PASS — no declared weight is contradicted by the LA fit",
              "collinearity": "PASS (with collinearity notes)",
              "unsupported": "FLAGGED — a weighted proxy is not evidenced at LA level",
              "contradicted": "FLAGGED — the LA fit contradicts a declared weight"}[v["status"]]
    print(f"\n  VETO: {banner}")
    for f in v["findings"]:
        print(f"    [{f['severity']}] {f['message']}")
    if v["status"] in ("unsupported", "contradicted"):
        print("    -> Weights are a declared prior; this does not stop the run. Either")
        print("       justify the weight on non-LA evidence or lower it in config.yaml.")

    print(f"\n  comparison schemes {'deprivation':>12} {'occupation':>11} {'isolation':>10}")
    print(f"    {'declared':<15} {r['declared_weights']['deprivation']:>12.3f} "
          f"{r['declared_weights']['occupation']:>11.3f} {r['declared_weights']['isolation']:>10.3f}")
    for name, sc in r["schemes"].items():
        print(f"    {name:<15} {sc['deprivation']:>12.3f} {sc['occupation']:>11.3f} "
              f"{sc['isolation']:>10.3f}")
    print(f"  -> {out}  (diagnostic only; sensitivity.py tests whether it matters)\n")


if __name__ == "__main__":
    from .config import load_config
    run(load_config())
