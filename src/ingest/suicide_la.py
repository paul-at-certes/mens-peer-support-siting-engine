"""Ingest the LA-level suicide signal -> fact_suicide_la.

Suicide deaths are only reliably published at **Local Authority** level (small
numbers + disclosure control below that, plus a ~200-270 day registration lag).
This is the ONLY place the outcome signal enters, and it stays at LA grain — it
is never joined down to a fabricated small-area rate.

Real source: OHID Fingertips API, indicator 41001 ("Suicide rate", source ONS),
which exposes sex-specific **Count** and **Denominator** per LA — exactly what
the Poisson/NB calibration needs (count outcome, population offset).

Important real-data caveats (documented and surfaced in the UI):
  * **England only.** Fingertips 41001 has no Welsh rows, so Welsh LSOAs carry a
    neutral suicide term and the calibration weights are England-learned (then
    applied to both nations' proxies — exactly the two-level design).
  * **Age 10+,** not strictly 16-64 working-age — the narrower band isn't
    offered by this indicator.
  * **3-year pooled** (Fingertips' rolling window), not 5 — we take the latest
    pooled period.
  * Lower-tier LA (area type 501) to match the spine's LAD codes.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import Config
from ..fetch import download_to
from ..io_utils import read_csv, require_file, validate_columns

REQUIRED = ["la_code", "deaths", "population"]

FINGERTIPS_URL = (
    "https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id"
    "?indicator_ids=41001&child_area_type_id=501&parent_area_type_id=15"
)
ENGLAND_AGG = "E92000001"   # the national aggregate row — drop it


def _raw_path(cfg: Config) -> Path:
    base = cfg.path("synthetic_raw") if cfg.mode == "synthetic" else cfg.path("real_raw")
    return base / "suicide_la.csv"


def _build_real(cfg: Config, dest: Path) -> Path:
    if dest.exists():
        return dest
    print("[suicide_la] fetching male suicide counts by LA (OHID Fingertips 41001) ...")
    raw_csv = download_to(FINGERTIPS_URL, cfg.path("real_raw") / "fingertips_41001.csv")
    df = pd.read_csv(raw_csv)
    df = df[(df["Sex"] == "Male") & (df["Area Code"] != ENGLAND_AGG)].copy()
    # Latest pooled period (max sortable), e.g. "2022 - 24".
    latest = df["Time period Sortable"].max()
    period_label = df.loc[df["Time period Sortable"] == latest, "Time period"].iloc[0]
    df = df[df["Time period Sortable"] == latest]
    out = df.rename(columns={
        "Area Code": "la_code", "Area Name": "la_name",
        "Count": "deaths", "Denominator": "population",
    })[["la_code", "la_name", "deaths", "population"]].copy()
    out = out.dropna(subset=["deaths", "population"])
    out["years_pooled"] = period_label
    out["sex"] = "M"
    out["age_band"] = "10+"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dest, index=False)
    return dest


def run(cfg: Config) -> pd.DataFrame:
    path = _raw_path(cfg)
    if cfg.mode == "real":
        _build_real(cfg, path)
    src = require_file(path, "suicide by local authority (male, OHID Fingertips 41001)",
                       "OHID Fingertips API indicator 41001 (England) + NRS/NISRA/StatsWales")
    df = validate_columns(read_csv(src), REQUIRED, "suicide_la")
    # Guardrail: this is aggregate count data, not person-level records.
    if (df["deaths"] <= 0).all():
        raise ValueError("[suicide_la] all-zero deaths column — check the source file.")
    df = df.copy()
    df["rate_per_100k"] = 100_000 * df["deaths"] / df["population"].clip(lower=1)
    out = cfg.path("interim") / "fact_suicide_la.parquet"
    df.to_parquet(out, index=False)
    print(f"[suicide_la] {len(df)} LAs, total pooled male deaths={int(df['deaths'].sum())} "
          f"-> {out.name}")
    return df
