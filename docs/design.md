# Design & Methodology — Men's Peer-Support Siting Engine

Methodology reference for the repo. Operational instructions (stack, repo layout, build sequence, data-source access, definition of done) live in `CLAUDE.md`; this document explains **why the system is built the way it is** and records the assumptions, the model specification, the validation approach, and the known limitations. Read both.

---

## 1. Purpose and framing

The engine ranks UK small areas by **unmet need for a men's peer-support group** so a charity (e.g. Andy's Man Club) can prioritise where to open next. It is a **resource-allocation** tool built on **aggregate open data**.

Two framing decisions shape everything downstream:

- **Area-level, not individual-level.** The majority of men who die by suicide were never in contact with mental health services, so individual/clinical prediction both misses the target population and carries serious ethical and information-governance costs. We model *where need concentrates*, never *who is at risk*.
- **Latent need, not predicted deaths.** The score estimates the latent demand for non-clinical peer support among working-age men. It is correlated with — but not the same as — the suicide rate. We use the suicide signal to *calibrate and validate* the need index, not as the target to be reproduced.

---

## 2. The granularity problem and its resolution

This is the defining methodological challenge.

**Outcome data is coarse and lagged.** Suicide deaths are only published reliably at **Local Authority** level. Below that, counts are small enough to be suppressed (disclosure control) and too volatile to trust year-on-year. Registration also lags by a median of ~200 days (England) to ~270 days (Wales), because deaths require a coroner's inquest; a single year's registrations are only ~39% complete for deaths that actually occurred in that year.

**Proxy and supply data is fine-grained.** Deprivation, occupation and household composition are available at **LSOA / Output Area**; group locations are precise points.

Naively joining LA suicide rates onto LSOAs would fabricate a resolution the outcome data does not have. The resolution is a **two-level model**:

1. **Calibrate at LA level.** Aggregate the small-area proxies up to LA; learn how strongly each predicts the (real, coarse) suicide signal.
2. **Apply at small-area level.** Use the learned weights on the small-area proxies to produce a latent `need_index` everywhere, including where no outcome data exists.
3. **Net off supply.** Subtract an accessibility-based `supply_index` at small-area level to obtain `priority_score`.

The outcome data thus does only what it can honestly support — weighting and validation — and is never interpolated to a false resolution.

### Cross-border non-comparability

The four UK nations publish **separate, non-comparable** deprivation indices (England IMD, Welsh WIMD, Scottish SIMD), run **separate censuses** (England & Wales 2021, Scotland 2022), and have **separate suicide-statistics bodies** (ONS, NRS, NISRA). Consequently:

- All normalisation is **within-nation** (percentile ranks), never raw cross-border scores.
- The UK-wide view is assembled by stitching within-nation percentiles, with a clear caveat that absolute comparability across borders is not claimed.
- v1 ships **England & Wales** first; Scotland and NI sit behind the same interfaces as documented stubs.

---

## 3. Inputs and the latent-need construct

`need_index` is a weighted composite of four standardised components, chosen because each is an established, area-measurable correlate of male suicide risk and of the kind of distress peer support addresses.

| Component | Measure | Rationale |
|---|---|---|
| **Deprivation** | IMD **income** + **employment** domains (not the headline index) | Working-age male suicide tracks economic insecurity — unemployment, unmanageable debt — more tightly than the composite IMD, which is diluted by domains like education and crime. |
| **High-risk occupation** | Male employment share in elevated-risk SOC-2020 groups (construction building trades, elementary construction, agriculture, process/plant operatives) | These occupations carry markedly elevated suicide risk and concentrate the masculine-norm, low-help-seeking population peer support is designed to reach. |
| **Isolation** | Male single/separated and living-alone proxies (Census household/relationship) | Relationship breakdown and social isolation are recurrent antecedents; peer connection is the direct counter. |
| **Suicide signal** | LA-level pooled male working-age rate, mapped down | Anchors the index to the observed outcome at the only resolution it exists. Carried at low weight relative to the fine-grained proxies, which generalise better than chasing historical counts. |

**Why not just rank by historical deaths?** Because that chases the past, is hostage to small-number noise and registration lag, and offers no signal where deaths haven't yet been recorded. A calibrated proxy projects to where the *next* need is.

**Occupation caveat.** Census occupation is **residence-based**, not workplace-based — it tells you where high-risk workers *live*, which is appropriate for siting a community group, but is not the same as where they work. Note this explicitly; do not present it as a workplace map.

---

## 4. Calibration model

> **Amended 2026-08-31 — `docs/adr/0001-calibration-as-veto.md` supersedes the weighting
> half of this section.** The model below is still fitted, still reported, and still the
> basis of the checks. What changed is what its coefficients are *for*: they **check** the
> scoring weights rather than becoming them, because on the real data they do not identify
> them. Read this section as the specification of the check.
>
> Three corrections to what follows:
> - **Weights are a declared prior** (`config.yaml` `scoring.component_weights`), applied to
>   within-nation percentile ranks. The "exponentiated to rate ratios" instruction below is
>   also withdrawn: rate ratios here sit near 1.0 (1.07–1.14), so normalising them to sum to
>   1 flattens the weights toward equal and erases the signal. Relative effect size lives in
>   the log-coefficients.
> - **The offset is the outcome dataset's own denominator**, not male working-age
>   population. Mixing an age-10+ numerator with a 16–64 denominator was suppressing the
>   isolation signal (its univariate CI moved from spanning zero to entirely positive).
> - **The composite scheme merges deprivation + isolation** (r=0.72 at LA level), the
>   actually-collinear pair, not deprivation + occupation (r=0.63).

The proxy weights are **learned, not guessed**, via an interpretable model fitted at Local Authority level.

**Specification.** Regress pooled male working-age suicide **counts** on the LA-aggregated proxies, with population as an offset:

```
deaths_LA  ~  Poisson/NegBin( exp( β0 + β1·deprivation + β2·occupation + β3·isolation ) ,  offset = log(male_working_age_pop) )
```

- **Counts with an offset**, not rates, so the model respects the Poisson nature of rare-event data and weights LAs by their population correctly.
- **Negative binomial** if the residuals are over-dispersed (they usually are for this kind of data); Poisson otherwise. Test and report the dispersion.
- **Pool ~5 years** of suicide registrations to stabilise the small annual counts before aggregating.
- The fitted coefficients (exponentiated to rate ratios) become the component weights `w1…w3`; the LA suicide signal itself enters the small-area index as a separate, lightly-weighted term.

**Outputs to persist and surface:** the coefficients, their **confidence intervals**, the dispersion statistic, and a goodness-of-fit summary. If a coefficient is not distinguishable from zero, say so — don't quietly keep a weight the data doesn't support.

**Interpretability is a requirement, not a nicety.** The point of a transparent GLM over a black-box learner is that every weight has a defensible meaning and an uncertainty range you can put in front of a charity board or a public-health lead.

---

## 5. Supply and accessibility

`supply_index` captures how well-served an area already is.

- **Travel time** from each small-area centroid to the nearest existing group, by car and (where available) public transport — modes matter for a population that may lack reliable car access.
- **Catchment density** — number of groups reachable within a 30-minute threshold.
- Combined and normalised so that easy access ⇒ high supply.

Provision changes rarely, so the travel-time matrix is **precomputed and cached**. v1 develops against a haversine (straight-line) stub behind a `TravelTimeProvider` interface; a real routing engine (OSRM / OpenRouteService) or hosted API is swapped in later without touching downstream code. Straight-line distance over-states accessibility in rural and estuarine geographies — flag this until real routing lands.

---

## 6. Scoring

```
need_index     = Σ wᵢ · zᵢ            # zᵢ = within-nation percentile of component i
supply_index   = normalise( f(travel_minutes, groups_within_30min) )
priority_score = need_index × (1 − supply_index)
```

High need **and** poor existing access float to the top.

### Two views, presented side by side

- **Per-capita** — `priority_score` as above. Surfaces the most acute pockets of need.
- **Reach** — `priority_score × male_working_age_pop`. Surfaces where one new group would touch the **most** men.

These answer different questions and a high-rate hamlet vs a moderate-rate town trade off differently between them; the charity should see both rather than have the tool pick for them.

### Explainability

Every area carries a `factor_breakdown` (per-component contribution to its score). Nothing in the ranking is unexplained — this is what makes the output auditable for face validity and for unintended bias.

---

## 7. Validation

A score is only useful if it's defensible. Three checks, in increasing strength:

1. **Face validity.** Review the top-ranked areas with charity staff and a local public-health lead. Do they recognise these as genuinely underserved, high-need places? Disagreement is signal — investigate, don't override silently.
2. **Sensitivity analysis.** Perturb the weights within their confidence intervals and re-rank. A robust shortlist shouldn't reshuffle wildly; areas that only rank highly under a narrow weight choice are flagged as low-confidence.
   *Implemented (`src/sensitivity.py`).* Because the proxies are collinear, `calibrate.py` fits three weighting schemes (multivariable / univariate / composite) and the sensitivity step measures both (a) how much the top-N shortlist moves *between* schemes and (b) its stability under CI perturbation of the active scheme. Headline result on real England & Wales data: the ordering is stable **within** any scheme (~99% shortlist retention, zero low-confidence areas), but the *choice of scheme* moves the precise top-100 — so the scheme is a documented decision (default **univariate**, the only scheme that keeps all three proxies contributing), not a silent default. Per-area robustness is persisted to `sensitivity.json` and surfaced in the app.
3. **Back-testing (v2).** Where groups have already opened, do the areas the model would have prioritised correspond to where new groups subsequently sustained good attendance? This is the closest thing to an outcome test and the most honest measure of whether the index tracks real demand.

---

## 8. Known limitations (state these on the tool's face)

- **Ecological inference.** Area-level associations need not hold for individuals; the tool allocates resource to places, and must not be read as a statement about any person.
- **Correlation, not causation.** The proxies are markers, not mechanisms. The index points to where need concentrates, not why.
- **Coarse, lagged outcome.** The suicide signal is LA-level and registration-delayed; it informs weighting but cannot drive fine-grained ranking on its own.
- **Cross-border comparability.** Within-nation only; the UK view is stitched percentiles, not an absolute common scale.
- **Residence-based occupation.** Where high-risk workers live, not where they work.
- **Need ≠ suicide rate.** Peer-support demand is broader than, and not identical to, the suicide rate; the score is a proxy for the former, calibrated against the latter.
- **Supply ≠ siteability.** A high score means "underserved and high-need", not "a venue, volunteers and partner appetite exist here". Those are local-judgement inputs the tool deliberately leaves to humans.

---

## 9. Ethics

- **Aggregate only** — no individual-level record enters any table, output or log.
- **Latent need, not prediction** — naming and UI copy reflect this throughout (`need_index`, never `risk_of_suicide`).
- **Human-in-the-loop** — the deliverable is a *shortlist for local judgement*, never an automated siting decision.
- **Responsible presentation** — follow Samaritans media guidelines for any public-facing maps of suicide-related data; show data vintages and small-number caveats.
- **Sustainability** — open data, open tooling, documented sources, so the charity or volunteers can maintain and replicate it.

---

## 10. Glossary

- **LSOA** — Lower-layer Super Output Area; small census geography (England & Wales), ~1,500 residents. **Data Zone** (Scotland), **SOA** (Northern Ireland) are the equivalents.
- **LA** — Local Authority; the coarse geography at which suicide data is published.
- **GSS code** — Government Statistical Service area code; the canonical join key across official datasets.
- **IMD / WIMD / SIMD** — Index of Multiple Deprivation for England / Wales / Scotland; **non-comparable across borders**.
- **SOC-2020** — Standard Occupational Classification, used to identify high-risk occupation groups.
- **nRTSSS** — near-to-real-time suspected suicide surveillance; the police-sourced early-warning system that partly mitigates registration lag (relevant to a possible phase 2, not used in v1).
- **Offset (in the GLM)** — log-population term that converts a count model into a rate model while preserving Poisson structure.
