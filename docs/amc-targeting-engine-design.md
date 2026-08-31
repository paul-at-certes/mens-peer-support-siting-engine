# Men's Peer-Support Siting Engine — Design Note

*A data-driven tool to prioritise where the next Andy's Man Club (or similar men's peer-support group) would do the most good.*

**Status:** draft proposal for discussion · **Scope:** v1 = open aggregate data only · **Author's note:** every score in this design decomposes into its contributing factors — there is no black box, and no individual is ever scored.

---

## 1. What the tool does (and what it deliberately does not do)

**Does:** ranks small geographic areas across the UK by *unmet need* — combining a latent **risk surface** (where working-age men are most likely to be struggling, based on deprivation, occupation, isolation and the suicide signal) with a **supply surface** (how well-served that area already is by existing groups), to produce a prioritised, mapped shortlist of candidate locations.

**Does not:** identify, score, or flag individuals. It operates only on area-level open statistics. This is a public-health *resource-allocation* tool, not a clinical risk tool — a distinction that keeps it both ethical and information-governance-light.

The output answers one concrete question the charity actually has: *given a goal of growing from ~320 to ~1,300 groups, which underserved areas should we open in first?*

---

## 2. The central design problem: geographic granularity

This is the crux of the whole build, so it comes first.

The **outcome signal** (suicide deaths) is only reliably published at **Local Authority** level. Below that, numbers are too small to publish (disclosure control) and too noisy to trust year-on-year. It also lags badly — a registration-based figure for a year is only ~39% complete for deaths that actually occurred in it, with median registration delays of ~200 days (England) to ~270 days (Wales).

The **risk proxies and supply data**, by contrast, are available at fine granularity: deprivation and occupation down to LSOA / Output Area, and group locations as precise points.

You cannot simply join these — you'd be inventing LSOA-level suicide rates that don't exist. The honest resolution is a **two-level model**:

1. **Calibrate** at Local Authority level — use the (coarse, real) suicide data to learn how strongly each fine-grained proxy actually predicts the outcome.
2. **Apply** those learned weights at fine (LSOA) level, where the proxies exist but the outcome doesn't, producing a smooth *latent risk surface*.
3. **Subtract supply** (accessibility to existing provision) at the same fine level to get *unmet need*.

This way the suicide data is used for what it can honestly support — validation and weighting — rather than being fabricated at a resolution it doesn't have.

A further wrinkle: the UK's four nations publish **separate, non-comparable** deprivation indices (England IMD, Welsh WIMD, Scottish SIMD) and run **separate censuses** (England & Wales 2021, Scotland 2022), via separate suicide statistics bodies (ONS, NRS, NISRA). v1 should therefore **rank within each nation** and present a harmonised UK view by percentile, never by raw cross-border score.

---

## 3. Data model

A simple star schema on a shared geographic spine. Base unit: **LSOA** (England & Wales) / **Data Zone** (Scotland) / **SOA** (Northern Ireland), with official lookups up to Local Authority, region and nation.

### Dimension tables

| Table | Grain | Key fields |
|---|---|---|
| `dim_geography` | small area | `area_code` (GSS), `area_name`, `la_code`, `la_name`, `region`, `nation`, `centroid_lat`, `centroid_lon` |
| `dim_population` | small area | `area_code`, `total_pop`, `male_pop`, `male_working_age_pop` (16–64), `year` |
| `dim_provision` | point | `group_id`, `org` (AMC / ManKind / other), `lat`, `lon`, `status`, `start_date`, `type` |

### Fact tables

| Table | Grain | Key fields |
|---|---|---|
| `fact_suicide_la` | LA × year × sex × age band | `la_code`, `year`, `deaths`, `population`, `rate`, `pooled_rate_5yr` |
| `fact_deprivation` | small area | `area_code`, `imd_rank_within_nation`, `income_domain`, `employment_domain`, `health_domain` |
| `fact_occupation` | small area | `area_code`, `male_high_risk_occ_count`, `male_high_risk_occ_pct` (construction, agriculture, elementary trades) |
| `fact_isolation` | small area | `area_code`, `male_single_separated_pct`, `male_living_alone_pct` |
| `fact_accessibility` | small area | `area_code`, `nearest_group_id`, `travel_minutes`, `groups_within_30min` |
| `fact_score` | small area | `area_code`, `need_index`, `supply_index`, `priority_score`, `rank`, `percentile`, `factor_breakdown` (JSON) |

`fact_score.factor_breakdown` carries the per-area contribution of each input — this is what makes the ranking explainable to a charity board and auditable for fairness.

---

## 4. Source-by-source ingestion & join logic

Everything resolves to the `area_code` spine. Point data (groups) joins via a travel-time matrix to area centroids.

**ONS "Suicides in England and Wales by local authority"** (+ NRS / NISRA equivalents)
- Grain: LA × year × sex × age. Key: GSS `la_code`.
- Transform: filter to **male, working age**; **pool 5 years** to stabilise small numbers; compute age-standardised rate with population denominator.
- Join: to `dim_geography` on `la_code`. Used only at calibration step (§5).

**Index of Multiple Deprivation** (IMD / WIMD / SIMD)
- Grain: LSOA / Data Zone. Key: `area_code`.
- Transform: take the **income** and **employment** domains specifically (more suicide-relevant than the headline index); convert to **within-nation percentiles** for cross-border comparability.
- Join: direct on `area_code`.

**Census 2021/2022 occupation & industry**
- Grain: Output Area / LSOA. SOC-2020 coded.
- Transform: aggregate male employment in elevated-risk groups (construction building trades, elementary construction, agriculture, process/plant operatives); express as male-employment share.
- Join: aggregate OA→LSOA, then on `area_code`.

**Census household / relationship status**
- Transform: derive male single/separated and living-alone proxies for the isolation layer.

**ONS mid-year population estimates**
- Provides denominators and the working-age-male scaling factor (so the tool can weigh *rate* against *number of men reachable*).

**Provision data (AMC group finder + peers)**
- Grain: point. Source: public group listings; geocode addresses / What3Words → lat/lon.
- Transform: build a **travel-time matrix** (car + public transport) from each area centroid to every group; derive nearest-group minutes and group count within a 30-minute catchment.
- Tooling: OpenRouteService / OSRM / Valhalla (self-host) or a hosted travel-time API. Precompute — provision changes rarely.
- Join: populates `fact_accessibility` per `area_code`.

---

## 5. Scoring methodology (transparent by design)

### Step 1 — Need index (latent risk)

A weighted combination of standardised (within-nation percentile) components:

```
need_index =  w1 · deprivation_income_employment
            + w2 · male_high_risk_occupation_share
            + w3 · male_isolation_proxy
            + w4 · local_suicide_signal (LA, mapped down, pooled)
```

**Deriving the weights, not guessing them.** Fit a simple, interpretable model at **Local Authority** level — a Poisson or negative-binomial regression of pooled male suicide *counts* on the aggregated proxies, with population as an offset. The fitted coefficients become `w1…w4`. This calibrates the index to genuinely predict the real outcome while remaining fully explainable — every weight has a clear meaning and a confidence interval. Start there before considering anything fancier.

*Why not just rank by historical deaths?* Because that chases the past and is hostage to noise and registration lag. A calibrated proxy generalises to where the next need is, not only where deaths have already been recorded.

### Step 2 — Supply index

```
supply_index = normalise( f(travel_minutes_to_nearest_group, groups_within_30min) )
```

Higher where men can already reach a group easily.

### Step 3 — Priority

```
priority_score = need_index × (1 − supply_index)
```

High need **and** poor existing access rises to the top. Provide **two views** the user can toggle:

- **Per-capita view** — `priority_score` as above (rate-led): finds the most acute pockets.
- **Reach view** — `priority_score × male_working_age_pop`: finds where a single new group would touch the *most* men. A high-rate hamlet and a moderate-rate town are different propositions; the charity should see both.

### Honesty guardrails baked into the scoring

- **Pool years** and show **uncertainty** — flag areas where the underlying numbers are small.
- **Within-nation normalisation** — never compare raw IMD/WIMD/SIMD across borders.
- **Full decomposition** — every area's score breaks down into its drivers in `factor_breakdown`; nothing is unexplained.
- **Latent, not individual** — the index estimates area need, never a person's risk.
- **Human-in-the-loop** — the output is a ranked shortlist for local judgement (venue availability, partner appetite, the charity's own organic intelligence), not an automated decision.

### Where to go next (v2+, optional)

Small-area estimation / spatial smoothing (e.g. a CAR/BYM spatial model) to borrow strength across neighbouring areas; sensitivity analysis on weights; back-testing against where new groups subsequently succeeded. None of this is needed to ship a useful v1.

---

## 6. Infrastructure recommendation

You flagged it needn't be Foundry — and for v1, it genuinely shouldn't be. Here's the honest reasoning.

**Right-size to the actual workload.** This is *not* big data. There are ~35,000 LSOAs in England & Wales, ~42,000 small areas UK-wide, across a few dozen source tables — megabytes to low single-digit gigabytes. Updates are slow and batch (ONS annual, Census decennial, IMD every few years, surveillance quarterly, group lists occasionally). There is no streaming, no high-velocity ingest, no person-level data. The heaviest compute is the one-off travel-time matrix.

Given that, a heavyweight governed platform is the wrong tool for this phase — its value lies in governing *sensitive, siloed, person-level enterprise data*, which v1 deliberately avoids. Reaching for it here adds cost, licensing and operational weight a charity can't sustain, for capabilities the problem doesn't need.

**Recommended v1 stack — cheap, reproducible, sustainable:**

- **Analytical store:** **DuckDB + Parquet.** At this data scale it's ideal — serverless, runs on a laptop or a tiny VM, fast SQL, no infrastructure to babysit. Version the source and output files in object storage (or even Git), so the whole pipeline is reproducible and auditable.
- **Transformation:** Python (`pandas` / `geopandas` for the spatial joins), organised as dbt-style modular models so logic is readable and testable. Spatial joins and the centroid lookups live here.
- **Routing / travel time:** OpenRouteService, OSRM or Valhalla (self-hosted) or a hosted travel-time API. Precompute once; refresh only when provision changes.
- **Orchestration:** deliberately minimal — a scheduled GitHub Action or a small cron job. Quarterly/annual cadence does not warrant Airflow.
- **Serving / front end:** a map is the product. Two good options:
  - *Static + durable:* scored GeoJSON → **PMTiles + MapLibre** on static hosting. Pennies to run, lasts for years, hard to break.
  - *Analyst-interactive:* a **Streamlit / Dash / Observable** app for filtering, toggling the per-capita vs reach views, and inspecting each area's factor breakdown.
- **Hosting:** a small cloud VM, Cloud Run, or static hosting + serverless. Realistically sub-£50/month, plausibly free-tier.

**Keep it open-source** where possible. It removes lock-in, lets the charity (or volunteers) maintain it after you've moved on, and lets other regions or causes replicate the method.

**Where a governed platform (Foundry/AIP or equivalent) *does* earn its place:** phase 2 — if the project ever integrates **sensitive, person- or incident-level real-time surveillance data** across police, coroner and NHS sources. There, lineage, fine-grained access control, audit and operational workflow are the entire point, and the governance justifies the weight. Match the infrastructure to the phase: featherweight for open-data siting, governed platform only if you move to sensitive integration.

---

## 7. Ethics & governance guardrails (carry these throughout)

- **Aggregate only** in v1 — no individual is identified, scored, or contacted as a result of this tool.
- **Latent need, not prediction** — the language matters; this allocates resource, it does not forecast deaths.
- **Decisions stay human** — the shortlist informs local judgement; siting also depends on venue availability, partner capacity and the charity's organic intelligence.
- **Follow Samaritans media guidelines** for anything public-facing (maps of suicide data can be misread or sensationalised).
- **Partner early** with the charity and, ideally, a local-authority public-health / suicide-prevention lead — they bring legitimacy, ground truth, and the route to action.
- **Document data vintage and limitations** on the face of the tool — registration lag, small-number caveats, cross-nation non-comparability.

---

## 8. Suggested build sequence

1. **Spine + population** — stand up `dim_geography` and `dim_population`; prove the lookups across all four nations.
2. **Risk proxies** — ingest deprivation, occupation, isolation; assemble the (uncalibrated) need layer.
3. **Calibration** — fit the LA-level model; derive and sanity-check the weights with confidence intervals.
4. **Provision + accessibility** — geocode groups; compute the travel-time matrix; build the supply layer.
5. **Score + map** — produce `fact_score`; ship the map with both views and per-area factor breakdowns.
6. **Validate with partners** — review the top-ranked areas against local knowledge; iterate weights.
7. **(Optional) v2** — spatial smoothing, back-testing, sensitivity analysis.

A focused v1 covering steps 1–5 is a few weeks of part-time work for someone with your background, and produces something concrete enough to put in front of AMC or a public-health team.
