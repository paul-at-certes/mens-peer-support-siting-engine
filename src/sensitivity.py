"""Sensitivity analysis — does the shortlist actually depend on our choices?

The scoring weights are a declared prior (docs/adr/0001-calibration-as-veto.md),
so the honest question is not "how tight are the confidence intervals" but "would
a different defensible configuration have given a different shortlist?". Three
axes, all scored off the SAME prepared component frame so only one thing varies:

1. **Named alternatives.** Rank under the declared weights, equal weights, and
   each scheme the LA fit supports (multivariable / univariate / composite).

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

**What the verdict measures.** Set membership is the wrong test and was the wrong
test here. An area at rank 101 versus rank 99 flips shortlist membership on a
rounding error while changing no decision, and Jaccard on two equal-sized sets
understates agreement badly (63 of 100 shared reads as 0.46). So the verdict is
driven by DISPLACEMENT: of the areas you would actually act on — the top
`decision_n` — how many stay inside `contention_band` under every alternative
configuration? Set overlap is still reported, as a share rather than a Jaccard,
but it does not gate.

**Tiers.** Because the ordering is less certain than the membership, the output
is banded rather than ranked (`fact_tier.parquet`): an area is in the shortlist
tier if it stays inside the top `shortlist_n` under EVERY configuration tested,
in contention if it reaches that under SOME configuration, and outside otherwise.
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


def _set_metrics(top: set, ref_top: set) -> dict:
    """Shared count, overlap SHARE, and Jaccard. The share is what plain language
    means by 'the shortlist barely moved'; Jaccard is kept because it is what
    earlier runs reported, and the two are easy to confuse."""
    shared = len(top & ref_top)
    n = len(ref_top) or 1
    return {"shared": shared, "of": len(ref_top),
            "overlap": round(shared / n, 4),
            "jaccard": round(_jaccard(top, ref_top), 4)}


def _displacement(ranks: np.ndarray, decision_idx: np.ndarray, band: int) -> dict:
    """Where the reference decision set lands under an alternative ranking."""
    r = ranks[decision_idx]
    return {"held": round(float((r <= band).mean()), 4),
            "median_rank": int(np.median(r)),
            "worst_rank": int(r.max())}


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx, ry = pd.Series(x).rank().to_numpy(), pd.Series(y).rank().to_numpy()
    return float(np.corrcoef(rx, ry)[0, 1])


def _topn(priority: np.ndarray, n: int) -> set:
    return set(np.argsort(-priority)[:n].tolist())


def _ranks(priority: np.ndarray) -> np.ndarray:
    """1 = highest priority."""
    return pd.Series(-priority).rank(method="min").to_numpy()


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
    D = int(scfg.get("decision_n", 20))
    band = int(scfg.get("contention_band", N))
    n_draws = int(scfg.get("n_draws", 200))
    thresholds = scfg.get("thresholds", {}) or {}
    rng = np.random.default_rng(int(scfg.get("seed", 7)))
    z = 1.959964  # 95% normal quantile

    comp_w, w_suicide = declared_weights(cfg)
    report = calibration_report(cfg)
    df = prepare_components(cfg)
    area_codes = df["area_code"].to_numpy()

    ref = _priority(df, comp_w, w_suicide)
    ref_rank = _ranks(ref)
    ref_top = _topn(ref, N)
    decision_idx = np.argsort(-ref)[:D]      # the areas you would actually act on

    # Ranks under every DEFENSIBLE configuration, for the tiering below. Random
    # CI draws are deliberately excluded: they are a perturbation, not a
    # configuration anyone would choose to ship.
    rank_stack = [ref_rank]

    result: dict = {"shortlist_n": N, "decision_n": D, "contention_band": band,
                    "declared_weights": comp_w, "suicide_signal_weight": w_suicide}

    # --- Axis 1: named alternatives ---------------------------------------
    alternatives = {"equal": {c: 1 / len(PROXY_COMPONENTS) for c in PROXY_COMPONENTS}}
    if report:
        alternatives.update(report.get("schemes", {}))

    # A scheme that zeroes a proxy the LA fit finds positively and significantly
    # associated with the outcome is discarding evidence, not weighing it.
    evidenced = set()
    if report:
        for name, u in (report.get("univariate_fit") or {}).items():
            if u["ci"][0] > 0:
                evidenced.add(name)

    axis1 = {}
    for name, w in alternatives.items():
        pr = _priority(df, w, w_suicide)
        rk = _ranks(pr)
        rank_stack.append(rk)
        axis1[name] = {
            "weights": {k: round(float(v), 4) for k, v in w.items()},
            **_set_metrics(_topn(pr, N), ref_top),
            "spearman": round(_spearman(pr, ref), 4),
            "displacement": _displacement(rk, decision_idx, band),
            "discards_evidenced": sorted(c for c in evidenced if w.get(c, 0.0) == 0.0),
        }
    result["alternatives"] = axis1

    # --- Axis 2: CI envelope ----------------------------------------------
    if report and "univariate" in report.get("scheme_fits", {}):
        factors = report["scheme_fits"]["univariate"]
        means = [f["coef"] for f in factors]
        sds = [max((f["ci"][1] - f["ci"][0]) / (2 * z), 0.0) for f in factors]
        ref_top_arr = np.array(sorted(ref_top))
        overlaps, helds, retention, shifts = [], [], np.zeros(len(ref_top_arr)), []
        for _ in range(n_draws):
            w = _weights_from_factors(factors, [rng.normal(m, s) for m, s in zip(means, sds)])
            pr = _priority(df, w, w_suicide)
            rk = _ranks(pr)
            top = _topn(pr, N)
            overlaps.append(len(top & ref_top) / N)
            helds.append(float((rk[decision_idx] <= band).mean()))
            retention += np.array([1.0 if i in top else 0.0 for i in ref_top_arr])
            shifts.append(np.abs(rk[ref_top_arr] - ref_rank[ref_top_arr]))
        retention /= n_draws
        shifts = np.concatenate(shifts)
        robustness = {area_codes[i]: round(float(r), 3) for i, r in zip(ref_top_arr, retention)}
        result["envelope"] = {
            "basis": "univariate LA-fit confidence intervals",
            "n_draws": n_draws,
            "mean_overlap": round(float(np.mean(overlaps)), 4),
            "min_overlap": round(float(np.min(overlaps)), 4),
            # Mean, not min: the minimum over N random draws is an extreme-value
            # statistic that necessarily worsens as n_draws grows, which would
            # make the verdict depend on how long we ran rather than on the data.
            "mean_held": round(float(np.mean(helds)), 4),
            "min_held": round(float(np.min(helds)), 4),
            "median_rank_shift": round(float(np.median(shifts)), 1),
            "p90_rank_shift": round(float(np.percentile(shifts, 90)), 1),
            "mean_retention": round(float(retention.mean()), 4),
            "n_low_confidence": int(sum(1 for r in robustness.values() if r < 0.5)),
        }
        result["area_robustness"] = robustness
        result["low_confidence_areas"] = sorted(a for a, r in robustness.items() if r < 0.5)
    else:
        result["envelope"] = {"skipped": "no calibration diagnostic available"}

    # --- Axis 3: supply constants -----------------------------------------
    base_tw = float(cfg["accessibility"].get("travel_weight", 0.6))
    base_catchment = float(cfg["accessibility"]["catchment_minutes"])
    variants = _supply_variants(cfg)
    if variants is None:
        result["supply"] = {"skipped": "no sensitivity.supply_sweep configured"}
    else:
        axis3 = {}
        for tw, catchment, acc in variants:
            d2 = prepare_components(cfg, travel_weight=tw, accessibility_df=acc)
            pr = _priority(d2, comp_w, w_suicide)
            rk = _ranks(pr)
            rank_stack.append(rk)
            axis3[f"travel_weight={tw:g},catchment={catchment:g}"] = {
                "is_shipped": bool(tw == base_tw and catchment == base_catchment),
                **_set_metrics(_topn(pr, N), ref_top),
                "spearman": round(_spearman(pr, ref), 4),
                "displacement": _displacement(rk, decision_idx, band),
            }
        result["supply"] = axis3

    # --- Tiers -------------------------------------------------------------
    R = np.vstack(rank_stack)
    best, worst = R.min(axis=0).astype(int), R.max(axis=0).astype(int)
    tier = np.where(worst <= N, "shortlist",
                    np.where(best <= N, "contention", "outside"))
    tiers = pd.DataFrame({"area_code": area_codes, "rank_declared": ref_rank.astype(int),
                          "rank_best": best, "rank_worst": worst, "tier": tier})
    tiers = tiers.sort_values(["rank_declared"]).reset_index(drop=True)
    tier_path = cfg.path("fact_tier")
    tiers.to_parquet(tier_path, index=False)
    result["tiers"] = {
        "n_configurations": len(rank_stack),
        "definition": (f"shortlist = inside the top {N} under EVERY configuration tested; "
                       f"contention = inside the top {N} under SOME configuration"),
        "counts": {k: int(v) for k, v in pd.Series(tier).value_counts().items()},
        "path": str(tier_path),
    }

    result["stability"] = _stability(result, thresholds)
    out = cfg.path("sensitivity")
    with open(out, "w") as fh:
        json.dump(result, fh, indent=2)
    _print_report(result, out)
    return result


def _stability(result: dict, thresholds: dict) -> dict:
    """Worst observed displacement per axis vs its threshold.

    DISPLACEMENT drives the verdict, not set membership: the question is whether
    the areas we would act on stay in contention, not whether an arbitrary
    top-N boundary happens to enclose the same members. Never fatal — an
    allocation index that refuses to produce a shortlist is less useful than one
    that produces it with the caveat attached.
    """
    held_t = float(thresholds.get("displacement_warn", 0.90))
    overlap_t = float(thresholds.get("overlap_warn", 0.70))
    checks = {}

    def _worst(items):
        # Lowest share held; where everything holds equally (the common case once
        # the measure is right), the configuration that displaced an area furthest.
        return min(items, key=lambda kv: (kv[1]["displacement"]["held"],
                                          -kv[1]["displacement"]["worst_rank"]))

    alts = result.get("alternatives") or {}
    if alts:
        name, v = _worst(alts.items())
        checks["schemes"] = {"worst_held": v["displacement"]["held"],
                             "worst_rank": v["displacement"]["worst_rank"],
                             "worst_overlap": min(a["overlap"] for a in alts.values()),
                             "worst_against": name}
    env = result.get("envelope") or {}
    if "mean_held" in env:
        checks["envelope"] = {"worst_held": env["mean_held"],
                              "worst_rank": None,
                              "worst_overlap": env["mean_overlap"],
                              "worst_against": "mean over CI draws"}
    sup = result.get("supply") or {}
    if sup and "skipped" not in sup:
        name, v = _worst(sup.items())
        checks["supply"] = {"worst_held": v["displacement"]["held"],
                            "worst_rank": v["displacement"]["worst_rank"],
                            "worst_overlap": min(a["overlap"] for a in sup.values()),
                            "worst_against": name}

    for c in checks.values():
        c["threshold_held"] = held_t
        c["threshold_overlap"] = overlap_t
        c["passes"] = bool(c["worst_held"] >= held_t)          # displacement gates
        c["overlap_note"] = bool(c["worst_overlap"] >= overlap_t)   # reported only

    unstable = [k for k, c in checks.items() if not c["passes"]]
    return {"checks": checks, "unstable_axes": unstable,
            "measure": "share of the decision set still inside the contention band",
            "status": "unstable" if unstable else "stable"}


def _print_report(r: dict, out) -> None:
    N, D, band = r["shortlist_n"], r["decision_n"], r["contention_band"]
    dw = r["declared_weights"]
    print("\n[sensitivity] ===== does the shortlist depend on our choices? =====")
    print(f"  reference: declared weights dep/occ/iso = "
          f"{dw['deprivation']:.2f}/{dw['occupation']:.2f}/{dw['isolation']:.2f}")
    print(f"  verdict measures DISPLACEMENT: of the top-{D} you would act on, how many "
          f"stay inside the top {band}?")
    print(f"  set overlap is reported as a SHARE of the top-{N} (Jaccard in brackets, "
          f"since the two are easy to confuse).")

    def _row(label, v, extra=""):
        d = v["displacement"]
        print(f"     {label:<34} {d['held']:>7.0%} {d['median_rank']:>7} {d['worst_rank']:>7}"
              f"   {v['shared']:>3}/{v['of']:<3} {v['overlap']:>5.0%} "
              f"({v['jaccard']:.2f}) {v['spearman']:>6.3f}{extra}")

    hdr = (f"     {'':<34} {'held':>7} {'med rk':>7} {'worst':>7}   "
           f"{'shared':>7} {'ovlp':>5} {'(jac)':>6} {'rho':>6}")
    print(f"\n  1) named alternatives"); print(hdr)
    for name, a in r["alternatives"].items():
        note = ("  [discards " + ", ".join(a["discards_evidenced"]) + "]"
                if a.get("discards_evidenced") else "")
        _row(name, a, note)

    env = r["envelope"]
    if "skipped" in env:
        print(f"\n  2) CI envelope: skipped — {env['skipped']}")
    else:
        print(f"\n  2) CI envelope ({env['basis']}, {env['n_draws']} draws)")
        print(f"     decision set held: mean {env['mean_held']:.0%} (min {env['min_held']:.0%}) "
              f"| overlap mean {env['mean_overlap']:.0%}")
        print(f"     retention {env['mean_retention']:.0%}; {env['n_low_confidence']} area(s) "
              f"below 50% | median rank shift {env['median_rank_shift']:.0f}, "
              f"p90 {env['p90_rank_shift']:.0f}")

    sup = r["supply"]
    if "skipped" in sup:
        print(f"\n  3) supply constants: skipped — {sup['skipped']}")
    else:
        print(f"\n  3) supply constants"); print(hdr)
        for key, v in sorted(sup.items(), key=lambda kv: kv[1]["displacement"]["held"]):
            _row(key, v, "  <- shipped" if v["is_shipped"] else "")

    t = r["tiers"]
    counts = t["counts"]
    print(f"\n  TIERS across {t['n_configurations']} configurations -> {t['path']}")
    print(f"     shortlist  {counts.get('shortlist', 0):>6}  (top {N} under EVERY configuration)")
    print(f"     contention {counts.get('contention', 0):>6}  (top {N} under SOME configuration)")
    print(f"     outside    {counts.get('outside', 0):>6}")

    st = r["stability"]
    print(f"\n  STABILITY: {st['status'].upper()}  ({st['measure']})")
    for name, c in st["checks"].items():
        flag = "ok " if c["passes"] else "WARN"
        rk = f", worst rank {c['worst_rank']}" if c["worst_rank"] else ""
        print(f"    [{flag}] {name:<9} {c['worst_held']:>6.0%} held vs {c['threshold_held']:.0%} "
              f"threshold{rk}  (worst: {c['worst_against']}; overlap {c['worst_overlap']:.0%})")
    if st["unstable_axes"]:
        print("    -> Areas we would act on drop out of contention under an alternative")
        print("       configuration. Read the shortlist as a starting point, not a ranking.")
    print(f"  -> {out}\n")


if __name__ == "__main__":
    from .config import load_config
    run(load_config())
