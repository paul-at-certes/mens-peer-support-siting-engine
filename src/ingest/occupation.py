"""Ingest occupation -> fact_occupation (keyed to area_code).

Male employment share in elevated-suicide-risk occupations.

Real source: Census 2021, Nomis table RM107 "Occupation by sex" (NM_2207_1) at
LSOA 2021. IMPORTANT CONSTRAINT discovered from the data: the only occupation x
sex table published at LSOA grain is at SOC-2020 **major-group** resolution
(10 groups), not sub-major. So we take the male share in major groups:
  * 5 = Skilled trades occupations   (contains the brief's construction &
        agricultural trades, but also food prep, textiles, etc. — coarse)
  * 8 = Process, plant & machine operatives
  * 9 = Elementary occupations        (incl. elementary construction)
relative to all males in employment (group 0 = Total).

Caveats carried downstream:
  * RESIDENCE-based (where high-risk workers live, not where they work).
  * Major-group coarseness — sub-major construction/agriculture cannot be
    isolated by sex at LSOA, so group 5 is a broad superset.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import Config
from ..fetch import nomis_csv_all
from ..io_utils import read_csv, require_file, validate_columns

REQUIRED = ["area_code", "male_high_risk_occ_pct"]

NOMIS_OCC_URL = "https://www.nomisweb.co.uk/api/v01/dataset/NM_2207_1.data.csv"
HIGH_RISK_GROUPS = [5, 8, 9]   # SOC-2020 major groups (see module docstring)


def _raw_path(cfg: Config) -> Path:
    base = cfg.path("synthetic_raw") if cfg.mode == "synthetic" else cfg.path("real_raw")
    return base / "occupation.csv"


def _build_real(cfg: Config, dest: Path) -> Path:
    if dest.exists():
        return dest
    print("[occupation] fetching Census 2021 occupation-by-sex (Nomis RM107) ...")
    codes = ",".join(str(c) for c in [0, *HIGH_RISK_GROUPS])
    params = {
        "geography": "TYPE151",          # all 2021 LSOAs (England & Wales)
        "c2021_occ_10": codes,
        "c_sex": "2",                    # male
        "measures": "20100",
        "select": "GEOGRAPHY_CODE,C2021_OCC_10,OBS_VALUE",
    }
    raw = nomis_csv_all(NOMIS_OCC_URL, params,
                        cache_path=cfg.path("real_raw") / "occupation_raw.csv")
    raw = raw.rename(columns={"GEOGRAPHY_CODE": "area_code", "OBS_VALUE": "value"})
    total = (raw[raw.C2021_OCC_10 == 0].set_index("area_code")["value"]
             .rename("male_in_employment"))
    high = (raw[raw.C2021_OCC_10.isin(HIGH_RISK_GROUPS)]
            .groupby("area_code")["value"].sum().rename("male_high_risk_occ_count"))
    df = pd.concat([total, high], axis=1).reset_index()
    df["male_high_risk_occ_pct"] = (
        df["male_high_risk_occ_count"] / df["male_in_employment"].clip(lower=1))
    dest.parent.mkdir(parents=True, exist_ok=True)
    df[["area_code", "male_high_risk_occ_pct", "male_high_risk_occ_count"]].to_csv(
        dest, index=False)
    return dest


def run(cfg: Config) -> pd.DataFrame:
    path = _raw_path(cfg)
    if cfg.mode == "real":
        _build_real(cfg, path)
    src = require_file(path, "occupation (Census 2021 SOC-2020, high-risk male share)",
                       "Nomis Census 2021 RM107 occupation by sex")
    df = validate_columns(read_csv(src), REQUIRED, "occupation")
    keep = [c for c in ["area_code", "male_high_risk_occ_pct", "male_high_risk_occ_count"]
            if c in df.columns]
    df = df[keep].copy()
    df = df.rename(columns={"male_high_risk_occ_pct": "occupation_proxy"})
    out = cfg.path("interim") / "fact_occupation.parquet"
    df.to_parquet(out, index=False)
    print(f"[occupation] {len(df)} areas -> {out.name}")
    return df
