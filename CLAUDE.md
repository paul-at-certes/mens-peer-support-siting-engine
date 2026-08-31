# Claude Code Brief — Men's Peer-Support Siting Engine (v1)

*Paste this as your opening instruction to Claude Code, and/or save it in the repo root as `CLAUDE.md` so it persists as context. A fuller methodology write-up should sit alongside it at `docs/design.md` — drop the design note in there and tell Claude Code to read it.*

---

## Mission

Build a v1 data pipeline + map that ranks UK small areas by **unmet need for a men's peer-support group** (think Andy's Man Club), to help prioritise where to open new groups. It combines a calibrated **risk surface** (deprivation, male high-risk occupations, isolation, plus a Local-Authority suicide signal) with a **supply surface** (travel time to existing groups) to produce a ranked, mapped shortlist with a transparent per-area factor breakdown.

This is a public-health **resource-allocation** tool on **aggregate open data only**. It must never score, identify, or target individuals.

---

## Locked technical decisions (do not re-litigate in v1)

- **Language:** Python 3.11+.
- **Analytical store:** DuckDB over Parquet files. No server, no cloud warehouse.
- **Transforms:** `pandas` + `geopandas`; organise as small, ordered, independently-runnable modules (dbt-style mindset, but plain Python is fine).
- **Routing / travel time:** abstract behind a `TravelTimeProvider` interface with two implementations — (a) a **straight-line/haversine stub** (default, so the pipeline runs with zero external dependencies) and (b) a **hosted API or OSRM/ORS** implementation wired in later. Build against the stub first.
- **Front end:** **Streamlit** for v1 (fast to build, easy to inspect factor breakdowns and toggle views during development). Keep the map layer swappable; a static MapLibre + PMTiles build is a later option, not now.
- **Config:** a single `config.yaml` for paths, year vintages, weights, catchment thresholds.
- **Reproducibility:** every step reads from / writes to versioned Parquet in `data/`; no hidden state.

---

## The one hard constraint — read this before writing any join

Suicide data only exists reliably at **Local Authority** level (small numbers + disclosure control + ~200–270 day registration lag). Deprivation, occupation and isolation exist at **LSOA / small-area** level. **Do not invent LSOA-level suicide rates.**

Instead, implement the two-level approach:

1. **Check at LA level** — aggregate the small-area proxies up to LA, fit an interpretable model (Poisson or negative-binomial regression of pooled male suicide *counts* on the proxies, at-risk population as offset). The coefficients **check** the declared proxy weights; they do not set them.

   > **Amended 2026-08-31 — see `docs/adr/0001-calibration-as-veto.md`.** This step originally read "the coefficients become the proxy weights, with confidence intervals". On the real data that does not hold: with ~292 LAs and three mutually collinear proxies the model does not identify the weights (deprivation comes out significantly *protective* in the multivariable fit), and equal weights disagrees with each fitted scheme about as much as they disagree with each other — while the choice still moves up to 12 of the top 20 areas. So the weights are a **declared prior** in `config.yaml`, and calibration **vetoes** any the data contradicts. The claim is now "sanity-checked at LA level", not "calibrated at LA level". Everything else about the two-level design is unchanged: **still do not invent LSOA-level suicide rates.**
2. **Apply at small-area level** — use those weights on the small-area proxies to produce a latent `need_index` where suicide data doesn't exist.
3. **Subtract supply** at small-area level to get `priority_score`.

Also: England (IMD), Wales (WIMD) and Scotland (SIMD) deprivation indices are **not comparable across borders**, and the nations run separate censuses and suicide-statistics bodies. **Rank/normalise within each nation**, present the UK view by percentile. For v1 it is acceptable to ship **England & Wales first** and stub the Scotland/NI adapters behind the same interface.

---

## Repo structure to scaffold

```
siting-engine/
  README.md
  CLAUDE.md                 # this brief
  config.yaml
  pyproject.toml            # deps: duckdb, pandas, geopandas, statsmodels, pyyaml, streamlit, requests
  data/
    raw/                    # manually-downloaded source files land here (git-ignored)
    interim/                # cleaned Parquet
    output/                 # fact_score.parquet, scored GeoJSON
  src/
    geography.py            # spine: dim_geography, dim_population, lookups
    ingest/
      deprivation.py
      occupation.py
      isolation.py
      suicide_la.py
      provision.py
    travel_time.py          # TravelTimeProvider interface + haversine stub + API impl
    accessibility.py        # fact_accessibility from provision + travel time
    calibrate.py            # LA-level regression -> VETO on the declared weights (+ CIs)
    caveats.py              # single source of the caveat/assurance copy (map + PDF)
    score.py                # need_index, supply_index, priority_score, factor_breakdown
    pipeline.py             # runs the whole thing end-to-end
  app/
    streamlit_app.py        # entry point: routes the pages below
    views/priority_map.py   # map + two views + per-area breakdown
    views/guide.py          # plain-English guide for non-technical readers
  docs/
    design.md               # full methodology (paste the design note here)
  tests/
    ...
```

---

## Build sequence (each step must leave the repo runnable)

Build a **walking skeleton first**: get `pipeline.py` running end-to-end on a tiny **sample/synthetic dataset** (a handful of LSOAs) with the haversine stub, producing a `fact_score.parquet` and a working Streamlit map. *Then* swap in real data source by source. Do not build all ingestion before anything runs.

1. **Spine + population** — `geography.py`: build `dim_geography` (small-area → LA → region → nation, with centroids) and `dim_population` (male working-age 16–64). Ship with a sample fixture so everything downstream can run immediately.
2. **Risk proxies** — `ingest/deprivation.py`, `occupation.py`, `isolation.py` → `data/interim/*.parquet`, all keyed to `area_code`. Income + employment deprivation domains specifically; male share in high-risk SOC-2020 groups (construction trades, elementary construction, agriculture, process/plant); male single/separated and living-alone proxies.
3. **Suicide signal + calibration check** — `ingest/suicide_la.py` (male, working age, 5-year pooled rate) then `calibrate.py` (the LA-level regression → a veto on the weights declared in `config.yaml`). Print the fit and CIs to the console alongside the declared weights, and persist the diagnostic to `weights.json`. The offset must be the outcome dataset's **own denominator** — pairing an age-10+ numerator with a 16–64 denominator biases the fit with local age structure. This step is **non-blocking**: the outcome source is England-only and Wales must stay rankable without it.
4. **Provision + accessibility** — `ingest/provision.py` (geocode group locations) + `travel_time.py` (haversine stub first) + `accessibility.py` (nearest-group minutes, groups within 30-min catchment).
5. **Score + breakdown** — `score.py`: `need_index = Σ wᵢ·proxyᵢ` (within-nation percentiles), `supply_index`, `priority_score = need_index × (1 − supply_index)`. Persist a per-area `factor_breakdown` (JSON column) so every score is explainable. Output two ranked views:
   - **Per-capita** (`priority_score`) — acute pockets.
   - **Reach** (`priority_score × male_working_age_pop`) — most men touched per new group.
6. **Map** — `app/streamlit_app.py`: choropleth of `priority_score`, toggle between the two views, click an area to see its factor breakdown and data-vintage caveats, overlay existing group points.
7. **Swap in real routing** — implement the API/OSRM `TravelTimeProvider`; precompute the matrix (provision changes rarely) and cache it.

---

## Data sources

Most UK statistical data must be **downloaded manually** from government portals (URLs and file layouts change, and some sit behind query builders). **Do not assume live fetching works.** Scaffold each ingestion module to read a documented file from `data/raw/`, validate its schema, and fail loudly with a clear "place file X here" message if absent. Where a clean API exists, you may automate it.

| Source | Grain | Join key | Access | Notes |
|---|---|---|---|---|
| ONS *Suicides by local authority* (+ NRS / NISRA) | LA × year × sex × age | GSS `la_code` | manual download (Excel) | male, working age, pool 5 years |
| IMD / WIMD / SIMD | LSOA / Data Zone | `area_code` | manual download | income + employment domains; within-nation percentiles |
| Census 2021 occupation (Nomis) | OA / LSOA, SOC-2020 | `area_code` | Nomis API automatable, else download | high-risk male occupation share |
| Census 2021 household/relationship | OA / LSOA | `area_code` | Nomis API / download | isolation proxies |
| ONS mid-year population estimates | LSOA / LA | `area_code` | download / ONS API | denominators + reach multiplier |
| ONS Postcode Directory / lookups | — | — | download | small-area → LA → region → nation, centroids |
| OHID Fingertips suicide indicators | LA / ICB | `la_code` | **API automatable** | optional cross-check on the LA signal |
| AMC + peer group locations | point | geocoded | scrape/compile public listings | geocode addresses / What3Words → lat/lon |

Document every file's expected name, source URL and vintage in `data/raw/README.md` as you go.

---

## Definition of done (v1)

- `python -m src.pipeline` runs clean from `data/raw/` to `data/output/fact_score.parquet` for England & Wales.
- `streamlit run app/streamlit_app.py` shows the ranked map, both views, per-area factor breakdowns, and existing groups.
- Scoring weights are **declared in `config.yaml`** and defended there; the LA-level fit is printed with confidence intervals, persisted as a diagnostic, and **vetoes** any weight the data contradicts.
- Shortlist stability is tested on **three axes** — alternative weightings, the CI envelope, and the supply constants — with the verdict surfaced on the map face and in the PDF. Thresholds are set from what the number means for the decision, never tuned until the data passes.
- Every score decomposes into named contributing factors.
- README documents how to obtain each dataset, run the pipeline, and launch the app.
- Scotland/NI adapters exist as clearly-marked stubs behind the same interfaces.

---

## Guardrails (enforce in code and UI)

- **Aggregate only.** No table, output, or log may contain individual-level records. If a source row could identify a person, stop and flag it.
- **Latent need, not prediction.** Name things accordingly (`need_index`, not `risk_of_suicide`). The tool allocates resource; it does not forecast deaths or rank people.
- **Show uncertainty.** Surface small-number and registration-lag caveats and data vintages on the map face.
- **Within-nation normalisation only** for deprivation; never compare raw IMD/WIMD/SIMD across borders.
- **Human-in-the-loop output.** The deliverable is a *shortlist for local judgement*, not an automated siting decision — make that explicit in the UI copy.

---

## How to start

> "Read `CLAUDE.md` and `docs/design.md`. Scaffold the repo structure described. Then build the walking skeleton: a synthetic 10-LSOA fixture, the spine, a stub for each ingestion step, the haversine `TravelTimeProvider`, the scoring step, and the Streamlit map — so `python -m src.pipeline` and `streamlit run app/streamlit_app.py` both work end-to-end on fake data. Show me the running skeleton before we touch any real dataset."
