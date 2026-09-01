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

**If you are struggling, please talk to someone.** Samaritans are free, 24 hours
a day, on **116 123**, or `jo@samaritans.org`. In the UK you can also text
**SHOUT to 85258**. This repository is about where to put support, and is no
substitute for any of it.

**Not affiliated with Andy's Man Club.** They are named throughout because they
are the model this is built around and their public group listing is one of its
inputs. They have not endorsed, reviewed or commissioned it.

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
  it does not supply one, because with 331 LAs and three collinear proxies it
  cannot identify them ([ADR 0001](docs/adr/0001-calibration-as-veto.md)). No
  small-area suicide rate is ever fabricated.
- **Within-nation normalisation** — IMD/WIMD/SIMD aren't comparable across
  borders, so everything is percentile-ranked within nation. v1 ships
  **England & Wales**; Scotland/NI are documented stubs.
- **Three views** — *per-capita* (`priority_score`) for acute pockets, *reach*
  (`priority_score × male_working_age_pop`) for the most men reached per group,
  and *remoteness*, which re-ranks the areas further from a major town or city
  against each other on the same per-capita score. The third re-ranks a subset;
  it re-scores nothing.
- **Named blind spot** — areas whose occupational mix carries at least the
  national-average male suicide risk while `need_index` still puts them in its
  bottom half are flagged, wherever they rank. The weighting outvotes occupation
  roughly two to one, so the ranking structurally cannot surface them; the flag
  says so instead of leaving it as a caveat. Descriptive only — see
  `src/blindspot.py`.
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
#
#    ON A FRESH CLONE, pick one of these first. accessibility.provider is
#    `osrm`, and the prepared routing graph in osrm-data/ is NOT in the repo
#    (8GB+, rebuildable from an OSM extract) — so an untouched clone stops and
#    asks you to start a routing server that has no graph to serve:
#      config.yaml -> accessibility.provider: haversine   # no server, runs now
#      config.yaml -> mode: synthetic                     # instant, fully offline
#      or build the graph and start OSRM — see "Real routing" below
#
#    Also on a fresh clone: the list of existing groups is NOT fetched
#    automatically. Harvesting it is 713 requests against a small charity's
#    website, so it only runs when you ask for it, once:
#      python -m src.ingest.provision      # a few minutes, throttled on purpose
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

### The app (`streamlit run app/streamlit_app.py`)

Two pages, routed from `app/streamlit_app.py`:

- **🗺️ Priority map** — the ranked surface, all three views, per-area breakdowns.
- **📖 Beginner's guide** — a plain-English explanation of what the tool is,
  where every dataset comes from, how the factors are combined and how the tiers
  are decided, written for a non-technical reader with no statistics background.
  Every figure on it is read live from the pipeline's own outputs, so it cannot
  drift out of step with what the tool actually produced.

#### The map page

The priority surface runs **full width**, with the ranked shortlist and the
per-area factor breakdown side by side beneath it. **Selecting a row in the
shortlist** rings that area on the map in yellow, recentres on it, and loads its
factor breakdown — and it stays visible even if the current tier scope would
otherwise hide it. It defaults to showing **only
the decision-relevant areas** —
tier ① plus ②, 171 of 35,672 today. Plotting all 35k small areas is slow to
render and unreadable: the areas you would act on are a few hundred, and drawing
the rest buries them. The sidebar's **Map: areas shown** switches between:

| scope | areas | what it is |
|---|---|---|
| ① Shortlist | 54 | inside the top 100 under **every** configuration tested |
| ① + ② In contention | 171 | *(default)* — reaches the top 100 under **some** |
| All areas | 35,672 | the full surface |

Colour is always scaled against **all** areas in the chosen nation(s), never
against the visible subset — otherwise filtering to the top 54 would repaint the
weakest of them pale, as though it were low priority. Tier counts recompute with
the nation filter (Wales alone: 2 and 7). If `fact_tier.parquet` is absent —
sensitivity not yet run — the control falls back to a plain top-N-by-score cap.

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
| Occupation | Nomis Census 2021 RM107 (LSOA male shares by SOC major group) + ONS custom dataset API (MSOA male sub-major mix) + ONS suicide-by-occupation SMRs |
| Isolation | Nomis Census 2021 RM074 (marital) + TS003 (one-person households) |
| Deprivation | IMD 2019 (England scores) + WIMD 2019 (Wales ranks), LSOA 2011→2021 |
| Suicide signal | Nomis NM_161_1 / ONS registrations (male, all ages, X60-X84 + Y10-Y34, 5-yr pooled, England & Wales) |
| Car access (context only) | Nomis Census 2021 TS045 — households with no car or van |
| Provision | Andy's Man Club group finder (live harvest) |

Key real-data honesty notes (also surfaced on the map face):
- **Suicide counts are male all-ages, England & Wales, 331 LAs.** Working age is
  not obtainable at LA level: the publisher zeroes any cell below 5, which at
  working-age-band granularity loses ~52% of deaths. All ages recovers 96.6% —
  measured, see [ADR 0001](docs/adr/0001-calibration-as-veto.md). The proxies are
  working-age measures, so the outcome is broader than the population targeted.
- **Occupation is an SMR-weighted composition index, residence-based.** Each of
  the 26 SOC-2020 sub-major groups is weighted by the male suicide rate actually
  recorded for it (ONS 2011–2015, England, SOC 2010), so elementary trades count
  for roughly three times the average and corporate managers under a third. The
  sub-major mix is only published at MSOA, so neighbourhoods within one MSOA
  share an answer for *which* trades their men do and differ only in how many —
  see `src/ingest/occupation.py` and `occupational-risk-layer-spec.md`.
  **Living-alone** is a one-person-household share (no sex-broken figure exists
  at LSOA).
- **Travel time is car-only.** Public transport is not modelled, and that bites
  hardest exactly where this tool points: about one household in four in England
  and Wales has no car or van, rising above 50% in some of the shortlisted areas.
  So `ingest/car_access.py` carries the per-area no-car share as **context**
  (Census 2021 TS045) and the map and PDF flag where the drive time overstates
  access. It changes no score — it says where the supply surface is least
  trustworthy. It is *not*, as this README used to claim, the weight for blending
  car and public-transport access: measured, no-car share correlates **+0.66**
  with whether an evening bus round trip is possible at all, so blending on it
  would cancel most of the correction it was meant to make. Public transport has
  now been measured and deliberately left unscored — see
  [ADR 0002](docs/adr/0002-public-transport-feasibility-spike.md) and
  `spikes/pt_evening_access.py`.
- **Travel time uses real road routing** via self-hosted OSRM
  (`accessibility.provider: osrm`, the shipped default — see below). The
  dependency-free haversine stub remains available for a run with no server. It
  is wrong in *both* directions, not just one — it under-states typical journeys
  and over-states the worst of them, because a flat speed ignores motorways. The
  table under "Real routing" below has the measured figures.

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
isolation, 0.63 with occupation), so the multivariable fit cannot show
deprivation to be positive at all, and equal weights disagrees with each fitted
scheme about as much as they disagree with each other. The model cannot identify these
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
every configuration tested), **in contention** (under some), or outside. The map
and the PDF show the tier and the rank range each area spans. Within a tier,
treat areas as jointly prioritised and let local judgement decide.

Tiers are computed **separately for each view**, and the app and PDF show the set
matching the ranking on screen. Reach multiplies priority by population, so its
leaders are frequently mid-table per capita — a tier from one ranking says
nothing about the other:

| | per-capita | reach |
|---|---|---|
| shortlist | 54 | 58 |
| in contention | 117 | 106 |

(The stability verdict itself is measured on the per-capita view.)

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
  ingest/                # deprivation, occupation, isolation, car_access,
                         #   remoteness, suicide_la, provision
                         #   + scotland_ni_stubs.py (documented stubs)
  travel_time.py         # TravelTimeProvider: haversine stub + OSRM/ORS stubs
  accessibility.py       # fact_accessibility (supply surface)
  calibrate.py           # LA-level GLM -> VETO on the declared weights -> weights.json
  caveats.py             # single source of the caveat/assurance copy (map + PDF)
  score.py               # need_index, supply_index, priority_score, factor_breakdown
  blindspot.py           # the occupational blind-spot flag (descriptive; never scored)
  pipeline.py            # runs the whole thing
app/
  streamlit_app.py       # entry point: routes the two pages below
  views/priority_map.py  # map + three views + per-area breakdown + caveats
  views/guide.py         # plain-English guide for non-technical readers
data/{raw,interim,output}/
tests/
```

## Data flow

```
raw (downloaded / synthetic)
  → geography.py        → dim_geography, dim_population
  → ingest/*            → fact_deprivation, fact_occupation, fact_isolation,
                          fact_car_access + fact_remoteness (context only),
                          fact_suicide_la, dim_provision
  → calibrate.py        → weights.json   (LA-level veto; non-blocking)
  → accessibility.py    → fact_accessibility   (TravelTimeProvider)
  → score.py            → fact_score.parquet + .geojson   (need × (1 − supply))
  → blindspot.py        → blind_spot.json   (what the need index cannot see)
  → app/views/          → map + guide
```

## How not to use this

The output names specific neighbourhoods. That is the point, and it is also the
risk, so this is the short version of what the rest of the documentation argues
at length.

- **Do not present it as a map of suicide risk.** It is not one, and it cannot
  become one: no small-area suicide rate exists, and none is invented here. The
  index measures conditions associated with *unmet need for support*. Captioning
  `fact_score.parquet` as "where men are most likely to take their own lives"
  would be false, and it would be false about real, named places.
- **Do not read the order as a ranking.** The evidence separates the tiers, not
  the areas within one. That is why the output is banded. Rank 3 is not a
  stronger case than rank 11.
- **Do not use it on an individual, ever.** Every input is an area aggregate.
  Nothing here says anything about any person who lives there, and no
  combination of these outputs can.
- **Do not site a group on it alone.** It knows nothing about venue,
  volunteers, partners or whether men in that area would come, and those decide
  whether a group survives. It narrows the question; people answer it.
- **Do not quietly change the weights and keep the claims.** They are a declared
  prior, defended in `config.yaml` and vetoed by the council-level fit. Move
  them and the sensitivity analysis has to be re-run and re-reported, because
  the shortlist does move.

## Licence and attribution

**Code:** MIT, see [`LICENSE`](LICENSE). Lift it, adapt it, run it for your own area. The data below is licensed separately and its terms still apply.

**Data:** none is redistributed here. `.gitignore` deliberately excludes
`data/raw/`, `data/interim/` and `data/output/`, so the repository carries the
code that fetches and derives, never the sources or the derived shortlist.
Please keep it that way. If you publish outputs from this pipeline, you need the
attributions below, and they are conditions of the licences, not courtesies.

- ONS suicide registrations, Census 2021 (via Nomis), population estimates and
  the Open Geography Portal boundaries and lookups: **Source: Office for
  National Statistics licensed under the Open Government Licence v.3.0.**
  Contains OS data © Crown copyright and database right 2026.
- Index of Multiple Deprivation 2019: **© Ministry of Housing, Communities and
  Local Government, licensed under the Open Government Licence v.3.0.**
- Welsh Index of Multiple Deprivation 2019: **© Welsh Government, licensed under
  the Open Government Licence v.3.0.**
- Road network for OSRM routing: **© OpenStreetMap contributors**, available
  under the [Open Database Licence](https://www.openstreetmap.org/copyright).
  The cached travel matrix is a derived database; ODbL share-alike conditions
  apply if you distribute it.
- Group locations: public listings from the Andy's Man Club group finder,
  harvested politely (see [`src/ingest/provision.py`](src/ingest/provision.py)).

General guidance on the Open Government Licence is
[here](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
This section is a good-faith summary, not legal advice.

## Guardrails (enforced in code & UI)

- **Aggregate only** — no individual-level record in any table, output, or log.
- **Latent need, not prediction** — named `need_index`, never `risk_of_suicide`.
- **Show uncertainty** — data vintages, registration-lag and small-number
  caveats surfaced on the map face.
- **Within-nation only** — never compare raw IMD/WIMD/SIMD across borders.
- **Human-in-the-loop** — the output is a shortlist; siting needs local judgement
  (venue, volunteers, partner appetite).
