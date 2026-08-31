"""Ingest isolation -> fact_isolation (keyed to area_code).

Two area-level isolation proxies:
  (a) male_single_separated_pct — share of men (16+) who are single, separated
      or divorced. Census 2021, Nomis RM074 "Legal partnership status by sex by
      age" (NM_2174_1): categories 1 (never married/never CP), 3 (separated),
      4 (divorced) over category 0 (total).
  (b) one_person_household_pct — share of one-person households. Census 2021,
      Nomis TS003 "Household composition" (NM_2023_1): code 1001 over 0.

HONESTY CAVEAT: (b) is a HOUSEHOLD characteristic, NOT a male person count —
Census 2021 publishes no sex-broken "men living alone" figure at LSOA grain
(the finest is MSOA). So we name it one_person_household_pct, not
male_living_alone_pct, and treat it as an area context signal.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import Config
from ..fetch import nomis_csv_all
from ..io_utils import read_csv, require_file, validate_columns

REQUIRED = ["area_code", "male_single_separated_pct", "one_person_household_pct"]

NOMIS_MARITAL_URL = "https://www.nomisweb.co.uk/api/v01/dataset/NM_2174_1.data.csv"
NOMIS_HHCOMP_URL = "https://www.nomisweb.co.uk/api/v01/dataset/NM_2023_1.data.csv"
SINGLE_SEP_CODES = [1, 3, 4]   # single, separated, divorced


def _raw_path(cfg: Config) -> Path:
    base = cfg.path("synthetic_raw") if cfg.mode == "synthetic" else cfg.path("real_raw")
    return base / "isolation.csv"


def _build_real(cfg: Config, dest: Path) -> Path:
    if dest.exists():
        return dest
    real_raw = cfg.path("real_raw")
    print("[isolation] fetching Census 2021 marital-status-by-sex (Nomis RM074) ...")
    marital = nomis_csv_all(
        NOMIS_MARITAL_URL,
        {"geography": "TYPE151", "c2021_lpstat_6": "0,1,3,4", "c2021_age_7": "0",
         "c_sex": "2", "measures": "20100",
         "select": "GEOGRAPHY_CODE,C2021_LPSTAT_6,OBS_VALUE"},
        cache_path=real_raw / "marital_raw.csv",
    ).rename(columns={"GEOGRAPHY_CODE": "area_code", "OBS_VALUE": "value"})
    m_total = (marital[marital.C2021_LPSTAT_6 == 0].set_index("area_code")["value"]
               .rename("male_16plus"))
    m_ss = (marital[marital.C2021_LPSTAT_6.isin(SINGLE_SEP_CODES)]
            .groupby("area_code")["value"].sum().rename("male_single_separated"))
    md = pd.concat([m_total, m_ss], axis=1)
    md["male_single_separated_pct"] = md["male_single_separated"] / md["male_16plus"].clip(lower=1)

    print("[isolation] fetching Census 2021 household composition (Nomis TS003) ...")
    hh = nomis_csv_all(
        NOMIS_HHCOMP_URL,
        {"geography": "TYPE151", "c2021_hhcomp_15": "0,1001", "measures": "20100",
         "select": "GEOGRAPHY_CODE,C2021_HHCOMP_15,OBS_VALUE"},
        cache_path=real_raw / "hhcomp_raw.csv",
    ).rename(columns={"GEOGRAPHY_CODE": "area_code", "OBS_VALUE": "value"})
    h_total = (hh[hh.C2021_HHCOMP_15 == 0].set_index("area_code")["value"]
               .rename("households"))
    h_alone = (hh[hh.C2021_HHCOMP_15 == 1001].set_index("area_code")["value"]
               .rename("one_person_households"))
    hd = pd.concat([h_total, h_alone], axis=1)
    hd["one_person_household_pct"] = hd["one_person_households"] / hd["households"].clip(lower=1)

    df = md[["male_single_separated_pct"]].join(hd[["one_person_household_pct"]],
                                                how="inner").reset_index()
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(dest, index=False)
    return dest


def run(cfg: Config) -> pd.DataFrame:
    path = _raw_path(cfg)
    if cfg.mode == "real":
        _build_real(cfg, path)
    src = require_file(path, "isolation (Census 2021 marital status + household composition)",
                       "Nomis Census 2021 RM074 + TS003")
    df = validate_columns(read_csv(src), REQUIRED, "isolation")
    df = df[REQUIRED].copy()
    # Combine the two isolation signals into one proxy (mean of the two shares).
    df["isolation_proxy"] = df[
        ["male_single_separated_pct", "one_person_household_pct"]
    ].mean(axis=1)
    out = cfg.path("interim") / "fact_isolation.parquet"
    df.to_parquet(out, index=False)
    print(f"[isolation] {len(df)} areas -> {out.name}")
    return df
