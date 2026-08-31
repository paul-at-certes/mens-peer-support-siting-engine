"""Ingest car availability -> fact_car_access (keyed to area_code).

DESCRIPTIVE CONTEXT ONLY. Nothing here enters need_index, supply_index,
priority_score, the factor breakdown, the tiers or the sensitivity analysis.

Why it exists. The supply surface is built from CAR travel times (real OSRM road
routing). Andy's Man Club sessions run on Monday evenings, and the men most
likely to need a free peer-support group are among the least likely to own a
car. So in an area where many households have no car, the drive time flatters
how reachable a group actually is. We cannot model public transport yet — that
needs a time-of-day parameter on the TravelTimeProvider, round-trip feasibility
(the last bus home is usually the binding constraint for an evening session) and
a local routing engine for the full matrix — but we can say where the current
number is most misleading, per area, on the map face and in the PDF.

It is NOT, however, the weight for blending car and public-transport access.
This module used to say it was. A spike measured it and the opposite is true:
no-car share correlates +0.66 with whether an evening bus round trip is even
possible (spikes/pt_evening_access.py, docs/adr/0002-*). Where the buses work a
third of households have no car; where they do not, seven in eight have one.
Blending on this share would cancel most of the correction it was meant to make.
Exposure and the quality of the alternative are different quantities and have to
stay separate.

Real source: Census 2021, Nomis table TS045 "Car or van availability"
(NM_2063_1) at LSOA 2021. Dimension codes were read from the dataset definition
at .../NM_2063_1.def.sdmx.json and its c2021_cars_5 codelist, not guessed:

    C2021_CARS_5 = 0   Total: All households
    C2021_CARS_5 = 1   No cars or vans in household

CAVEAT carried downstream: this counts HOUSEHOLDS, not men. A household with a
car does not mean every adult in it can use one on a Monday evening, so this is
a floor on the number of men facing a non-car journey, not an estimate of it.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import Config
from ..fetch import nomis_csv_all
from ..geography import EW_LSOA_GEOG
from ..io_utils import read_csv, require_file, validate_columns

REQUIRED = ["area_code", "households", "no_car_households"]

NOMIS_CARS_URL = "https://www.nomisweb.co.uk/api/v01/dataset/NM_2063_1.data.csv"
ALL_HOUSEHOLDS = 0     # C2021_CARS_5 code for "Total: All households"
NO_CAR = 1             # C2021_CARS_5 code for "No cars or vans in household"

# Sanity bands for the national extract. Census 2021 counted 24.8 million
# households in England & Wales, about 23% of them with no car or van. Two ways
# this API fails quietly: a geography type that returns rows carrying no values,
# and the disclosure rule that zeroes small cells. Both leave the row count
# looking healthy, so we check the VALUES, not the shape.
EXPECTED_HOUSEHOLDS = (22_000_000, 28_000_000)
EXPECTED_NO_CAR_SHARE = (0.10, 0.40)
EXPECTED_AREAS = 30_000        # England & Wales has 35,672 LSOAs


def _raw_path(cfg: Config) -> Path:
    base = cfg.path("synthetic_raw") if cfg.mode == "synthetic" else cfg.path("real_raw")
    return base / "car_access.csv"


def _check_extract(df: pd.DataFrame) -> None:
    """Fail loudly if the national totals say the extract is not trustworthy."""
    if len(df) < EXPECTED_AREAS:
        raise ValueError(
            f"car_access: only {len(df):,} areas returned, expected at least "
            f"{EXPECTED_AREAS:,}. The Nomis extract looks truncated — "
            f"nomis_csv_all pages at 25,000 rows, so check the paginator ran.")
    if df[["households", "no_car_households"]].isna().any().any():
        raise ValueError("car_access: the extract contains missing values. A Nomis "
                         "geography type that returns rows with no values will do "
                         "this. Check the geography parameter.")
    total = int(df["households"].sum())
    no_car = int(df["no_car_households"].sum())
    if not EXPECTED_HOUSEHOLDS[0] <= total <= EXPECTED_HOUSEHOLDS[1]:
        raise ValueError(
            f"car_access: national household total {total:,} falls outside the "
            f"plausible range {EXPECTED_HOUSEHOLDS[0]:,}-{EXPECTED_HOUSEHOLDS[1]:,}. "
            f"Census 2021 counted about 24.8 million households in England & Wales, "
            f"so the extract is incomplete or the wrong dimension code was used.")
    share = no_car / max(total, 1)
    if not EXPECTED_NO_CAR_SHARE[0] <= share <= EXPECTED_NO_CAR_SHARE[1]:
        raise ValueError(
            f"car_access: national no-car share {share:.1%} falls outside the "
            f"plausible range {EXPECTED_NO_CAR_SHARE[0]:.0%}-"
            f"{EXPECTED_NO_CAR_SHARE[1]:.0%} (Census 2021: about 23%). Check the "
            f"C2021_CARS_5 codes against the dataset definition.")
    print(f"[car_access] extract check: {len(df):,} areas, {total:,} households, "
          f"{no_car:,} with no car or van ({share:.1%} nationally).")


def _build_real(cfg: Config, dest: Path) -> Path:
    if dest.exists():
        return dest
    print("[car_access] fetching Census 2021 car or van availability (Nomis TS045) ...")
    raw = nomis_csv_all(
        NOMIS_CARS_URL,
        {"geography": EW_LSOA_GEOG,
         "c2021_cars_5": f"{ALL_HOUSEHOLDS},{NO_CAR}",
         "measures": "20100",
         "select": "GEOGRAPHY_CODE,C2021_CARS_5,OBS_VALUE"},
        cache_path=cfg.path("real_raw") / "car_access_raw.csv",
    ).rename(columns={"GEOGRAPHY_CODE": "area_code", "OBS_VALUE": "value"})

    total = (raw[raw.C2021_CARS_5 == ALL_HOUSEHOLDS].set_index("area_code")["value"]
             .rename("households"))
    none = (raw[raw.C2021_CARS_5 == NO_CAR].set_index("area_code")["value"]
            .rename("no_car_households"))
    df = pd.concat([total, none], axis=1).reset_index()
    _check_extract(df)

    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(dest, index=False)
    return dest


def run(cfg: Config) -> pd.DataFrame:
    path = _raw_path(cfg)
    if cfg.mode == "real":
        _build_real(cfg, path)
    src = require_file(path, "car access (Census 2021 car or van availability)",
                       "Nomis Census 2021 TS045 (NM_2063_1)")
    df = validate_columns(read_csv(src), REQUIRED, "car access")
    df = df[REQUIRED].copy()
    # Share of households with no car or van, 0..1. Clipped because a handful of
    # areas carry a zero household count (communal-establishment-only LSOAs).
    df["no_car_share"] = (df["no_car_households"] / df["households"].clip(lower=1)).clip(0, 1)
    out = cfg.path("interim") / "fact_car_access.parquet"
    df.to_parquet(out, index=False)
    print(f"[car_access] {len(df)} areas -> {out.name} "
          f"(median no-car share {df['no_car_share'].median():.1%})")
    return df
