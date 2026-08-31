"""Sensitivity analysis — does the shortlist actually depend on the weighting?

design.md §7, validation check #2: "Perturb the weights within their confidence
intervals and re-rank. A robust shortlist shouldn't reshuffle wildly; areas that
only rank highly under a narrow weight choice are flagged as low-confidence."

Two complementary checks, both scored off the SAME prepared component frame so
only the weights vary:

1. **Scheme comparison.** Rank under each learned scheme (multivariable /
   univariate / composite) and measure how much the top-N shortlist and the
   overall ordering move relative to the active scheme. If they barely move, the
   deprivation-collinearity worry is cosmetic and we can pick the most
   interpretable scheme with confidence.

2. **CI perturbation.** Draw the active scheme's coefficients from their
   confidence intervals many times, re-rank each draw, and measure shortlist
   stability + per-area retention. Areas that fall out of the shortlist under
   small weight wobble are flagged low-confidence.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .config import Config
from .score import PROXY_COMPONENTS, apply_weights, prepare_components, _load_weights


def _weights_from_factors(factors: list[dict], coefs) -> dict:
    """Map factor coefficients -> normalised component weights via the loadings.
    Positive part only (a protective/zero factor contributes nothing)."""
    comp = {c: 0.0 for c in PROXY_COMPONENTS}
    for f, coef in zip(factors, coefs):
        w = max(float(coef), 0.0)
        for c, load in f["loadings"].items():
            comp[c] += w * load
    tot = sum(comp.values()) or 1.0
    return {c: comp[c] / tot for c in PROXY_COMPONENTS}


def _priority(df: pd.DataFrame, comp_w: dict, w_suicide: float) -> np.ndarray:
    _, priority, _, _, _ = apply_weights(df, comp_w, w_suicide)
    return np.asarray(priority)


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 1.0


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    return float(np.corrcoef(rx, ry)[0, 1])


def _topn_set(priority: np.ndarray, n: int) -> set:
    return set(np.argsort(-priority)[:n].tolist())


def run(cfg: Config) -> dict:
    w = _load_weights(cfg)
    scfg = cfg.get("sensitivity", {}) or {}
    N = int(scfg.get("shortlist_n", 100))
    n_draws = int(scfg.get("n_draws", 200))
    rng = np.random.default_rng(int(scfg.get("seed", 7)))
    z = 1.959964  # 95% normal quantile

    df = prepare_components(cfg)
    w_suicide = float(w["suicide_signal_weight"])
    active = w["active_scheme"]
    fits = w["scheme_fits"]

    # Active reference ranking.
    active_w = _weights_from_factors(fits[active], [f["coef"] for f in fits[active]])
    active_priority = _priority(df, active_w, w_suicide)
    active_top = _topn_set(active_priority, N)
    area_codes = df["area_code"].to_numpy()

    # 1) Scheme comparison -------------------------------------------------
    scheme_comparison = {}
    for name, factors in fits.items():
        comp_w = _weights_from_factors(factors, [f["coef"] for f in factors])
        pr = _priority(df, comp_w, w_suicide)
        top = _topn_set(pr, N)
        scheme_comparison[name] = {
            "weights": {k: round(v, 4) for k, v in comp_w.items()},
            "topN_jaccard_vs_active": round(_jaccard(top, active_top), 4),
            "spearman_vs_active": round(_spearman(pr, active_priority), 4),
        }

    # 2) CI perturbation of the active scheme ------------------------------
    factors = fits[active]
    sds = [max((f["ci"][1] - f["ci"][0]) / (2 * z), 0.0) for f in factors]
    means = [f["coef"] for f in factors]
    active_top_arr = np.array(sorted(active_top))
    # rank of every area under the point ranking (1 = highest priority)
    point_rank = pd.Series(-active_priority).rank(method="min").to_numpy()

    jaccards = []
    retention = np.zeros(len(active_top_arr))      # times each shortlist area stays in top-N
    rank_shifts = []                               # |rank change| for shortlist areas
    for _ in range(n_draws):
        draw = [rng.normal(m, s) for m, s in zip(means, sds)]
        comp_w = _weights_from_factors(factors, draw)
        pr = _priority(df, comp_w, w_suicide)
        top = _topn_set(pr, N)
        jaccards.append(_jaccard(top, active_top))
        retention += np.array([1.0 if i in top else 0.0 for i in active_top_arr])
        dr = pd.Series(-pr).rank(method="min").to_numpy()
        rank_shifts.append(np.abs(dr[active_top_arr] - point_rank[active_top_arr]))

    retention /= n_draws
    rank_shifts = np.concatenate(rank_shifts)
    area_robustness = {area_codes[i]: round(float(r), 3)
                       for i, r in zip(active_top_arr, retention)}
    low_conf = sorted([a for a, r in area_robustness.items() if r < 0.5])

    result = {
        "active_scheme": active,
        "shortlist_n": N,
        "n_draws": n_draws,
        "scheme_comparison": scheme_comparison,
        "perturbation": {
            "mean_topN_jaccard": round(float(np.mean(jaccards)), 4),
            "min_topN_jaccard": round(float(np.min(jaccards)), 4),
            "median_rank_shift": round(float(np.median(rank_shifts)), 1),
            "p90_rank_shift": round(float(np.percentile(rank_shifts, 90)), 1),
            "mean_retention": round(float(retention.mean()), 4),
            "n_low_confidence": len(low_conf),
        },
        "area_robustness": area_robustness,
        "low_confidence_areas": low_conf,
    }
    out = cfg.path("sensitivity")
    with open(out, "w") as fh:
        json.dump(result, fh, indent=2)

    # --- Console report ----------------------------------------------------
    print("\n[sensitivity] ===== weighting robustness =====")
    print(f"  active scheme: {active} | shortlist top-{N} | {n_draws} CI draws")
    print(f"  {'scheme':<15} {'top-N overlap':>14} {'Spearman':>10}  weights(dep/occ/iso)")
    for name, cmp in scheme_comparison.items():
        wv = cmp["weights"]
        mark = " *" if name == active else "  "
        print(f"  {name:<15} {cmp['topN_jaccard_vs_active']:>14.3f} "
              f"{cmp['spearman_vs_active']:>10.3f}  "
              f"{wv['deprivation']:.2f}/{wv['occupation']:.2f}/{wv['isolation']:.2f}{mark}")
    p = result["perturbation"]
    print(f"  CI perturbation: mean top-N overlap {p['mean_topN_jaccard']:.3f} "
          f"(min {p['min_topN_jaccard']:.3f}), median rank shift {p['median_rank_shift']:.0f}, "
          f"p90 {p['p90_rank_shift']:.0f}")
    print(f"  shortlist retention {p['mean_retention']:.1%}; "
          f"{p['n_low_confidence']} area(s) flagged low-confidence (<50% retention)")
    print(f"  -> {out}\n")
    return result


if __name__ == "__main__":
    from .config import load_config
    run(load_config())
