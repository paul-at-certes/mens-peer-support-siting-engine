"""SPIKE — does the need surface agree with where Andy's Man Club already opened?

This is throwaway research code, deliberately kept OUT of ``src/``. Nothing in
the pipeline imports it. It exists so the numbers in
``docs/adr/0003-concordance-with-existing-groups.md`` can be re-derived.

WHAT IT ANSWERS
    Every review of this tool will open with the same question: "does it agree
    with what we already know?" AMC has opened 354 groups over a decade of local
    judgement. If the need surface says those places are unremarkable, the
    surface has a problem. If it says they are high-need, that is corroboration
    from a source the model has never seen.

WHAT IT IS NOT
    This is NOT the back-test in docs/design.md section 7 check 3. That check asks
    whether areas the model prioritised went on to sustain good attendance. It
    needs opening dates and attendance figures, neither of which is in any open
    source — AMC would have to provide them. What is measurable today is
    CONCORDANCE: does the surface, built from open data alone, independently
    rate the places a charity chose by other means?

    Existing groups are corroboration, not ground truth. AMC's siting reflects
    volunteer availability, venue offers and founder geography as much as need.
    Agreement raises confidence; disagreement is not automatically the model's
    error, and is not read as one here.

THE ONE THING THAT WOULD MAKE THIS CIRCULAR
    ``priority_score`` subtracts a supply surface built FROM these 354 groups.
    Scoring the groups against it would be marking the model's homework with its
    own answers: every group sits where supply is, by construction, maximal.
    Everything below therefore uses ``need_index`` and its components ONLY. The
    supply side of the model is not under test here and cannot be.

THE FOUR MEASUREMENTS
    A. BETWEEN LA  — do the local authorities that have a group score higher
       than those that do not? This is the "right towns?" question. It is the
       one most contaminated by founder geography: AMC began in Halifax and grew
       outward through the North, and the North is also more deprived. So it is
       run twice, the second time permuting only WITHIN region, which asks the
       fairer question — given the region, did they pick its higher-need LAs?

    B. WITHIN LA   — among the small areas of an LA that has a group, how high
       does the group's own small area rank? This is the "right neighbourhood?"
       question, and it is immune to founder geography entirely: it never
       compares one LA with another.

    C. VENUE vs CATCHMENT — B scores the neighbourhood the VENUE sits in, which
       is a town centre with a hall to hire, not necessarily anybody's home. So
       C repeats the measurement over the areas each group actually serves (the
       small areas whose nearest group it is). If the venue LSOA under-reads,
       this is where it shows.

    D. BY COMPONENT — B, but for deprivation, occupation and isolation
       separately. Directly relevant to the occupational blind spot: if
       concordance is carried by deprivation alone, then the one component the
       weighting outvotes is also the one AMC's own choices do not corroborate.

METHOD — assignment, and why it is checked twice
    Group -> small area is done by POSTCODE through the ONS Postcode Directory
    (exact: ONSPD assigns each postcode to the LSOA it falls in). It is then
    cross-checked against nearest population-weighted centroid, which is what
    this repo would otherwise have had to use, and the disagreement rate is
    reported. Nearest-centroid is not accurate enough for measurement B — in a
    dense town the nearest centroid is routinely the next LSOA over — so if the
    postcode route fails the run stops rather than quietly degrading.

USAGE
    python spikes/group_need_concordance.py
    python spikes/group_need_concordance.py --draws 20000 --json out.json

The ONSPD lookup for the ~354 group postcodes is cached under
data/raw/real/ (git-ignored). First run takes a few minutes; the ArcGIS hosted
table answers an IN-list of 40 postcodes in about 20 seconds.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config  # noqa: E402
from src.fetch import get  # noqa: E402

ONSPD_LAYER = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "ONS_Postcode_Directory_(May_2026)_for_the_United_Kingdom_(Hosted_Table)/"
    "FeatureServer/0/query"
)
ONSPD_VINTAGE = "ONS Postcode Directory, May 2026 (LSOA 2021)"
# The hosted table is ~2.7M rows and the IN-list scan is not indexed, so a big
# chunk is not faster than a small one -- it just risks the 45s timeout. 40 is
# comfortably inside it.
_CHUNK = 40


# ---------------------------------------------------------------------------
# Assignment: group -> small area
# ---------------------------------------------------------------------------
def normalise_postcode(pc: str) -> str:
    """Put a postcode into ONSPD's ``pcds`` form: upper case, exactly one space
    before the final three characters.

    The AMC listing is hand-entered and about 2% of it arrives without the
    space -- "SS155NX", "LL138DG". ONSPD stores "SS15 5NX", so an equality join
    on the raw string silently drops those groups. Silently, because a missing
    group looks exactly like a group in Scotland: both are simply absent from
    the England & Wales analysis. Five of the six unmatched postcodes on the
    2026-09-01 harvest were this and nothing more.

    A postcode too short to split is returned squeezed rather than mangled --
    it will fail to match, which is the correct outcome for a truncated entry
    (one group's postcode is recorded as "AB54 8J", which is not a postcode).
    """
    squeezed = "".join(str(pc).split()).upper()
    if len(squeezed) < 5:
        return squeezed
    return f"{squeezed[:-3]} {squeezed[-3:]}"


def onspd_lookup(postcodes: list[str], cache: Path, *, force: bool = False) -> pd.DataFrame:
    """Postcode -> LSOA21 via the ONS Postcode Directory. Cached to ``cache``.

    Returns columns pcds, lsoa21cd, lad25cd, ctry25cd, doterm. Rows for
    postcodes ONSPD does not know are simply absent -- the caller reports them.
    """
    if cache.exists() and not force:
        return pd.read_csv(cache)

    wanted = sorted({normalise_postcode(p) for p in postcodes if isinstance(p, str) and p.strip()})
    rows: list[dict] = []
    for i in range(0, len(wanted), _CHUNK):
        chunk = wanted[i : i + _CHUNK]
        where = "pcds IN (" + ",".join("'" + p.replace("'", "''") + "'" for p in chunk) + ")"
        resp = get(
            ONSPD_LAYER,
            params={
                "where": where,
                "outFields": "pcds,lsoa21cd,lad25cd,ctry25cd,doterm",
                "returnGeometry": "false",
                "f": "json",
            },
        )
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"ONSPD query failed: {payload['error']}")
        rows += [f["attributes"] for f in payload.get("features", [])]
        print(f"  ONSPD {min(i + _CHUNK, len(wanted))}/{len(wanted)} postcodes", flush=True)

    out = pd.DataFrame(rows)
    if out.empty:
        raise RuntimeError("ONSPD returned no rows for any group postcode -- refusing to cache.")
    cache.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(cache, index=False)
    return out


def nearest_centroid(groups: pd.DataFrame, geo: pd.DataFrame) -> pd.Series:
    """Nearest population-weighted LSOA centroid to each group. Cross-check only.

    Haversine over a 354 x 35,672 grid -- small enough to do densely.
    """
    lat1 = np.radians(groups["lat"].to_numpy())[:, None]
    lon1 = np.radians(groups["lon"].to_numpy())[:, None]
    lat2 = np.radians(geo["centroid_lat"].to_numpy())[None, :]
    lon2 = np.radians(geo["centroid_lon"].to_numpy())[None, :]
    d = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    idx = np.argmin(d, axis=1)
    return pd.Series(geo["area_code"].to_numpy()[idx], index=groups.index)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def within_group_percentile(values: np.ndarray, target: float) -> float:
    """Percentile of ``target`` within ``values`` (midrank; 0..1)."""
    below = float(np.sum(values < target))
    equal = float(np.sum(values == target))
    return (below + 0.5 * equal) / len(values)


def within_la_test(
    placed: pd.DataFrame, scored: pd.DataFrame, column: str, *, draws: int, seed: int
) -> dict:
    """Measurement B/D: rank each group's own small area within its own LA.

    Under the null -- venues placed independently of ``column`` within the LA --
    each percentile is Uniform(0,1) and the mean is 0.5. The permutation
    redraws one venue small area per real venue from the same LA, preserving how
    many groups each LA has and how many small areas it has to choose from.
    """
    by_la = {la: g[column].to_numpy() for la, g in scored.groupby("la_code")}
    obs, sizes, las = [], [], []
    for _, row in placed.iterrows():
        pool = by_la.get(row["la_code"])
        if pool is None or len(pool) < 2:
            continue
        obs.append(within_group_percentile(pool, row[column]))
        sizes.append(len(pool))
        las.append(row["la_code"])
    obs_arr = np.array(obs)

    rng = _rng(seed)
    null_means = np.empty(draws)
    pools = [by_la[la] for la in las]
    for d in range(draws):
        draw = [within_group_percentile(p, p[rng.integers(len(p))]) for p in pools]
        null_means[d] = float(np.mean(draw))

    mean = float(obs_arr.mean())
    p = float((np.sum(null_means >= mean) + 1) / (draws + 1))
    return {
        "column": column,
        "n_groups": int(len(obs_arr)),
        "mean_percentile": round(mean, 4),
        "median_percentile": round(float(np.median(obs_arr)), 4),
        "share_above_half": round(float(np.mean(obs_arr > 0.5)), 4),
        "share_top_decile": round(float(np.mean(obs_arr >= 0.9)), 4),
        "null_mean": round(float(null_means.mean()), 4),
        "p_one_sided": round(p, 5),
        "percentiles": [round(float(v), 4) for v in obs_arr],
        "la_codes": las,
    }


def between_la_test(
    la_table: pd.DataFrame, *, draws: int, seed: int, stratify_by: str | None = None
) -> dict:
    """Measurement A: do LAs that have a group score higher than those that don't?

    Statistic is the difference in mean LA need (population-weighted within LA,
    then unweighted across LAs -- an LA is one decision, regardless of size).
    ``stratify_by`` permutes the has-group label only within each stratum, which
    is how founder geography is held constant.
    """
    has = la_table["has_group"].to_numpy()
    val = la_table["la_need"].to_numpy()
    obs = float(val[has].mean() - val[~has].mean())

    rng = _rng(seed)
    if stratify_by is None:
        blocks = [np.arange(len(la_table))]
    else:
        strata = la_table[stratify_by].to_numpy()
        blocks = [np.where(strata == s)[0] for s in pd.unique(strata)]

    null = np.empty(draws)
    for d in range(draws):
        shuffled = has.copy()
        for block in blocks:
            shuffled[block] = rng.permutation(has[block])
        null[d] = val[shuffled].mean() - val[~shuffled].mean()
    p = float((np.sum(null >= obs) + 1) / (draws + 1))

    return {
        "stratified_by": stratify_by or "none (national)",
        "n_las_with_group": int(has.sum()),
        "n_las_without": int((~has).sum()),
        "mean_need_with_group": round(float(val[has].mean()), 4),
        "mean_need_without": round(float(val[~has].mean()), 4),
        "difference": round(obs, 4),
        "null_difference_mean": round(float(null.mean()), 4),
        "p_one_sided": round(p, 5),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _fmt_pct(x: float) -> str:
    return f"{100 * x:.1f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--draws", type=int, default=10000, help="permutation draws (default 10000)")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--json", type=Path, default=Path("data/output/concordance.json"))
    ap.add_argument("--refresh-onspd", action="store_true", help="re-fetch the postcode lookup")
    args = ap.parse_args(argv)

    cfg = load_config()
    interim = cfg.path("interim")

    scored = pd.read_parquet(cfg.path("fact_score"))
    geo = pd.read_parquet(interim / "dim_geography.parquet")
    pop = pd.read_parquet(interim / "dim_population.parquet")
    prov = pd.read_parquet(interim / "dim_provision.parquet")
    acc = pd.read_parquet(interim / "fact_accessibility.parquet")

    # Component percentiles live in the factor_breakdown JSON, one blob per area.
    comps = scored["factor_breakdown"].map(json.loads)
    for name in ("deprivation", "occupation", "isolation"):
        scored[f"{name}_pct"] = [c["components"][name]["percentile"] for c in comps]

    print(f"\nCONCORDANCE SPIKE -- need surface vs {len(prov)} existing groups")
    print("=" * 72)

    # --- assignment ---------------------------------------------------------
    cache = cfg.path("real_raw") / "group_postcode_lsoa.csv"
    print(f"\nAssigning groups to small areas via {ONSPD_VINTAGE}")
    lookup = onspd_lookup(prov["postcode"].tolist(), cache, force=args.refresh_onspd)
    lookup["pcds"] = lookup["pcds"].map(normalise_postcode)

    prov = prov.copy()
    prov["pcds"] = prov["postcode"].map(normalise_postcode)
    reformatted = int((prov["pcds"] != prov["postcode"].str.upper().str.strip()).sum())
    prov = prov.merge(
        lookup[["pcds", "lsoa21cd", "ctry25cd", "doterm"]].drop_duplicates("pcds"),
        on="pcds",
        how="left",
    )

    unmatched = prov["lsoa21cd"].isna().sum()
    terminated = prov["doterm"].notna().sum()
    prov["nearest_lsoa"] = nearest_centroid(prov, geo)
    both = prov.dropna(subset=["lsoa21cd"])
    agree = float((both["lsoa21cd"] == both["nearest_lsoa"]).mean())

    print(f"  postcode matched      : {len(prov) - unmatched}/{len(prov)}"
          f"  ({unmatched} unmatched, {terminated} terminated postcodes)")
    print(f"  reformatted to pcds   : {reformatted} -- hand-entered without the space; "
          f"an exact join would have dropped them as though they were Scottish")
    print(f"  nearest-centroid agrees: {_fmt_pct(agree)}% -- the other "
          f"{_fmt_pct(1 - agree)}% is why assignment is by postcode, not centroid")

    scored_codes = set(scored["area_code"])
    placed = prov[prov["lsoa21cd"].isin(scored_codes)].copy()
    outside = len(prov) - unmatched - len(placed)
    print(f"  in the scored surface : {len(placed)}  ({outside} outside England & Wales, "
          f"{unmatched} unassignable)")

    placed = placed.merge(
        scored[["area_code", "la_code", "la_name", "region", "nation", "need_index",
                "deprivation_pct", "occupation_pct", "isolation_pct", "percentile"]],
        left_on="lsoa21cd", right_on="area_code", how="left",
    )

    # --- A: between LA ------------------------------------------------------
    pop_w = scored.merge(pop[["area_code", "male_working_age_pop"]].rename(
        columns={"male_working_age_pop": "w"}), on="area_code", how="left")
    pop_w["w"] = pop_w["w"].fillna(0).clip(lower=1)
    la_need = (pop_w.assign(num=pop_w["need_index"] * pop_w["w"])
               .groupby(["la_code", "region", "nation"], as_index=False)
               .agg(num=("num", "sum"), w=("w", "sum")))
    la_need["la_need"] = la_need["num"] / la_need["w"]
    la_need["has_group"] = la_need["la_code"].isin(set(placed["la_code"]))

    print("\nA. BETWEEN LOCAL AUTHORITY -- did they pick the higher-need towns?")
    a_nat = between_la_test(la_need, draws=args.draws, seed=args.seed)
    a_reg = between_la_test(la_need, draws=args.draws, seed=args.seed, stratify_by="region")
    for label, res in (("national", a_nat), ("within region", a_reg)):
        print(f"  {label:<14} {res['n_las_with_group']} LAs with a group mean need "
              f"{res['mean_need_with_group']:.3f} vs {res['mean_need_without']:.3f} "
              f"for {res['n_las_without']} without  "
              f"(diff {res['difference']:+.3f}, p={res['p_one_sided']})")

    # --- B: within LA -------------------------------------------------------
    print("\nB. WITHIN LOCAL AUTHORITY -- did they pick the higher-need neighbourhoods?")
    b = within_la_test(placed, scored, "need_index", draws=args.draws, seed=args.seed)
    print(f"  {b['n_groups']} groups. Mean rank of the venue's own small area within its LA: "
          f"{_fmt_pct(b['mean_percentile'])}th percentile (null 50.0)")
    print(f"  median {_fmt_pct(b['median_percentile'])}th, "
          f"{_fmt_pct(b['share_above_half'])}% above their LA's midpoint, "
          f"{_fmt_pct(b['share_top_decile'])}% in their LA's top decile, p={b['p_one_sided']}")

    # --- C: venue vs catchment ---------------------------------------------
    print("\nC. VENUE vs CATCHMENT -- is the venue's own area representative of who it serves?")
    served = acc.merge(scored[["area_code", "la_code", "need_index"]], on="area_code", how="left")
    served = served.merge(pop[["area_code", "male_working_age_pop"]], on="area_code", how="left")
    served["male_working_age_pop"] = served["male_working_age_pop"].fillna(0).clip(lower=1)
    served["num"] = served["need_index"] * served["male_working_age_pop"]
    catch = (served.groupby("nearest_group_id", as_index=False)
             .agg(num=("num", "sum"), w=("male_working_age_pop", "sum"),
                  n_areas=("area_code", "count")))
    catch["catchment_need"] = catch["num"] / catch["w"]

    placed_c = placed.merge(catch, left_on="group_id", right_on="nearest_group_id", how="left")
    have = placed_c.dropna(subset=["catchment_need"])
    nat_pool = scored["need_index"].to_numpy()
    venue_nat = np.array([within_group_percentile(nat_pool, v) for v in have["need_index"]])
    catch_nat = np.array([within_group_percentile(nat_pool, v) for v in have["catchment_need"]])
    print(f"  {len(have)} groups are some area's nearest. On the NATIONAL need scale:")
    print(f"    the venue's own small area   : mean {_fmt_pct(float(venue_nat.mean()))}th percentile")
    print(f"    the areas it is nearest to   : mean {_fmt_pct(float(catch_nat.mean()))}th percentile "
          f"(median catchment {int(have['n_areas'].median())} small areas)")
    delta = float(catch_nat.mean() - venue_nat.mean())
    print(f"    difference {100 * delta:+.1f} points -- "
          + ("the venue area UNDER-states who it serves" if delta > 0.01
             else "the venue area OVER-states who it serves" if delta < -0.01
             else "the venue area is representative"))

    # --- D: by component ----------------------------------------------------
    print("\nD. BY COMPONENT -- which part of the surface do their choices corroborate?")
    d_res = {}
    for name in ("deprivation", "occupation", "isolation"):
        r = within_la_test(placed, scored, f"{name}_pct", draws=args.draws, seed=args.seed)
        d_res[name] = r
        print(f"  {name:<12} mean {_fmt_pct(r['mean_percentile'])}th percentile within LA, "
              f"p={r['p_one_sided']}")

    payload = {
        "spike": "group_need_concordance",
        "question": "Does need_index agree with where AMC already opened groups?",
        "not_a_backtest": (
            "Concordance only. design.md s7 check 3 needs opening dates and attendance, "
            "which no open source carries."
        ),
        "inputs": {
            "groups_total": int(len(prov)),
            "groups_assigned": int(len(placed)),
            "groups_unmatched_postcode": int(unmatched),
            "postcodes_reformatted": reformatted,
            "groups_outside_england_wales": int(outside),
            "assignment": ONSPD_VINTAGE,
            "nearest_centroid_agreement": round(agree, 4),
            "scored_areas": int(len(scored)),
            "draws": args.draws,
            "seed": args.seed,
        },
        "a_between_la": {"national": a_nat, "within_region": a_reg},
        "b_within_la": b,
        "c_venue_vs_catchment": {
            "n_groups": int(len(have)),
            "venue_mean_national_percentile": round(float(venue_nat.mean()), 4),
            "catchment_mean_national_percentile": round(float(catch_nat.mean()), 4),
            "difference": round(delta, 4),
            "median_catchment_areas": int(have["n_areas"].median()),
        },
        "d_by_component": d_res,
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, indent=2))
    print(f"\nWritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
