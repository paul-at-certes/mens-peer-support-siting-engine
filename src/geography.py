"""Spine: dim_geography + dim_population.

The geographic spine every other table joins to. Small area (LSOA 2021) ->
Local Authority -> region -> nation, with population-weighted centroids; plus
male working-age (16-64) population for denominators and the reach multiplier.

Sources:
  * synthetic mode -> the fixture produced by ``src.synthetic``.
  * real mode      -> ONS Open Geography Portal (ArcGIS REST) for the spine and
                      Nomis Census 2021 table RM121 (sex by age) for population.
                      Both are fetched once and cached under data/raw/real/.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import Config
from .fetch import arcgis_query_all, nomis_csv_all
from .io_utils import read_csv, require_file, validate_columns

GEO_COLUMNS = [
    "area_code", "area_name", "la_code", "la_name",
    "region", "nation", "centroid_lon", "centroid_lat",
]
POP_COLUMNS = ["area_code", "total_pop", "male_pop", "male_working_age_pop", "year"]

# --- Real source endpoints (verified live) ---------------------------------
_ARCGIS = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
LOOKUP_LAYER = f"{_ARCGIS}/LSOA21_BUA22_LAD22_RGN22_EW_LU_v2/FeatureServer/0"
PWC_LAYER = f"{_ARCGIS}/LSOA_PopCentroids_EW_2021_V4/FeatureServer/0"

NOMIS_POP_URL = "https://www.nomisweb.co.uk/api/v01/dataset/NM_2221_1.data.csv"
EW_LSOA_GEOG = "2092957703TYPE151"          # England & Wales, 2021 LSOAs
AGE_16_64 = [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]   # C2021_AGE_24 working-age bands


def _raw_path(cfg: Config, fname: str) -> Path:
    base = cfg.path("synthetic_raw") if cfg.mode == "synthetic" else cfg.path("real_raw")
    return base / fname


# ---------------------------------------------------------------------------
# Real-data builders (cache CSVs into data/raw/real/ on first run)
# ---------------------------------------------------------------------------
def _build_real_geography_csv(cfg: Config, dest: Path) -> Path:
    if dest.exists():
        return dest
    print("[geography] fetching LSOA21 -> LAD -> region lookup (ONS Geo Portal) ...")
    lookup = arcgis_query_all(
        LOOKUP_LAYER,
        out_fields="LSOA21CD,LSOA21NM,LAD22CD,LAD22NM,RGN22CD,RGN22NM",
        order_by="LSOA21CD", page_size=1000,
    )
    print(f"[geography] ... {len(lookup)} LSOAs; fetching population-weighted centroids ...")
    cent = arcgis_query_all(
        PWC_LAYER, out_fields="LSOA21CD", order_by="LSOA21CD",
        page_size=2000, return_geometry=True, out_sr=4326,
    )
    cent["centroid_lon"] = cent["_geometry"].apply(lambda g: g["x"])
    cent["centroid_lat"] = cent["_geometry"].apply(lambda g: g["y"])

    df = lookup.merge(cent[["LSOA21CD", "centroid_lon", "centroid_lat"]],
                      on="LSOA21CD", how="inner")
    df["nation"] = df["LSOA21CD"].str[0]          # E01... -> E, W01... -> W
    out = df.rename(columns={
        "LSOA21CD": "area_code", "LSOA21NM": "area_name",
        "LAD22CD": "la_code", "LAD22NM": "la_name", "RGN22NM": "region",
    })[GEO_COLUMNS]
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dest, index=False)
    return dest


def _build_real_population_csv(cfg: Config, dest: Path) -> Path:
    if dest.exists():
        return dest
    print("[geography] fetching Census 2021 sex-by-age population (Nomis RM121) ...")
    age_codes = ",".join(str(c) for c in [0, *AGE_16_64])
    params = {
        "geography": EW_LSOA_GEOG,
        "c2021_age_24": age_codes,
        "c_sex": "0,2",            # 0 = all persons, 2 = male
        "measures": "20100",       # value (counts)
        "select": "GEOGRAPHY_CODE,C2021_AGE_24,C_SEX,OBS_VALUE",
    }
    raw = nomis_csv_all(NOMIS_POP_URL, params,
                        cache_path=cfg.path("real_raw") / "population_raw.csv")
    raw = raw.rename(columns={"GEOGRAPHY_CODE": "area_code", "OBS_VALUE": "value"})

    total = (raw[(raw.C_SEX == 0) & (raw.C2021_AGE_24 == 0)]
             .set_index("area_code")["value"].rename("total_pop"))
    male = (raw[(raw.C_SEX == 2) & (raw.C2021_AGE_24 == 0)]
            .set_index("area_code")["value"].rename("male_pop"))
    male_wa = (raw[(raw.C_SEX == 2) & (raw.C2021_AGE_24.isin(AGE_16_64))]
               .groupby("area_code")["value"].sum().rename("male_working_age_pop"))

    pop = pd.concat([total, male, male_wa], axis=1).reset_index()
    pop["year"] = 2021
    dest.parent.mkdir(parents=True, exist_ok=True)
    pop[POP_COLUMNS].to_csv(dest, index=False)
    return dest


# ---------------------------------------------------------------------------
# Build dim tables
# ---------------------------------------------------------------------------
def build_dim_geography(cfg: Config) -> pd.DataFrame:
    path = _raw_path(cfg, "geography.csv")
    if cfg.mode == "real":
        _build_real_geography_csv(cfg, path)
    src = require_file(path, "geography spine",
                       "ONS Open Geography Portal / Postcode Directory")
    df = validate_columns(read_csv(src), GEO_COLUMNS, "geography")
    df = df[df["nation"].isin(cfg.nations)].copy()
    df = df.drop_duplicates(subset="area_code").reset_index(drop=True)
    df.to_parquet(cfg.path("interim") / "dim_geography.parquet", index=False)
    return df


def build_dim_population(cfg: Config) -> pd.DataFrame:
    path = _raw_path(cfg, "population.csv")
    if cfg.mode == "real":
        _build_real_population_csv(cfg, path)
    src = require_file(path, "population estimates",
                       "ONS mid-year population estimates / Census 2021 (Nomis RM121)")
    df = validate_columns(read_csv(src), POP_COLUMNS, "population")
    df.to_parquet(cfg.path("interim") / "dim_population.parquet", index=False)
    return df


def run(cfg: Config) -> dict[str, pd.DataFrame]:
    geo = build_dim_geography(cfg)
    pop = build_dim_population(cfg)
    # Keep population to the areas present in the (nation-filtered) spine.
    pop = pop[pop["area_code"].isin(geo["area_code"])].reset_index(drop=True)
    pop.to_parquet(cfg.path("interim") / "dim_population.parquet", index=False)
    print(f"[geography] dim_geography: {len(geo)} areas across "
          f"{geo['la_code'].nunique()} LAs, nations={sorted(geo['nation'].unique())}")
    print(f"[geography] dim_population: {len(pop)} areas")
    return {"dim_geography": geo, "dim_population": pop}
