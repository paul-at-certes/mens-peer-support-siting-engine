"""Ingest the ONS Rural-Urban Classification -> fact_remoteness (by area_code).

DESCRIPTIVE CONTEXT ONLY. Nothing here enters need_index, supply_index,
priority_score, the factor breakdown, the tiers or the sensitivity analysis. It
follows the car_access precedent exactly: the column is attached in score.py
AFTER every score is settled, outside prepare_components(), so no weighting
scheme, tier or sensitivity draw can reach it even by accident.

Why it exists. The occupation factor carries real independent information (57.2%
of it is not explained by deprivation) and the ranking structurally cannot use
it: the areas where occupation says most sit at the 96th percentile on
occupation and the 22nd on deprivation AND isolation, so the two of them outvote
it nearly two to one. Those areas are overwhelmingly remote. This lets the map
rank WITHIN the remote classes, so they can be seen against each other rather
than against a country they cannot win in. It re-ranks a subset. It re-scores
nothing.

THE AXIS IS REMOTENESS, NOT RURALITY. RUC21 crosses settlement size with
distance to a major town or city, and measured on this data the signal sits
entirely in the "Further" half:

    class                              occ   dep   iso   drive  median rank
    Smaller rural: Further            0.76  0.32  0.21    34m        9,438
    Smaller rural: Nearer             0.35  0.21  0.16    20m       23,043
    Larger rural: Further             0.79  0.50  0.46    29m        5,913
    Larger rural: Nearer              0.45  0.34  0.34    17m       19,296
    Urban: Further                    0.69  0.59  0.56    25m       10,509
    Urban: Nearer                     0.48  0.55  0.56    12m       18,838

"Nearer" rural areas have LOW occupational risk, and remote URBAN areas carry
the highest deprivation of any class. So the view cuts on the *F1 codes, not on
Urban_rural_flag — a rural-only cut would drop 2,451 remote urban LSOAs that
belong in the same conversation.

Real source: ONS Open Geography Portal, LSOA21_RUC21_EW_LU (ArcGIS REST),
published on LSOA 2021 for England and Wales, so it joins straight onto the
spine with no crosswalk. Fields LSOA21CD, RUC21CD, RUC21NM, Urban_rural_flag.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import Config
from ..fetch import arcgis_query_all
from ..io_utils import read_csv, require_file, validate_columns

REQUIRED = ["area_code", "ruc21_code", "ruc21_name", "urban_rural_flag"]

_ARCGIS = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
RUC_LAYER = f"{_ARCGIS}/LSOA21_RUC21_EW_LU/FeatureServer/0"

# The six RUC21 classes and the counts ONS publishes for England & Wales. The
# extract is checked against these rather than against a row count alone: an
# ArcGIS layer that pages badly returns a healthy-looking number of rows.
EXPECTED_CLASSES = {
    "UN1": 27_106,   # Urban: Nearer to a major town or city
    "UF1": 2_451,    # Urban: Further from a major town or city
    "RLN1": 2_127,   # Larger rural: Nearer
    "RSN1": 1_735,   # Smaller rural: Nearer
    "RSF1": 1_232,   # Smaller rural: Further
    "RLF1": 1_021,   # Larger rural: Further
}
EXPECTED_AREAS = 35_672          # LSOAs in England & Wales, 2021

# Remoteness is the "Further from a major town or city" half of every settlement
# size. Keyed off the code, not the name, because the published names are long
# and have been reworded between vintages.
REMOTE_CODES = ("UF1", "RSF1", "RLF1")

# Short labels for the map legend and the PDF. The published names run to eight
# words and do not fit a table cell.
SHORT_LABEL = {
    "UN1": "Urban, nearer", "UF1": "Urban, further",
    "RLN1": "Larger rural, nearer", "RLF1": "Larger rural, further",
    "RSN1": "Smaller rural, nearer", "RSF1": "Smaller rural, further",
}


def _raw_path(cfg: Config) -> Path:
    base = cfg.path("synthetic_raw") if cfg.mode == "synthetic" else cfg.path("real_raw")
    return base / "remoteness.csv"


def _check_extract(df: pd.DataFrame) -> None:
    """Fail loudly if the extract is short or carries an unfamiliar class."""
    if len(df) < EXPECTED_AREAS:
        raise ValueError(
            f"remoteness: only {len(df):,} areas returned, expected {EXPECTED_AREAS:,}. "
            f"arcgis_query_all pages at page_size rows and needs a stable order_by — "
            f"check the paginator ran to the end.")
    unknown = set(df["ruc21_code"]) - set(EXPECTED_CLASSES)
    if unknown:
        raise ValueError(
            f"remoteness: unrecognised RUC21 class code(s) {sorted(unknown)}. The "
            f"classification has been revised; re-read the codes before trusting the "
            f"remote/not-remote split, which is keyed off them.")
    counts = df["ruc21_code"].value_counts().to_dict()
    print(f"[remoteness] extract check: {len(df):,} areas, "
          + ", ".join(f"{k} {counts.get(k, 0):,}" for k in EXPECTED_CLASSES))


def _build_real(cfg: Config, dest: Path) -> Path:
    if dest.exists():
        return dest
    print("[remoteness] fetching Rural-Urban Classification 2021 (ONS Geo Portal) ...")
    raw = arcgis_query_all(
        RUC_LAYER, out_fields="LSOA21CD,RUC21CD,RUC21NM,Urban_rural_flag",
        order_by="LSOA21CD", page_size=2000,
    ).rename(columns={"LSOA21CD": "area_code", "RUC21CD": "ruc21_code",
                      "RUC21NM": "ruc21_name", "Urban_rural_flag": "urban_rural_flag"})
    df = raw[REQUIRED].drop_duplicates(subset="area_code")
    _check_extract(df)
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(dest, index=False)
    return dest


def run(cfg: Config) -> pd.DataFrame:
    path = _raw_path(cfg)
    if cfg.mode == "real":
        _build_real(cfg, path)
    src = require_file(path, "rural-urban classification (RUC21)",
                       "ONS Open Geography Portal, LSOA21_RUC21_EW_LU")
    df = validate_columns(read_csv(src), REQUIRED, "remoteness")[REQUIRED].copy()

    df["is_remote"] = df["ruc21_code"].isin(REMOTE_CODES)
    df["ruc21_label"] = df["ruc21_code"].map(SHORT_LABEL).fillna(df["ruc21_name"])

    # Every area in the spine must carry a class. A partial join would quietly
    # shrink the remoteness view rather than fail, and an area with no class
    # would read as "not remote", which is a claim we would not have evidence for.
    spine = pd.read_parquet(cfg.path("interim") / "dim_geography.parquet")
    missing = set(spine["area_code"]) - set(df["area_code"])
    if missing:
        raise ValueError(
            f"remoteness: {len(missing):,} of {len(spine):,} spine areas have no RUC21 "
            f"class (e.g. {sorted(missing)[:3]}). RUC21 is published on LSOA 2021, the "
            f"same geography as the spine, so this should be an exact join — a gap "
            f"means one side is on a different vintage.")

    out = cfg.path("interim") / "fact_remoteness.parquet"
    df.to_parquet(out, index=False)
    n_remote = int(df["is_remote"].sum())
    print(f"[remoteness] {len(df):,} areas -> {out.name} "
          f"({n_remote:,} remote, {n_remote / len(df):.1%}; "
          f"{int((df.urban_rural_flag.astype(str).str.startswith('R')).sum()):,} rural)")
    return df
