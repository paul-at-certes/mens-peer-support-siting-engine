# Men's Peer-Support Siting Engine — v1

Ranks UK small areas by **unmet need for a men's peer-support group** (think
Andy's Man Club) to help prioritise where to open new groups. It combines a
**need surface** (deprivation, high-risk male occupation, isolation, plus a
Local-Authority suicide signal) with a **supply surface** (travel time to
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

- **Two-level model** — proxy weights are **declared** in `config.yaml` and
  **checked** at Local-Authority level (Poisson/NegBin regression of pooled male
  suicide counts on the aggregated proxies, at-risk population offset), then
  applied to small-area proxies. The fit vetoes any weight the data contradicts;
  it does not supply one, because with ~292 LAs and three collinear proxies it
  cannot identify them ([ADR 0001](docs/adr/0001-calibration-as-veto.md)). No
  small-area suicide rate is ever fabricated.
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
#    Wales (~35k LSOAs; first run pulls from ONS/Nomis/AMC, a few
#    minutes; subsequent runs use the cache). Set mode: synthetic in config.yaml
#    to run the instant offline fixture instead.
python -m src.pipeline
#    -> data/output/fact_score.parquet, fact_score.geojson, weights.json
#    -> prints the declared weights, the LA-level veto verdict, and a
#       three-axis shortlist-stability report

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
| Suicide signal | Nomis NM_161_1 / ONS registrations (male, all ages, X60-X84 + Y10-Y34, 5-yr pooled, England & Wales) |
| Provision | Andy's Man Club group finder (live harvest) |

Key real-data honesty notes (also surfaced on the map face):
- **Suicide counts are male all-ages, England & Wales, 331 LAs.** Working age is
  not obtainable at LA level: the publisher zeroes any cell below 5, which at
  working-age-band granularity loses ~52% of deaths. All ages recovers 96.6% —
  measured, see [ADR 0001](docs/adr/0001-calibration-as-veto.md). The proxies are
  working-age measures, so the outcome is broader than the population targeted.
- **Occupation is SOC major-group, residence-based**; **living-alone** is a
  one-person-household share (no sex-broken figure exists at LSOA).
- **Travel time defaults to the haversine stub** (straight-line over-states rural
  access). Real road routing via **self-hosted OSRM** is implemented and ready —
  see below.

### Real routing (OSRM) — the shipped default

`accessibility.provider: osrm` is the default: travel times are **real road
driving times** on the GB network, not straight lines. The prepared graph is in
`osrm-data/` (built from a Geofabrik `great-britain-latest.osm.pbf` extract with
`osrm-extract` / `osrm-partition` / `osrm-customize`).

**Start the routing server** — one line, and the graph loads in a few seconds:

```bash
docker run -d --name amc-osrm -p 5001:5000 \
  -v "$PWD/osrm-data:/data" osrm/osrm-backend \
  osrm-routed --algorithm mld --max-table-size 2000 \
  /data/great-britain-latest.osrm
```

`--algorithm mld` is required (the graph is partitioned/customised, not
contracted). `--max-table-size` must exceed the group count — 354 groups today,
2000 leaves plenty of headroom and sets the origin chunk size to 1,646.

The 35,672 × 354 matrix takes **~2 minutes** and is then **cached** under
`data/interim/travel_matrix/`, keyed by a content hash of the coordinates. So the
server is only needed on the first run after geography or provision changes — you
can `docker stop amc-osrm` afterwards. If the server is down while `provider:
osrm` is set, the pipeline stops with the docker command above rather than a
connection error, and it never silently falls back to straight-line.

**Why it matters.** Against the haversine stub, road routing is wrong in *both*
directions, not just one:

| | haversine | OSRM road |
|---|---|---|
| median nearest group | 10.2 min | **13.8 min** |
| 90th percentile | 41.4 min | **35.1 min** |

A flat 40 km/h under-states typical journeys by ~35% while over-stating the worst
ones, because it ignores motorways. Switching moved 4 of the per-capita top 20 —
though all 20 stayed inside the OSRM top 100, consistent with the supply axis
being the most stable part of the model.

Set `provider: haversine` to run with no server at all. `ors` remains a stub
behind the same interface.

## Weighting & sensitivity

**The weights are a declared prior, not a fitted result.** They live in
`config.yaml` under `scoring.component_weights` (0.40 deprivation / 0.35
occupation / 0.25 isolation) and are applied to within-nation percentile ranks.

The reason is in [ADR 0001](docs/adr/0001-calibration-as-veto.md): at LA level
the three proxies are mutually collinear (deprivation correlates 0.72 with
isolation, 0.63 with occupation), so the multivariable fit returns deprivation as
significantly *protective*, and equal weights disagrees with each fitted scheme
about as much as they disagree with each other. The model cannot identify these
weights — but the choice still moves up to 12 of the top 20 areas. So we state
the weights, defend them, and use the fit to **veto** any the data contradicts.

`calibrate.py` prints the check. Severities: `contradicted` (the LA fit puts the
proxy's confidence interval entirely below zero), `unsupported` (the interval
spans zero and we lean on the proxy anyway), `collinearity` (informational).

`sensitivity.py` then asks whether our choices moved the shortlist, on three
axes, each scored against the shipped configuration's top-100:

| Axis | What varies | Decision set held | Shortlist overlap |
|---|---|---|---|
| **Alternative weightings** | declared vs equal vs the three fitted schemes | **100%** | 63–92% |
| **CI envelope** | weights drawn from the LA fit's confidence intervals | **100%** | 91% mean |
| **Supply constants** | travel/catchment split, 5×3 sweep | **100%** | 83–100% |

**What "held" means.** Set membership is a poor test — an area at rank 101 versus
99 flips in and out of a shortlist without changing any decision. So the verdict
measures **displacement**: of the top 20 areas (the ones you would actually act
on), how many stay inside the top 100 under an alternative configuration? All of
them do, under all 20 configurations tested; the furthest any fell was rank 70.

**Note on the overlap column:** it is the *share* of the top-100 retained, not
Jaccard. For equal-sized sets `Jaccard = overlap / (2 − overlap)`, so 63% overlap
reads as Jaccard 0.46 — a conflation that once set this bar 12 points too high.

**Tiers, not ranks.** Because the *order* is far less certain than the
*membership*, `fact_tier.parquet` bands every area: **shortlist** (top 100 under
every configuration — 47 areas), **in contention** (under some — 133), or
outside. The map and the PDF show the tier and the rank range each area spans.
Within a tier, treat areas as jointly prioritised and let local judgement decide.

The one dimension that does move the shortlist is **how much weight occupation
carries** (0.35 declared vs ~0.62 under the fitted schemes). That is a domain
question — is it measuring need, or "working-class male area"? — not one more
fitting will answer.

`sensitivity.json` records per-area robustness and low-confidence flags; the app
and the PDF surface both. Treat the output as a **shortlist for local judgement**,
not a ranking.

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
  calibrate.py           # LA-level GLM -> VETO on the declared weights -> weights.json
  caveats.py             # single source of the caveat/assurance copy (map + PDF)
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
  → calibrate.py        → weights.json   (LA-level veto; non-blocking)
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
