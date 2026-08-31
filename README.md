# Men's Peer-Support Siting Engine — v1

Ranks UK small areas by **unmet need for a men's peer-support group** (think
Andy's Man Club) to help prioritise where to open new groups. It combines a
calibrated **need surface** (deprivation, high-risk male occupation, isolation,
plus a Local-Authority suicide signal) with a **supply surface** (travel time to
existing groups) to produce a ranked, mapped shortlist with a transparent
per-area factor breakdown.

> This is a public-health **resource-allocation** tool on **aggregate open data
> only**. It never scores, identifies, or targets individuals. The deliverable is
> a **shortlist for local judgement**, not an automated siting decision.

See [`CLAUDE.md`](CLAUDE.md) for the operational brief and
[`docs/design.md`](docs/design.md) for the full methodology (the *why*).

---

## What's built (v1 walking skeleton)

The whole pipeline runs end-to-end on a **synthetic fixture** with zero external
dependencies, so you can see it working before wiring in any real dataset. Then
real sources swap in one at a time behind the same interfaces.

- **Two-level model** — proxy weights are *learned* at Local-Authority level
  (Poisson/NegBin regression of pooled male working-age suicide counts on the
  aggregated proxies, population offset), then *applied* to small-area proxies.
  No small-area suicide rate is ever fabricated.
- **Within-nation normalisation** — IMD/WIMD/SIMD aren't comparable across
  borders, so everything is percentile-ranked within nation. v1 ships
  **England & Wales**; Scotland/NI are documented stubs.
- **Two views** — *per-capita* (`priority_score`) for acute pockets, and *reach*
  (`priority_score × male_working_age_pop`) for the most men reached per group.
- **Fully explainable** — every area carries a `factor_breakdown` JSON.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .   # or: pip install numpy pandas pyarrow statsmodels pyyaml requests odfpy streamlit pydeck

# 1) Run the pipeline.
#    mode: real (default) fetches + caches the live open data for England &
#    Wales (~35k LSOAs; first run pulls from ONS/Nomis/Fingertips/AMC, a few
#    minutes; subsequent runs use the cache). Set mode: synthetic in config.yaml
#    to run the instant offline fixture instead.
python -m src.pipeline
#    -> data/output/fact_score.parquet, fact_score.geojson, weights.json
#    -> prints calibration weights with confidence intervals

# 2) Launch the map
streamlit run app/streamlit_app.py

# 3) (Optional) Generate the shareable shortlist PDF
pip install -e ".[report]"     # one-off: adds reportlab (pure-Python, no system libs)
python -m src.report
#    -> data/output/amc_top20_report.pdf
```

### Shortlist report (`python -m src.report`)

A static, shareable **PDF** of the top-N areas for a new group — for handing to a
board or partner who won't run the app. It is a faithful *rendering* of
`fact_score.parquet` (no re-analysis), so it can never disagree with the map: the
framing, a ranked table, a plain-English reason per area (its dominant factors
straight from `factor_breakdown`), and the same caveats the app shows. Configure
it under `report:` in `config.yaml` — `top_n` (default 20) and `view` (`reach`,
the "most men reached per group" lens, or `per_capita` for acute pockets).
Robustness flags come from the per-capita shortlist, so reach-ranked areas
outside it show `—` with a footnote rather than a fabricated confidence score.

Run the tests with `pytest` (they always use the synthetic fixture — no network).

> **Python:** the brief targets 3.11+, but the code runs on **3.9+** (it uses
> `from __future__ import annotations`), so it installs on stock macOS Python.
> The core install is lightweight — `duckdb`/`geopandas` are an optional
> `[stack]` extra and the pipeline does not need them (centroids come
> pre-computed from ONS, so there are no spatial joins).

## Real data sources (mode: real, the default)

All England & Wales sources are fetched and cached automatically — see
[`data/raw/README.md`](data/raw/README.md) for the full table of endpoints and
caveats. In brief:

| Layer | Source |
|---|---|
| Spine + centroids | ONS Open Geography Portal (ArcGIS REST), LSOA 2021 |
| Population (male 16–64) | Nomis Census 2021 RM121 |
| Occupation | Nomis Census 2021 RM107 (SOC major groups 5/8/9, male) |
| Isolation | Nomis Census 2021 RM074 (marital) + TS003 (one-person households) |
| Deprivation | IMD 2019 (England scores) + WIMD 2019 (Wales ranks), LSOA 2011→2021 |
| Suicide signal | OHID Fingertips indicator 41001 (male, age 10+, England) |
| Provision | Andy's Man Club group finder (live harvest) |

Key real-data honesty notes (also surfaced on the map face):
- **Suicide is England-only and age 10+** → weights are England-calibrated;
  Wales scores on its proxies with a neutral suicide term.
- **Occupation is SOC major-group, residence-based**; **living-alone** is a
  one-person-household share (no sex-broken figure exists at LSOA).
- **Travel time defaults to the haversine stub** (straight-line over-states rural
  access). Real road routing via **self-hosted OSRM** is implemented and ready —
  see below.

### Real routing (OSRM)

The `OSRMTravelTimeProvider` (`src/travel_time.py`) is built and tested: it
computes the origin×destination matrix in chunks against an OSRM `/table`
service and caches it (keyed by a content hash) under
`accessibility.matrix_cache`, since provision changes rarely. To switch it on:

1. Stand up OSRM on a GB extract (one-off — you can tear it down once the matrix
   is cached). The class docstring has the exact `docker run` commands; the
   server needs `--max-table-size` ≥ `accessibility.osrm.max_table_size` (and >
   the group count).
2. Set `accessibility.provider: osrm` and `accessibility.osrm.base_url` in
   `config.yaml`, then re-run `python -m src.pipeline`.

Haversine stays the default so the pipeline runs with zero dependencies until
then. (ORS remains a stub behind the same interface.)

## Weighting & sensitivity

The proxy weights are *learned* at LA level, but the proxies are collinear
(deprivation, occupation and isolation all track disadvantage), so partial GLM
coefficients are fragile — `calibrate.py` fits **three** schemes and
`sensitivity.py` (design.md §7) tests how much the shortlist depends on the
choice:

| Scheme | Idea | Caveat |
|---|---|---|
| `multivariable` | partial coefficients of the 3-proxy GLM | collinearity zeroes **deprivation**; top-100 only ~35–47% overlaps the others |
| `univariate` *(default)* | each proxy weighted by its **own** association with suicide | keeps all three proxies; mild double-counting of correlated economic proxies |
| `composite` | merge deprivation+occupation into one disadvantage factor vs isolation | here the isolation coef flips negative, zeroing **isolation** |

**Finding:** within any scheme the shortlist is very stable (≈99% retention under
CI perturbation; **0** low-confidence areas), but the *choice of scheme* moves the
top-100 materially. We therefore default to **univariate** — the only scheme that
keeps all three theoretically-grounded proxies contributing — as a deliberate,
documented decision (set `scoring.weighting_scheme` in `config.yaml` to compare).
`sensitivity.json` records the per-area robustness and any low-confidence flags;
the app surfaces both.

## Repo layout

```
config.yaml              # all paths, vintages, weights, thresholds
src/
  config.py              # config + path resolution
  synthetic.py           # synthetic fixture generator (the skeleton's "raw" data)
  fetch.py               # cached HTTP + ArcGIS / Nomis paginators (real mode)
  io_utils.py            # loud failures + schema validation
  geography.py           # spine: dim_geography + dim_population
  ingest/                # deprivation, occupation, isolation, suicide_la, provision
                         #   + scotland_ni_stubs.py (documented stubs)
  travel_time.py         # TravelTimeProvider: haversine stub + OSRM/ORS stubs
  accessibility.py       # fact_accessibility (supply surface)
  calibrate.py           # LA-level GLM -> learned weights (+ CIs) -> weights.json
  score.py               # need_index, supply_index, priority_score, factor_breakdown
  pipeline.py            # runs the whole thing
app/streamlit_app.py     # map + two views + per-area breakdown + caveats
data/{raw,interim,output}/
tests/
```

## Data flow

```
raw (downloaded / synthetic)
  → geography.py        → dim_geography, dim_population
  → ingest/*            → fact_deprivation, fact_occupation, fact_isolation,
                          fact_suicide_la, dim_provision
  → calibrate.py        → weights.json   (LA-level regression)
  → accessibility.py    → fact_accessibility   (TravelTimeProvider)
  → score.py            → fact_score.parquet + .geojson   (need × (1 − supply))
  → streamlit_app.py    → map
```

## Guardrails (enforced in code & UI)

- **Aggregate only** — no individual-level record in any table, output, or log.
- **Latent need, not prediction** — named `need_index`, never `risk_of_suicide`.
- **Show uncertainty** — data vintages, registration-lag and small-number
  caveats surfaced on the map face.
- **Within-nation only** — never compare raw IMD/WIMD/SIMD across borders.
- **Human-in-the-loop** — the output is a shortlist; siting needs local judgement
  (venue, volunteers, partner appetite).
