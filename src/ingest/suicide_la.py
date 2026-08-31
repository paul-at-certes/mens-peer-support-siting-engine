"""Ingest the LA-level suicide signal -> fact_suicide_la.

Suicide deaths are only reliably published at **Local Authority** level (small
numbers + disclosure control below that, plus a ~200-270 day registration lag).
This is the ONLY place the outcome signal enters, and it stays at LA grain — it
is never joined down to a fabricated small-area rate.

Real source: **Nomis `NM_161_1`** (*Mortality statistics: underlying cause, sex
and age*), which exposes cause x sex x age x local authority for England **and**
Wales via the same API the occupation and isolation adapters already use.

  * **Suicide definition:** ICD-10 `X60-X84` (intentional self-harm) + `Y10-Y34`
    (event of undetermined intent) — the standard ONS definition.
  * **Male, all ages, pooled over the latest 5 registration years.**
  * **England and Wales**, all 331 spine local authorities.

**Why all ages and not working age?** This was tried and measured. Nomis applies
a disclosure rule that zeroes any cell below 5, and an LA x cause x 5-year-age-band
x single-year cell is almost always below 5 — across 33,900 such cells the extract
contains no value of 1, 2, 3 or 4 anywhere. Summing the working-age bands recovers
only ~48% of the published national male 15-64 total; requesting all ages recovers
**96.6%**. A working-age series that silently loses half its deaths, and loses them
disproportionately in small local authorities, is worse for calibration than an
all-ages one that is nearly complete. The denominator is therefore male all-ages
population from the spine, so numerator and denominator still match.

Why not the ONS *Suicides by local authority* workbook, which the brief names?
It was checked and rejected: that release is **persons-only**. Neither of its
count tables carries a sex breakdown, so it cannot answer a question about men.
Nomis carries the same registrations with the sex dimension intact.

Why not OHID Fingertips 41001, which this module used previously? It is
England-only, age 10+, and fixed at 3-year pooling. It remains a useful
cross-check on the LA signal (the role the brief originally gave it), but it
cannot cover Wales and its age band does not match the working-age population
this tool is about.

Caveats that remain, and are surfaced in the UI:
  * **All ages, not working age** — see above. The proxies are working-age
    measures, so the outcome is broader than the population the tool targets.
  * **~3.4% of deaths are lost to the below-5 disclosure rule**, and they fall
    disproportionately in small local authorities.
  * **Registration lag.** The latest year is registrations, not occurrences.
  * **Boundary vintage.** Nomis reports the Buckinghamshire and Northamptonshire
    unitaries under their predecessor districts; those are summed back up to the
    successor code (see LA_MERGERS). Whole-district mergers, so the sum is exact.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import Config
from ..fetch import nomis_csv_all
from ..io_utils import read_csv, require_file, validate_columns

REQUIRED = ["la_code", "deaths"]

NOMIS_URL = "https://www.nomisweb.co.uk/api/v01/dataset/NM_161_1.data.csv"
CAUSE_SUICIDE = "2550,2570"        # X60-X84 intentional self-harm + Y10-Y34 undetermined
GENDER_MALE = "1"
# Age 0 = "total (all ages)". Do NOT switch this to the working-age bands: the
# below-5 disclosure rule zeroes almost every cell at that granularity and the
# series loses about half its deaths. Measured, not assumed — see module docstring.
AGE_ALL = "0"
GEOGRAPHY_LAS = "TYPE434"          # the LA type this dataset is actually published at
MEASURE_DEATHS = "1"
DEFAULT_POOL_YEARS = 5

# Nomis publishes some of the 2019-2021 unitary mergers under their predecessor
# districts. Summing counts up to the successor is exact — these are whole-district
# mergers with no boundary splits. A merger is applied ONLY when the successor is
# absent from the extract, so successors Nomis does report directly are left alone.
LA_MERGERS = {
    "E06000058": ["E06000028", "E06000029", "E07000048"],           # Bournemouth, Christchurch and Poole
    "E06000059": ["E07000049", "E07000050", "E07000051",
                  "E07000052", "E07000053"],                        # Dorset
    "E06000060": ["E07000004", "E07000005", "E07000006", "E07000007"],  # Buckinghamshire
    "E06000061": ["E07000150", "E07000152", "E07000153", "E07000156"],  # North Northamptonshire
    "E06000062": ["E07000151", "E07000154", "E07000155"],           # West Northamptonshire
    "E07000244": ["E07000205", "E07000206"],                        # East Suffolk
    "E07000245": ["E07000201", "E07000204"],                        # West Suffolk
    "E07000246": ["E07000190", "E07000191"],                        # Somerset West and Taunton
}


def _raw_path(cfg: Config) -> Path:
    base = cfg.path("synthetic_raw") if cfg.mode == "synthetic" else cfg.path("real_raw")
    return base / "suicide_la.csv"


def _pool_years(cfg: Config) -> int:
    return int((cfg.get("calibration", {}) or {}).get("pool_years", DEFAULT_POOL_YEARS))


def _build_real(cfg: Config, dest: Path) -> Path | None:
    """Fetch and reshape the Nomis extract. Returns None on failure so the
    caller falls through to require_file's actionable 'place the file here'
    message rather than surfacing a raw HTTP error."""
    if dest.exists():
        return dest
    pool = _pool_years(cfg)
    print(f"[suicide_la] fetching male suicide counts by LA, {pool}-year pooled "
          f"(Nomis NM_161_1, England & Wales) ...")
    params = {
        "geography": GEOGRAPHY_LAS,
        "cause_of_death": CAUSE_SUICIDE,
        "gender": GENDER_MALE,
        "age": AGE_ALL,
        "measure": MEASURE_DEATHS,
        "measures": "20100",
        "date": f"latestMINUS{pool - 1}-latest",
        "select": "date_name,geography_code,geography_name,obs_value",
    }
    try:
        # Paginated: a truncated extract silently loses whole years, and the
        # 25,000-row cap is easy to hit as dimensions are added.
        df = nomis_csv_all(NOMIS_URL, params,
                           cache_path=cfg.path("real_raw") / "nomis_mortality_suicide.csv")
    except Exception as exc:  # noqa: BLE001
        print(f"[suicide_la] WARNING: could not fetch from Nomis ({type(exc).__name__}: {exc}).")
        return None
    if df.empty or "OBS_VALUE" not in df.columns:
        print("[suicide_la] WARNING: Nomis returned no usable rows.")
        return None

    df = df.rename(columns={"GEOGRAPHY_CODE": "la_code", "GEOGRAPHY_NAME": "la_name",
                            "OBS_VALUE": "deaths"})
    years = sorted(df["DATE_NAME"].astype(str).unique())
    # Sum over cause codes, age bands and years -> one pooled count per LA.
    pooled = (df.groupby(["la_code", "la_name"], as_index=False)["deaths"].sum())

    # Fold predecessor districts into their successor unitary authority, but only
    # where Nomis does not already report the successor directly.
    present = set(pooled["la_code"])
    merged_rows, consumed = [], set()
    for succ, preds in LA_MERGERS.items():
        if succ in present:
            continue
        part = pooled[pooled["la_code"].isin(preds)]
        if part.empty:
            continue
        merged_rows.append({"la_code": succ, "la_name": succ,
                            "deaths": float(part["deaths"].sum())})
        consumed.update(part["la_code"])
    if merged_rows:
        pooled = pd.concat([pooled[~pooled["la_code"].isin(consumed)],
                            pd.DataFrame(merged_rows)], ignore_index=True)

    pooled["years_pooled"] = f"{years[0]}-{years[-1]}" if years else ""
    pooled["sex"] = "M"
    pooled["age_band"] = "all ages"
    dest.parent.mkdir(parents=True, exist_ok=True)
    pooled.to_csv(dest, index=False)
    print(f"[suicide_la] {len(pooled)} LAs pooled over {years[0]}-{years[-1]} "
          f"({len(merged_rows)} merged from predecessor districts) -> {dest.name}")
    return dest


# The at-risk population must cover the same people as the death counts. Deaths
# are male all-ages, so the offset is male all-ages population — not the
# working-age column. Mixing the two is the mismatch that previously biased the
# calibration offset with local age structure.
POP_COLUMN = "male_pop"


def _la_population(cfg: Config) -> pd.DataFrame:
    """Male all-ages population per LA, aggregated from the population spine.

    Nomis publishes the deaths but no usable denominator at this grain (its
    percentage-of-population measure rounds to three decimal places, which is far
    too coarse for a regression offset). The spine's own Census population keeps
    numerator and denominator on matched boundaries and matched age coverage.
    """
    interim = cfg.path("interim")
    geo = pd.read_parquet(interim / "dim_geography.parquet")[["area_code", "la_code"]]
    pop = pd.read_parquet(interim / "dim_population.parquet")[["area_code", POP_COLUMN]]
    return (geo.merge(pop, on="area_code")
               .groupby("la_code", as_index=False)[POP_COLUMN].sum()
               .rename(columns={POP_COLUMN: "population"}))


def run(cfg: Config) -> pd.DataFrame:
    path = _raw_path(cfg)
    if cfg.mode == "real":
        _build_real(cfg, path)
    src = require_file(
        path, "male working-age suicide by local authority",
        "Nomis NM_161_1 (cause X60-X84 + Y10-Y34, gender Male, age 0 = all ages, "
        "geography TYPE434) — all ages, not working age; see this module's docstring "
        "— https://www.nomisweb.co.uk/datasets/mortsa")
    df = validate_columns(read_csv(src), REQUIRED, "suicide_la").copy()

    # Guardrail: this is aggregate count data, not person-level records.
    if (df["deaths"] <= 0).all():
        raise ValueError("[suicide_la] all-zero deaths column — check the source file.")

    if "population" not in df.columns:
        df = df.merge(_la_population(cfg), on="la_code", how="left")
    missing_pop = int(df["population"].isna().sum())
    if missing_pop:
        print(f"[suicide_la] NOTE: {missing_pop} LA(s) have deaths but no population in the "
              f"spine — dropped (they cannot contribute to a rate or an offset).")
        df = df.dropna(subset=["population"])

    df["rate_per_100k"] = 100_000 * df["deaths"] / df["population"].clip(lower=1)
    out = cfg.path("interim") / "fact_suicide_la.parquet"
    df.to_parquet(out, index=False)
    nations = df["la_code"].str[0].value_counts().to_dict()
    print(f"[suicide_la] {len(df)} LAs {nations}, total pooled male deaths="
          f"{int(df['deaths'].sum())} -> {out.name}")
    return df
