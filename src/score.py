"""score.py — need_index, supply_index, priority_score + factor_breakdown.

Applies the DECLARED component weights (config.yaml `scoring.component_weights`)
to the small-area proxies, each expressed as a within-nation percentile, nets
off the supply surface, and persists a fully decomposed score so nothing in the
ranking is unexplained.

The weights are a stated prior, not a regression output: the LA-level fit in
calibrate.py checks them (and can veto one) but does not supply them. See
docs/adr/0001-calibration-as-veto.md. Because both the weights and the
components live on a common 0..1 percentile scale, there is no unit transfer
from the LA fit to get wrong.

    need_index     = Σ wᵢ·zᵢ            # zᵢ = within-nation percentile of comp i
    supply_index   = normalise( f(travel_minutes, groups_within_catchment) )
    priority_score = need_index × (1 − supply_index)

Two views are produced side by side:
    * per-capita : priority_score                          (acute pockets)
    * reach      : priority_score × male_working_age_pop   (most men reached)

All normalisation is WITHIN-NATION — IMD/WIMD/SIMD and the censuses are not
comparable across borders, so the UK view is stitched percentiles, never a raw
cross-border scale.

One column, ``no_car_share``, is carried purely as CONTEXT. It is attached after
the score is computed, deliberately outside ``prepare_components``, so it cannot
reach need_index, supply_index, priority_score, the factor breakdown, the tiers
or the sensitivity analysis. It says where the car-only travel time overstates
access; it does not change the ranking. See src/ingest/car_access.py.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .config import Config

# Components that make up the need index. suicide_signal is carried separately
# at a low, fixed weight (it does not come from the regression coefficient).
PROXY_COMPONENTS = ["deprivation", "occupation", "isolation"]


def _within_nation_pct(df: pd.DataFrame, col: str) -> pd.Series:
    """Percentile rank (0..1) of ``col`` computed separately within each nation."""
    return df.groupby("nation")[col].rank(pct=True)


def declared_weights(cfg: Config) -> tuple[dict, float]:
    """The declared component weights + suicide-signal weight, from config.

    This is the ONLY source of scoring weights. calibrate.py writes a diagnostic
    report to weights.json, but scoring never reads it — so the pipeline scores
    with or without an outcome dataset (which matters: the outcome needs a live
    fetch and covers England and Wales only).
    """
    sc = cfg["scoring"]
    comp = {c: float(sc["component_weights"][c]) for c in PROXY_COMPONENTS}
    return comp, float(sc["suicide_signal_weight"])


def calibration_report(cfg: Config) -> dict | None:
    """The calibration diagnostic, if calibrate.py has run. Never required."""
    path = cfg.path("weights")
    if not path.exists():
        return None
    with open(path) as fh:
        return json.load(fh)


def prepare_components(cfg: Config, travel_weight: float | None = None,
                       accessibility_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Merge proxies, map the suicide signal down, and compute the within-nation
    percentiles + supply_index. Weight-agnostic, so multiple weighting schemes
    can be scored off the SAME prepared frame (used by score.py and
    sensitivity.py)."""
    interim = cfg.path("interim")
    geo = pd.read_parquet(interim / "dim_geography.parquet")
    pop = pd.read_parquet(interim / "dim_population.parquet")
    dep = pd.read_parquet(interim / "fact_deprivation.parquet")
    occ = pd.read_parquet(interim / "fact_occupation.parquet")
    iso = pd.read_parquet(interim / "fact_isolation.parquet")
    suicide = pd.read_parquet(interim / "fact_suicide_la.parquet")
    acc = (pd.read_parquet(interim / "fact_accessibility.parquet")
           if accessibility_df is None else accessibility_df)

    df = (geo
          .merge(pop[["area_code", "male_working_age_pop"]], on="area_code")
          .merge(dep[["area_code", "deprivation_proxy"]], on="area_code")
          .merge(occ[["area_code", "occupation_proxy"]], on="area_code")
          .merge(iso[["area_code", "isolation_proxy"]], on="area_code")
          .merge(acc[["area_code", "travel_minutes", "groups_within_catchment"]],
                 on="area_code"))

    # Map the LA suicide signal DOWN to each small area in that LA. This is the
    # only downward mapping of the outcome, and it stays a single low-weighted
    # LA-level term — we never fabricate a small-area suicide rate. Missing LAs
    # are filled with the WITHIN-NATION median (never across the border); a
    # nation with no suicide source at all (Scotland and NI, once their adapters
    # land) gets a neutral term below so its ranking rests on the proxies.
    df = df.merge(suicide[["la_code", "rate_per_100k"]], on="la_code", how="left")
    df["suicide_signal_la"] = df.groupby("nation")["rate_per_100k"].transform(
        lambda s: s.fillna(s.median()) if s.notna().any() else s)

    # --- Within-nation percentiles for every component --------------------
    df["pct_deprivation"] = _within_nation_pct(df, "deprivation_proxy")
    df["pct_occupation"] = _within_nation_pct(df, "occupation_proxy")
    df["pct_isolation"] = _within_nation_pct(df, "isolation_proxy")
    # Neutral 0.5 where a whole nation lacks any suicide source (all-NaN -> NaN).
    df["pct_suicide_signal"] = _within_nation_pct(df, "suicide_signal_la").fillna(0.5)

    # --- Supply index (higher = better served) ----------------------------
    # Replace non-finite travel times (no groups) with the worst observed.
    tmin = df["travel_minutes"].replace([np.inf, -np.inf], np.nan)
    tmin = tmin.fillna(tmin.max())
    df["_travel_pct"] = _within_nation_pct(df.assign(travel_minutes=tmin), "travel_minutes")
    gmax = df.groupby("nation")["groups_within_catchment"].transform("max").replace(0, 1)
    catchment_norm = df["groups_within_catchment"] / gmax
    tw = float(cfg["accessibility"].get("travel_weight", 0.6)) if travel_weight is None \
        else float(travel_weight)
    raw_supply = tw * (1 - df["_travel_pct"]) + (1 - tw) * catchment_norm
    # Min-max within nation to 0..1.
    gmin = raw_supply.groupby(df["nation"]).transform("min")
    gspan = (raw_supply.groupby(df["nation"]).transform("max") - gmin).replace(0, 1)
    df["supply_index"] = (raw_supply - gmin) / gspan
    return df


def apply_weights(df: pd.DataFrame, comp_w: dict, w_suicide: float):
    """Given prepared percentiles + supply_index, return (need_index, priority,
    reach, contrib) for one weighting scheme. Pure — does not mutate df."""
    total_w = sum(comp_w.values()) + w_suicide
    contrib = {}
    need = np.zeros(len(df))
    for c in PROXY_COMPONENTS:
        part = comp_w[c] * df[f"pct_{c}"]
        contrib[c] = part
        need += part.to_numpy()
    suicide_part = w_suicide * df["pct_suicide_signal"]
    contrib["suicide_signal"] = suicide_part
    need += suicide_part.to_numpy()
    need_index = need / total_w
    priority = need_index * (1 - df["supply_index"].to_numpy())
    reach = priority * df["male_working_age_pop"].to_numpy()
    return need_index, priority, reach, contrib, total_w


def _car_access(cfg: Config) -> pd.Series:
    """Share of households with no car or van, per area. Empty if not ingested.

    DESCRIPTIVE ONLY — never an input to a score. Kept out of
    prepare_components() so no weighting scheme, tier or sensitivity draw can
    reach it even by accident.
    """
    path = cfg.path("interim") / "fact_car_access.parquet"
    if not path.exists():
        return pd.Series(dtype="float64", name="no_car_share")
    car = pd.read_parquet(path)
    return car.set_index("area_code")["no_car_share"].astype(float)


def run(cfg: Config) -> pd.DataFrame:
    comp_w, w_suicide = declared_weights(cfg)
    df = prepare_components(cfg)
    need_index, priority, reach, contrib, total_w = apply_weights(df, comp_w, w_suicide)
    df["need_index"] = need_index
    df["priority_score"] = priority
    df["reach_score"] = reach

    # Ranks (1 = highest priority) and within-nation percentile of priority.
    df["rank"] = df["priority_score"].rank(ascending=False, method="min").astype(int)
    df["rank_reach"] = df["reach_score"].rank(ascending=False, method="min").astype(int)
    df["percentile"] = _within_nation_pct(df, "priority_score")

    # --- Descriptive context: car or van availability ----------------------
    # Attached here, after every score is settled, precisely so it cannot enter
    # one. Optional: the fetch is non-blocking in the pipeline, and an area with
    # no figure shows no figure rather than a made-up one.
    df["no_car_share"] = _car_access(cfg).reindex(df["area_code"].to_numpy()).to_numpy()

    # --- Factor breakdown (per-area explanation) ---------------------------
    def _breakdown(i: int) -> str:
        row = {
            "components": {
                c: {
                    "percentile": round(float(df[f"pct_{c}"].iloc[i]), 4),
                    # Normalised share, so the four weights in a breakdown sum
                    # to 1 and each contribution = weight x percentile.
                    "weight": round(float(comp_w[c]) / total_w, 4),
                    "contribution": round(float(contrib[c].iloc[i] / total_w), 4),
                } for c in PROXY_COMPONENTS
            },
            "suicide_signal": {
                "la_rate_per_100k": (None if pd.isna(df["suicide_signal_la"].iloc[i])
                                     else round(float(df["suicide_signal_la"].iloc[i]), 2)),
                "percentile": round(float(df["pct_suicide_signal"].iloc[i]), 4),
                "weight": round(w_suicide / total_w, 4),
                "contribution": round(float(contrib["suicide_signal"].iloc[i] / total_w), 4),
            },
            "weight_basis": "declared prior (config.yaml scoring.component_weights)",
            "need_index": round(float(df["need_index"].iloc[i]), 4),
            "supply_index": round(float(df["supply_index"].iloc[i]), 4),
            "priority_score": round(float(df["priority_score"].iloc[i]), 4),
        }
        return json.dumps(row)

    df["factor_breakdown"] = [_breakdown(i) for i in range(len(df))]

    cols = [
        "area_code", "area_name", "la_code", "la_name", "region", "nation",
        "centroid_lon", "centroid_lat", "male_working_age_pop",
        "need_index", "supply_index", "priority_score", "reach_score",
        "rank", "rank_reach", "percentile", "travel_minutes",
        "groups_within_catchment", "no_car_share", "factor_breakdown",
    ]
    out = df[cols].sort_values("priority_score", ascending=False).reset_index(drop=True)

    fp = cfg.path("fact_score")
    out.to_parquet(fp, index=False)
    _write_geojson(cfg, out)

    print(f"\n[score] {len(out)} areas scored -> {fp}")
    print("[score] top 5 by priority (per-capita view):")
    for _, r in out.head(5).iterrows():
        print(f"   #{r['rank']:>3}  {r['area_code']}  {r['nation']}  "
              f"priority={r['priority_score']:.3f}  need={r['need_index']:.3f}  "
              f"supply={r['supply_index']:.3f}")
    return out


def _write_geojson(cfg: Config, df: pd.DataFrame) -> None:
    """Write a point GeoJSON at centroids (real LSOA boundaries swap in later)."""
    features = []
    for _, r in df.iterrows():
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [float(r["centroid_lon"]), float(r["centroid_lat"])]},
            "properties": {
                "area_code": r["area_code"], "nation": r["nation"],
                "priority_score": float(r["priority_score"]),
                "reach_score": float(r["reach_score"]),
                "need_index": float(r["need_index"]),
                "supply_index": float(r["supply_index"]),
                "rank": int(r["rank"]),
            },
        })
    fc = {"type": "FeatureCollection", "features": features}
    with open(cfg.path("scored_geojson"), "w") as fh:
        json.dump(fc, fh)
