"""Ingest deprivation -> fact_deprivation (keyed to area_code = LSOA 2021).

Uses the IMD **income** and **employment** domains specifically (not the
headline index), because working-age male suicide tracks economic insecurity
more tightly than the composite.

Three real-data complications, all handled here:

1. **Scores vs ranks.** England (IMD 2019, File 7) publishes domain *scores*
   (rates, ~0-1, higher = more deprived). Wales (WIMD 2019) publishes domain
   *ranks* (1 = most deprived). They are NOT numerically comparable. We convert
   each nation to a WITHIN-NATION deprivation percentile (0-1, higher = more
   deprived) so they share a scale and direction without ever comparing raw
   cross-border values.

2. **Boundary vintage.** Both indices are on **LSOA 2011** boundaries, but the
   rest of the pipeline is **LSOA 2021**. We crosswalk via the ONS
   LSOA11->LSOA21 best-fit lookup (split = copy down, merge = average).

3. **Two nations, two sources.** England CSV + Wales ODS, stitched.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import Config
from ..fetch import arcgis_query_all, download_to
from ..io_utils import read_csv, require_file, validate_columns

REQUIRED = ["area_code", "deprivation_proxy"]

# England IMD 2019 — File 7 (scores, ranks, deciles), LSOA 2011.
IMD_ENG_URL = ("https://assets.publishing.service.gov.uk/media/5dc407b440f0b6379a7acc8d/"
               "File_7_-_All_IoD2019_Scores__Ranks__Deciles_and_Population_Denominators_3.csv")
# Wales WIMD 2019 — domain ranks by small area (ODS), LSOA 2011.
WIMD_URL = ("https://www.gov.wales/sites/default/files/statistics-and-research/2022-02/"
            "welsh-index-multiple-deprivation-2019-index-and-domain-ranks-by-small-area.ods")
# ONS LSOA 2011 -> LSOA 2021 best-fit crosswalk.
_ARCGIS = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
XWALK_LAYER = f"{_ARCGIS}/LSOA11_LSOA21_LAD22_EW_LU_v5/FeatureServer/0"


def _raw_path(cfg: Config) -> Path:
    base = cfg.path("synthetic_raw") if cfg.mode == "synthetic" else cfg.path("real_raw")
    return base / "deprivation.csv"


def _crosswalk(cfg: Config) -> pd.DataFrame:
    cache = cfg.path("real_raw") / "lsoa11_to_lsoa21.csv"
    if cache.exists():
        return pd.read_csv(cache)
    print("[deprivation] fetching LSOA11->LSOA21 crosswalk (ONS Geo Portal) ...")
    xw = arcgis_query_all(XWALK_LAYER, out_fields="LSOA11CD,LSOA21CD,CHGIND",
                          order_by="LSOA11CD", page_size=1000)
    xw[["LSOA11CD", "LSOA21CD", "CHGIND"]].to_csv(cache, index=False)
    return xw


def _within_nation_dep_percentile_2011(cfg: Config) -> pd.DataFrame:
    """Per LSOA-2011 deprivation percentile (0-1, higher = more deprived),
    computed within nation from income+employment domains."""
    real_raw = cfg.path("real_raw")

    # England: scores (rates) — percentile-rank directly (higher = more deprived).
    eng_csv = download_to(IMD_ENG_URL, real_raw / "imd2019_england_file7.csv")
    eng = pd.read_csv(eng_csv)
    eng = eng.rename(columns={
        "LSOA code (2011)": "LSOA11CD",
        "Income Score (rate)": "income",
        "Employment Score (rate)": "employment",
    })[["LSOA11CD", "income", "employment"]]
    eng["income_pct"] = eng["income"].rank(pct=True)
    eng["employment_pct"] = eng["employment"].rank(pct=True)

    # Wales: ranks (1 = most deprived) — invert to a 0-1 deprivation percentile.
    wimd_ods = download_to(WIMD_URL, real_raw / "wimd2019_wales_ranks.ods")
    wal = pd.read_excel(wimd_ods, sheet_name="WIMD_2019_ranks", engine="odf", header=2)
    wal.columns = wal.columns.str.strip()   # source headers carry trailing spaces
    wal = wal.rename(columns={"LSOA code": "LSOA11CD",
                              "Income": "income_rank", "Employment": "employment_rank"})
    wal = wal[["LSOA11CD", "income_rank", "employment_rank"]].dropna(subset=["LSOA11CD"])
    n_w = len(wal)
    wal["income_pct"] = 1 - (wal["income_rank"] - 1) / (n_w - 1)
    wal["employment_pct"] = 1 - (wal["employment_rank"] - 1) / (n_w - 1)

    both = pd.concat([eng[["LSOA11CD", "income_pct", "employment_pct"]],
                      wal[["LSOA11CD", "income_pct", "employment_pct"]]], ignore_index=True)
    both["deprivation_proxy"] = both[["income_pct", "employment_pct"]].mean(axis=1)
    return both[["LSOA11CD", "deprivation_proxy"]]


def _build_real(cfg: Config, dest: Path) -> Path:
    if dest.exists():
        return dest
    dep11 = _within_nation_dep_percentile_2011(cfg)
    xw = _crosswalk(cfg)
    # Join 2011 deprivation to the crosswalk, then collapse to LSOA 2021. Splits
    # copy the 2011 value down to each child; merges average the constituents.
    merged = xw.merge(dep11, on="LSOA11CD", how="left")
    out = (merged.groupby("LSOA21CD", as_index=False)["deprivation_proxy"].mean()
           .rename(columns={"LSOA21CD": "area_code"}))
    out = out.dropna(subset=["deprivation_proxy"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dest, index=False)
    return dest


def run(cfg: Config) -> pd.DataFrame:
    path = _raw_path(cfg)
    if cfg.mode == "real":
        _build_real(cfg, path)
    src = require_file(path, "deprivation (IMD/WIMD income + employment domains)",
                       "gov.uk IMD 2019 File 7 / gov.wales WIMD 2019 ranks")
    df = read_csv(src)
    # Synthetic fixtures carry the raw domains; compute the proxy if needed.
    if "deprivation_proxy" not in df.columns:
        validate_columns(df, ["area_code", "income_domain", "employment_domain"],
                         "deprivation")
        df["deprivation_proxy"] = df[["income_domain", "employment_domain"]].mean(axis=1)
    validate_columns(df, REQUIRED, "deprivation")
    out = cfg.path("interim") / "fact_deprivation.parquet"
    df[["area_code", "deprivation_proxy"]].to_parquet(out, index=False)
    print(f"[deprivation] {len(df)} areas -> {out.name}")
    return df
