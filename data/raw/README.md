# `data/raw/` — source data

In **`mode: real`** (the default) the pipeline **fetches the automatable sources
itself** and caches them under `data/raw/real/` (and the AMC harvest at
`data/raw/amc_groups.json`). Nothing needs to be placed by hand for England &
Wales. Re-running the pipeline reuses the cache; delete a cached file to force a
refresh. In **`mode: synthetic`** a fake fixture is generated under
`data/raw/synthetic/` and none of this is used.

Each adapter still validates its source's schema and fails loudly with a "place
file X here" message if a (non-automatable) source is ever missing.

## Sources fetched automatically (England & Wales)

| Source | Adapter | Endpoint / file | Grain | Vintage & caveats |
|---|---|---|---|---|
| LSOA21 → LAD → Region lookup | `geography.py` | ONS Open Geography Portal `LSOA21_BUA22_LAD22_RGN22_EW_LU_v2` (ArcGIS REST) | LSOA 2021 | 35,672 LSOAs; LAD/RGN 2022 boundaries |
| LSOA21 pop-weighted centroids | `geography.py` | ONS Geo Portal `LSOA_PopCentroids_EW_2021_V4` | LSOA 2021 | lon/lat via `outSR=4326` |
| Population by sex & age | `geography.py` | Nomis `NM_2221_1` (Census 2021 RM121) | LSOA 2021 | male 16–64 summed from age bands 7–17 |
| Occupation by sex | `ingest/occupation.py` | Nomis `NM_2207_1` (Census 2021 RM107) | LSOA 2021 | **SOC major groups** 5/8/9 only (sub-major not sex-crossed at LSOA); **residence-based** |
| Marital status by sex | `ingest/isolation.py` | Nomis `NM_2174_1` (Census 2021 RM074) | LSOA 2021 | male single/separated/divorced ÷ male 16+ |
| Household composition | `ingest/isolation.py` | Nomis `NM_2023_1` (Census 2021 TS003) | LSOA 2021 | one-person-household share (**not** sex-broken) |
| IMD 2019 (England) | `ingest/deprivation.py` | gov.uk File 7 (scores CSV) | LSOA **2011** | income + employment **scores** |
| WIMD 2019 (Wales) | `ingest/deprivation.py` | gov.wales domain **ranks** ODS | LSOA **2011** | income + employment **ranks** (different scale → within-nation percentile) |
| LSOA 2011 → 2021 crosswalk | `ingest/deprivation.py` | ONS Geo Portal `LSOA11_LSOA21_LAD22_EW_LU_v5` | — | brings IMD/WIMD (2011) onto 2021; split=copy, merge=average |
| Suicide by LA | `ingest/suicide_la.py` | OHID Fingertips API indicator `41001`, area type 501 | LA × sex × period | **male, age 10+, 3-yr pooled, England-only**; `Count` + `Denominator` |
| AMC group locations | `ingest/provision.py` | Andy's Man Club WP Store Locator (`admin-ajax.php`) | point | grid-harvested + deduped (~360 groups) |

## Manual / not-yet-automated sources

- **Scotland & Northern Ireland** — out of scope for v1 (separate deprivation
  indices, censuses and suicide bodies). Stubs in
  `src/ingest/scotland_ni_stubs.py`.
- **Welsh suicide signal** — Fingertips 41001 is England-only; a StatsWales / ONS
  source would wire in behind `suicide_la.py`. Until then Wales scores on its
  proxies with a neutral suicide term.
- If you ever switch to manually-downloaded files, drop them in `data/raw/real/`
  under the filename the adapter expects (it prints the path on failure) and
  record the URL + vintage here.
