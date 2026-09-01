"""Does the occupation factor earn its weight, or is it deprivation in a hat?

Deprivation and occupation overlap heavily: poor areas and manual-work areas are
largely the same areas. If they overlap completely, then weighting them 0.40 and
0.35 quietly makes deprivation worth 0.75 and the ranking counts one fact twice.

This regresses each proxy on deprivation and keeps the RESIDUAL — the part
deprivation did not already predict. Two numbers matter: how much is left over
(does the factor carry independent information at all), and where the leftovers
are (the places the factor is actually adding something).

    DIAGNOSTIC ONLY. Nothing here reaches need_index, priority_score, the tiers
    or the sensitivity draws. Scoring the residual would make a fitted quantity
    the input to a declared-prior architecture, which is exactly what
    docs/adr/0001-calibration-as-veto.md rules out, and it would residualise
    occupation against deprivation while leaving isolation raw. See
    docs/occupational-risk-layer-spec.md 6.1.

Isolation is measured the same way, because singling out occupation would be an
arbitrary choice about which factor has to justify itself.

Run:  python -m src.occupation_diagnostic
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .config import Config, load_config
from .score import _within_nation_pct

# A factor whose independent share falls below this is mostly restating
# deprivation, and its declared weight should be revisited. Set from what the
# number means: below a third, two thirds of what the factor contributes is
# already in the ranking under another name.
INDEPENDENT_SHARE_FLOOR = 0.33

FACTORS = ["occupation", "isolation"]


def _fit(y: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, float]:
    """Least-squares fit of y on x. Returns (residuals, R^2)."""
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (slope * x + intercept)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((resid ** 2).sum()) / ss_tot if ss_tot else 0.0
    return resid, r2


def prepare(cfg: Config) -> pd.DataFrame:
    """Geography + the three proxies, as within-nation percentiles (the scale
    score.py actually weights, so the diagnostic answers the real question)."""
    interim = cfg.path("interim")
    df = pd.read_parquet(interim / "dim_geography.parquet")
    for name, cols in [("deprivation", ["deprivation_proxy"]),
                       ("occupation", ["occupation_proxy"]),
                       ("isolation", ["isolation_proxy"])]:
        f = pd.read_parquet(interim / f"fact_{name}.parquet")
        df = df.merge(f[["area_code", *cols]], on="area_code")
    for c in ["deprivation", "occupation", "isolation"]:
        df[f"pct_{c}"] = _within_nation_pct(df, f"{c}_proxy")
    return df.dropna(subset=[f"pct_{c}" for c in ["deprivation", "occupation", "isolation"]])


def run(cfg: Config | None = None) -> dict:
    cfg = cfg or load_config()
    df = prepare(cfg)
    dep = df["pct_deprivation"].to_numpy()

    report: dict = {
        "basis": "within-nation percentiles, the scale score.py weights",
        "n_areas": int(len(df)),
        "independent_share_floor": INDEPENDENT_SHARE_FLOOR,
        "factors": {},
        "by_nation": {},
        "diagnostic_only": ("never enters need_index, priority_score, the tiers "
                            "or the sensitivity draws"),
    }

    print("\n[occupation-diagnostic] ===== does each factor earn its weight? =====")
    print(f"  basis: within-nation percentiles, {len(df):,} areas")
    print(f"  {'factor':<12} {'overlap r':>10} {'explained':>10} {'independent':>12}  verdict")
    for f in FACTORS:
        y = df[f"pct_{f}"].to_numpy()
        resid, r2 = _fit(y, dep)
        df[f"resid_{f}"] = resid
        independent = 1.0 - r2
        ok = independent >= INDEPENDENT_SHARE_FLOOR
        report["factors"][f] = {
            "pearson_with_deprivation": round(float(np.corrcoef(y, dep)[0, 1]), 4),
            "spearman_with_deprivation": round(float(
                pd.Series(y).corr(pd.Series(dep), method="spearman")), 4),
            "variance_explained_by_deprivation": round(float(r2), 4),
            "independent_share": round(float(independent), 4),
            # Sanity: by construction the residual is uncorrelated with what it
            # was regressed on. If this is not ~0 the fit is wrong.
            "residual_corr_with_deprivation": round(float(np.corrcoef(resid, dep)[0, 1]), 6),
            "earns_its_weight": bool(ok),
        }
        print(f"  {f:<12} {report['factors'][f]['pearson_with_deprivation']:>10.3f} "
              f"{r2:>9.1%} {independent:>12.1%}  "
              f"{'carries independent information' if ok else 'MOSTLY RESTATES DEPRIVATION'}")

    for nation, g in df.groupby("nation"):
        d = g["pct_deprivation"].to_numpy()
        report["by_nation"][str(nation)] = {
            "n": int(len(g)),
            **{f: round(float(1.0 - _fit(g[f"pct_{f}"].to_numpy(), d)[1]), 4)
               for f in FACTORS},
        }

    # --- where occupation is actually adding something ---------------------
    def _rows(g: pd.DataFrame) -> list[dict]:
        return [{"area_code": r.area_code, "area_name": r.area_name,
                 "la_name": r.la_name, "nation": r.nation,
                 "pct_occupation": round(float(r.pct_occupation), 3),
                 "pct_deprivation": round(float(r.pct_deprivation), 3),
                 "residual": round(float(r.resid_occupation), 3)}
                for r in g.itertuples()]

    high = df.nlargest(10, "resid_occupation")
    low = df.nsmallest(10, "resid_occupation")
    report["divergence"] = {
        "occupation_high_for_their_deprivation": _rows(high),
        "occupation_low_for_their_deprivation": _rows(low),
    }
    print("\n  Where occupation says MORE than deprivation does "
          "(the layer's reason to exist):")
    for r in high.head(5).itertuples():
        print(f"    +{r.resid_occupation:.2f}  {r.area_name[:26]:<26} {r.la_name[:20]:<20} "
              f"occ {r.pct_occupation:.2f} vs dep {r.pct_deprivation:.2f}")
    print("\n  Where it says LESS (deprived, but not on manual-work risk):")
    for r in low.head(5).itertuples():
        print(f"    {r.resid_occupation:.2f}  {r.area_name[:26]:<26} {r.la_name[:20]:<20} "
              f"occ {r.pct_occupation:.2f} vs dep {r.pct_deprivation:.2f}")

    # --- can that independent information actually reach the shortlist? ----
    # The point of the factor is the areas deprivation misses. If those areas
    # rank nowhere, the factor carries information the ranking cannot use, and
    # the map has to say so.
    score_path = cfg.path("fact_score")
    if score_path.exists():
        sc = pd.read_parquet(score_path)[["area_code", "rank", "rank_reach"]]
        m = high.merge(sc, on="area_code")
        if len(m):
            ex = m.nsmallest(1, "rank").iloc[0]
            report["outvoted"] = {
                "n_areas": int(len(df)),
                # How many areas these figures actually describe. The copy reads
                # it and says so: the median and best rank below are a fact
                # about these few, not about the class of outvoted places. The
                # claim about the class is the blind-spot flag's, which tests
                # every area.
                "n_examined": int(len(m)),
                "median_rank": int(m["rank"].median()),
                "best_rank": int(m["rank"].min()),
                # Strongest-first, deduped — not alphabetical, so the copy names
                # the areas that actually make the point.
                "example_las": list(dict.fromkeys(
                    str(r.la_name) for r in m.sort_values(
                        "resid_occupation", ascending=False).itertuples()))[:3],
                "example_occupation_pct": round(float(ex.pct_occupation), 2),
                "example_deprivation_pct": round(float(ex.pct_deprivation), 2),
            }
            print(f"\n  Those areas' standing in the ranking itself: "
                  f"median rank {int(m['rank'].median()):,} of {len(df):,}, "
                  f"best {int(m['rank'].min()):,}")
            print("  -> the factor carries information the weighting outvotes.")

    out = cfg.path("occupation_diagnostic")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\n  -> {out}  (diagnostic only; it changes no rank)")
    return report


if __name__ == "__main__":
    run()
