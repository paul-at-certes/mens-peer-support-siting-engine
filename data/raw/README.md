# `data/raw/` — source data

In **`mode: real`** (the default) the pipeline **fetches the automatable sources
itself** and caches them under `data/raw/real/`. Nothing needs to be placed by
hand for England & Wales, with **one deliberate exception**: the AMC group
harvest. It is 713 requests against a small charity's website, so it never runs
on its own. Run it once, yourself:

```bash
python -m src.ingest.provision     # -> data/raw/amc_groups.json, a few minutes
```

It is throttled on purpose. A missing cache fails the pipeline loudly with that
command rather than silently re-scraping. Re-running the pipeline reuses the cache; delete a cached file to force a
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
| Occupation by sex, major groups | `ingest/occupation.py` | Nomis `NM_2207_1` (Census 2021 RM107) | LSOA 2021 | male shares across all 9 SOC-2020 major groups; **residence-based** |
| Occupation by sex, sub-major groups | `ingest/occupation.py` | ONS custom dataset API, `occupation_current_27a` × `sex` | **MSOA** 2021 | the within-major mix. Sub-major × sex is **68% disclosure-blocked at LSOA**, so it is taken at MSOA and applied to the LSOA major shares |
| Male suicide SMRs by occupation | `ingest/occupation.py` | ONS *Suicide by occupation: England, main data tables*, Table 3 (`.xls`) | SOC 2010 sub-major | the weights. England only, deaths registered **2011–2015**, ages 20–64; the 2016–2020 update was cancelled |
| Marital status by sex | `ingest/isolation.py` | Nomis `NM_2174_1` (Census 2021 RM074) | LSOA 2021 | male single/separated/divorced ÷ male 16+ |
| Household composition | `ingest/isolation.py` | Nomis `NM_2023_1` (Census 2021 TS003) | LSOA 2021 | one-person-household share (**not** sex-broken) |
| IMD 2019 (England) | `ingest/deprivation.py` | gov.uk File 7 (scores CSV) | LSOA **2011** | income + employment **scores** |
| WIMD 2019 (Wales) | `ingest/deprivation.py` | gov.wales domain **ranks** ODS | LSOA **2011** | income + employment **ranks** (different scale → within-nation percentile) |
| LSOA 2011 → 2021 crosswalk | `ingest/deprivation.py` | ONS Geo Portal `LSOA11_LSOA21_LAD22_EW_LU_v5` | — | brings IMD/WIMD (2011) onto 2021; split=copy, merge=average |
| Suicide by LA | `ingest/suicide_la.py` | Nomis API `NM_161_1` (ONS registrations), geography `TYPE434` | LA × cause × sex × age × year | **male, all ages, X60-X84 + Y10-Y34, 5-yr pooled, England & Wales**; denominator is `male_pop` from the population spine |
| Car or van availability | `ingest/car_access.py` | Nomis `NM_2063_1` (Census 2021 TS045) | LSOA 2021 | no-car **households** ÷ all households; **descriptive context only — never enters a score** |
| Rural-Urban Classification 2021 | `ingest/remoteness.py` | ONS Geo Portal `LSOA21_RUC21_EW_LU` (ArcGIS REST) | LSOA 2021 | settlement size × distance to a major town or city; the `*F1` codes are "remote". **Descriptive context only — never enters a score**; it decides which areas the remoteness *view* re-ranks |
| Postcode → LSOA21 | `spikes/group_need_concordance.py` | ONS Geo Portal `ONS_Postcode_Directory_(May_2026)_for_the_United_Kingdom_(Hosted_Table)` (ArcGIS REST) | postcode | **not a pipeline input** — used only by the concordance spike to place group venues in a small area. Cached to `real/group_postcode_lsoa.csv`. The `pcds` key needs exactly one space before the last three characters; the AMC listing is hand-entered and ~2% arrives without it |
| AMC group locations | `ingest/provision.py` | Andy's Man Club WP Store Locator (`admin-ajax.php`) | point | grid-harvested + deduped (~360 groups); **run by hand, throttled** — see above |

## Manual / not-yet-automated sources

- **Scotland & Northern Ireland** — out of scope for v1 (separate deprivation
  indices, censuses and suicide bodies). Stubs in
  `src/ingest/scotland_ni_stubs.py`.
- **Working-age suicide counts** — not obtainable at LA level. Nomis zeroes any
  cell below 5; at LA × cause × 5-year-age-band × year granularity that loses
  ~52% of deaths (measured: no value of 1-4 appears anywhere in 33,900 cells).
  All ages recovers 96.6%, so the adapter uses all ages. The ONS *Suicides by
  local authority* workbook does not help — it is persons-only, with no sex
  breakdown in either count table.
- **OHID Fingertips 41001** — the previous source; England-only, age 10+, 3-year
  pooled. Retained as an optional cross-check on the LA signal, per the brief.
- **Public transport travel time** — measured, but deliberately **not scored**.
  Travel time stays car-only. See
  [`docs/adr/0002-public-transport-feasibility-spike.md`](../../docs/adr/0002-public-transport-feasibility-spike.md)
  and re-derive with `python spikes/pt_evening_access.py`.
  What the spike established: the **Bus Open Data Service** publishes GTFS for
  all of GB at `https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/all`
  (~1.4GB, regenerated daily, **no API key**), and it is enough to answer whether
  a man can reach a Monday-evening session by 19:00 and get home after 21:00.
  Two reasons it does not enter the score. **BODS is a bus feed** — nationally 3
  rail routes and 57 tram, so National Rail and Nottingham's NET tram are both
  absent, which understates rural access. And the answer swings on the
  *acceptable-journey* rules rather than on the data: Mansfield runs 0% to 94%
  round-trip-feasible depending on how long a journey you assume a man will
  make. `ingest/car_access.py` remains the honest partial answer — it says where
  the drive time is least worth trusting without pretending to model the
  alternative. Note the spike also killed its stated future use: no-car share
  correlates **+0.66** with bus feasibility, so it is not usable as a blend
  weight.
- If you ever switch to manually-downloaded files, drop them in `data/raw/real/`
  under the filename the adapter expects (it prints the path on failure) and
  record the URL + vintage here.
