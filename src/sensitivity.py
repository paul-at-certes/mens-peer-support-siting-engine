"""Sensitivity analysis — does the shortlist actually depend on our choices?

The scoring weights are a declared prior (docs/adr/0001-calibration-as-veto.md),
so the honest question is not "how tight are the confidence intervals" but "would
a different defensible configuration have given a different shortlist?". Three
axes, all scored off the SAME prepared component frame so only one thing varies:

1. **Named alternatives.** Rank under the declared weights, equal weights, and
   each scheme the LA fit supports (multivariable / univariate / composite).
   This is the headline: if the shortlist barely moves, the weighting argument
   is settled by evidence rather than assertion.

2. **CI envelope.** Draw component weights from the univariate fit's confidence
   intervals. The point weights stay declared; this asks how far the *data* would
   let them move. Univariate rather than partial coefficients for the same reason
   the veto uses them — under collinearity a partial coefficient does not answer
   "is this proxy associated with the outcome".

3. **Supply constants.** Sweep the travel/catchment split. The supply surface
   gates the shortlist harder than the need weights do — the great majority of
   the per-capita top 100 sits in the bottom decile of supply — so its two
   hand-set constants get the same scrutiny as any weight.

Axes 1 and 2 need the calibration diagnostic; axis 3 does not. Each axis is
skipped, not fatal, if its inputs are missing.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .config import Config
from .score import (PROXY_COMPONENTS, apply_weights, calibration_report,
                    declared_weights, prepare_components)


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


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 1.0


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx, ry = pd.Series(x).rank().to_numpy(), pd.Series(y).rank().to_numpy()
    return float(np.corrcoef(rx, ry)[0, 1])


def _topn(priority: np.ndarray, n: int) -> set:
    return set(np.argsort(-priority)[:n].tolist())


def _priority(df: pd.DataFrame, comp_w: dict, w_suicide: float) -> np.ndarray:
    _, priority, _, _, _ = apply_weights(df, comp_w, w_suicide)
    return np.asarray(priority)


def _supply_variants(cfg: Config):
    """(travel_weight, catchment_minutes, accessibility_df) for the axis-3 sweep.

    Catchment variants need the travel matrix re-derived; the provider caches it
    where that is expensive, and the haversine default recomputes in seconds.
    """
    sweep = (cfg.get("sensitivity", {}) or {}).get("supply_sweep", {}) or {}
    tws = [float(t) for t in sweep.get("travel_weight", [])]
    catchments = [float(c) for c in sweep.get("catchment_minutes", [])]
    if not tws and not catchments:
        return None

    from . import accessibility
    interim = cfg.path("interim")
    geo = pd.read_parquet(interim / "dim_geography.parquet")
    prov = pd.read_parquet(interim / "dim_provision.parquet")
    minutes = accessibility.travel_matrix(cfg, geo, prov)
    base_catchment = float(cfg["accessibility"]["catchment_minutes"])
    base_tw = float(cfg["accessibility"].get("travel_weight", 0.6))

    frames = {c: accessibility.summarise(minutes, geo, prov, c)
              for c in sorted(set(catchments) | {base_catchment})}
    for tw in sorted(set(tws) | {base_tw}):
        for c in sorted(set(catchments) | {base_catchment}):
            yield tw, c, frames[c]


def run(cfg: Config) -> dict:
    scfg = cfg.get("sensitivity", {}) or {}
    N = int(scfg.get("shortlist_n", 100))
    n_draws = int(scfg.get("n_draws", 200))
    thresholds = scfg.get("thresholds", {}) or {}
    rng = np.random.default_rng(int(scfg.get("seed", 7)))
    z = 1.959964  # 95% normal quantile

    comp_w, w_suicide = declared_weights(cfg)
    report = calibration_report(cfg)
    df = prepare_components(cfg)
    area_codes = df["area_code"].to_numpy()

    ref_priority = _priority(df, comp_w, w_suicide)
    ref_top = _topn(ref_priority, N)
    result: dict = {"shortlist_n": N, "declared_weights": comp_w,
                    "suicide_signal_weight": w_suicide}

    # --- Axis 1: named alternatives ---------------------------------------
    alternatives = {"equal": {c: 1 / len(PROXY_COMPONENTS) for c in PROXY_COMPONENTS}}
    if report:
        alternatives.update(report.get("schemes", {}))
    # A scheme that zeroes a proxy the LA fit finds positively and significantly
    # associated with the outcome is discarding evidence, not weighing it. Note
    # it — it is usually the outlier, and a reader should know why.
    evidenced = set()
    if report:
        for name, u in (report.get("univariate_fit") or {}).items():
            if u["ci"][0] > 0:
                evidenced.add(name)

    axis1 = {}
    for name, w in alternatives.items():
        pr = _priority(df, w, w_suicide)
        dropped = sorted(c for c in evidenced if w.get(c, 0.0) == 0.0)
        axis1[name] = {
            "weights": {k: round(float(v), 4) for k, v in w.items()},
            "topN_jaccard_vs_declared": round(_jaccard(_topn(pr, N), ref_top), 4),
            "spearman_vs_declared": round(_spearman(pr, ref_priority), 4),
            "discards_evidenced": dropped,
        }
    result["alternatives"] = axis1

    # --- Axis 2: CI envelope around the declared weights -------------------
    if report and "univariate" in report.get("scheme_fits", {}):
        factors = report["scheme_fits"]["univariate"]
        means = [f["coef"] for f in factors]
        sds = [max((f["ci"][1] - f["ci"][0]) / (2 * z), 0.0) for f in factors]
        ref_top_arr = np.array(sorted(ref_top))
        point_rank = pd.Series(-ref_priority).rank(method="min").to_numpy()
        jaccards, retention, shifts = [], np.zeros(len(ref_top_arr)), []
        for _ in range(n_draws):
            w = _weights_from_factors(factors, [rng.normal(m, s) for m, s in zip(means, sds)])
            pr = _priority(df, w, w_suicide)
            top = _topn(pr, N)
            jaccards.append(_jaccard(top, ref_top))
            retention += np.array([1.0 if i in top else 0.0 for i in ref_top_arr])
            dr = pd.Series(-pr).rank(method="min").to_numpy()
            shifts.append(np.abs(dr[ref_top_arr] - point_rank[ref_top_arr]))
        retention /= n_draws
        shifts = np.concatenate(shifts)
        robustness = {area_codes[i]: round(float(r), 3) for i, r in zip(ref_top_arr, retention)}
        low_conf = sorted([a for a, r in robustness.items() if r < 0.5])
        result["envelope"] = {
            "basis": "univariate LA-fit confidence intervals",
            "n_draws": n_draws,
            "mean_topN_jaccard": round(float(np.mean(jaccards)), 4),
            "min_topN_jaccard": round(float(np.min(jaccards)), 4),
            "median_rank_shift": round(float(np.median(shifts)), 1),
            "p90_rank_shift": round(float(np.percentile(shifts, 90)), 1),
            "mean_retention": round(float(retention.mean()), 4),
            "n_low_confidence": len(low_conf),
        }
        result["area_robustness"] = robustness
        result["low_confidence_areas"] = low_conf
    else:
        result["envelope"] = {"skipped": "no calibration diagnostic available"}

    # --- Axis 3: supply constants -----------------------------------------
    base_tw = float(cfg["accessibility"].get("travel_weight", 0.6))
    base_catchment = float(cfg["accessibility"]["catchment_minutes"])
    axis3 = {}
    variants = _supply_variants(cfg)
    if variants is None:
        result["supply"] = {"skipped": "no sensitivity.supply_sweep configured"}
    else:
        for tw, catchment, acc in variants:
            key = f"travel_weight={tw:g},catchment={catchment:g}"
            d2 = prepare_components(cfg, travel_weight=tw, accessibility_df=acc)
            pr = _priority(d2, comp_w, w_suicide)
            axis3[key] = {
                "is_shipped": bool(tw == base_tw and catchment == base_catchment),
                "topN_jaccard_vs_shipped": round(_jaccard(_topn(pr, N), ref_top), 4),
                "spearman_vs_shipped": round(_spearman(pr, ref_priority), 4),
            }
        result["supply"] = axis3

    result["stability"] = _stability(result, thresholds)
    out = cfg.path("sensitivity")
    with open(out, "w") as fh:
        json.dump(result, fh, indent=2)
    _print_report(result, out)
    return result


def _stability(result: dict, thresholds: dict) -> dict:
    """Worst observed overlap per axis vs its warn threshold. Never fatal —
    an allocation index that refuses to produce a shortlist is less useful than
    one that produces it with the caveat attached."""
    checks = {}

    alts = result.get("alternatives") or {}
    if alts:
        worst = min(alts.items(), key=lambda kv: kv[1]["topN_jaccard_vs_declared"])
        checks["schemes"] = {"worst_overlap": worst[1]["topN_jaccard_vs_declared"],
                             "worst_against": worst[0],
                             "threshold": float(thresholds.get("schemes_warn", 0.50))}
    env = result.get("envelope") or {}
    if "mean_topN_jaccard" in env:
        # Mean, not min: the minimum over N random draws is an extreme-value
        # statistic that necessarily worsens as n_draws grows, which would make
        # the verdict depend on how long we ran rather than on the data.
        checks["envelope"] = {"worst_overlap": env["mean_topN_jaccard"],
                              "worst_against": "mean over CI draws",
                              "threshold": float(thresholds.get("envelope_warn", 0.70))}
    sup = result.get("supply") or {}
    if sup and "skipped" not in sup:
        worst = min(sup.items(), key=lambda kv: kv[1]["topN_jaccard_vs_shipped"])
        checks["supply"] = {"worst_overlap": worst[1]["topN_jaccard_vs_shipped"],
                            "worst_against": worst[0],
                            "threshold": float(thresholds.get("supply_warn", 0.50))}

    for c in checks.values():
        c["passes"] = bool(c["worst_overlap"] >= c["threshold"])
    unstable = [k for k, c in checks.items() if not c["passes"]]
    return {"checks": checks, "unstable_axes": unstable,
            "status": "unstable" if unstable else "stable"}


def _print_report(r: dict, out) -> None:
    N = r["shortlist_n"]
    print("\n[sensitivity] ===== does the shortlist depend on our choices? =====")
    dw = r["declared_weights"]
    print(f"  reference: declared weights dep/occ/iso = "
          f"{dw['deprivation']:.2f}/{dw['occupation']:.2f}/{dw['isolation']:.2f}, "
          f"shortlist top-{N}")

    print(f"\n  1) named alternatives  {'top-N overlap':>14} {'Spearman':>9}  weights(dep/occ/iso)")
    for name, a in r["alternatives"].items():
        w = a["weights"]
        note = (f"  [discards {', '.join(a['discards_evidenced'])}]"
                if a.get("discards_evidenced") else "")
        print(f"     {name:<18} {a['topN_jaccard_vs_declared']:>14.3f} "
              f"{a['spearman_vs_declared']:>9.3f}  "
              f"{w['deprivation']:.2f}/{w['occupation']:.2f}/{w['isolation']:.2f}{note}")

    env = r["envelope"]
    if "skipped" in env:
        print(f"\n  2) CI envelope: skipped — {env['skipped']}")
    else:
        print(f"\n  2) CI envelope ({env['basis']}, {env['n_draws']} draws)")
        print(f"     mean top-N overlap {env['mean_topN_jaccard']:.3f} "
              f"(min {env['min_topN_jaccard']:.3f}), median rank shift "
              f"{env['median_rank_shift']:.0f}, p90 {env['p90_rank_shift']:.0f}")
        print(f"     shortlist retention {env['mean_retention']:.1%}; "
              f"{env['n_low_confidence']} area(s) below 50% retention")

    sup = r["supply"]
    if "skipped" in sup:
        print(f"\n  3) supply constants: skipped — {sup['skipped']}")
    else:
        print(f"\n  3) supply constants   {'top-N overlap':>14} {'Spearman':>9}")
        for key, v in sorted(sup.items(), key=lambda kv: kv[1]["topN_jaccard_vs_shipped"]):
            mark = "  <- shipped" if v["is_shipped"] else ""
            print(f"     {key:<34} {v['topN_jaccard_vs_shipped']:>8.3f} "
                  f"{v['spearman_vs_shipped']:>9.3f}{mark}")

    st = r["stability"]
    print(f"\n  STABILITY: {st['status'].upper()}")
    for name, c in st["checks"].items():
        flag = "ok " if c["passes"] else "WARN"
        print(f"    [{flag}] {name:<9} worst overlap {c['worst_overlap']:.3f} "
              f"vs threshold {c['threshold']:.2f}  (worst: {c['worst_against']})")
    if st["unstable_axes"]:
        print("    -> The shortlist is sensitive to a choice we made rather than to the")
        print("       data. Treat it as a starting point for local judgement, not a ranking.")
    print(f"  -> {out}\n")


if __name__ == "__main__":
    from .config import load_config
    run(load_config())
