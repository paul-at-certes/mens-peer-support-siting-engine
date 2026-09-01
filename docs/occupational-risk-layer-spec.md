# Occupational Risk Layer — Implementation Spec (v2)

**Project:** Men's Peer-Support Siting Engine
**Layer:** Male occupational composition index (`occupation_proxy`), England & Wales
**Status:** **Built and shipped** (2026-08-31). Design questions resolved 2026-08-31; the
implementation is `src/ingest/occupation.py`. Read this as the specification it was built
to, not as outstanding work.
**Supersedes:** v1 of this file. What changed and why is in §2.

---

## 1. Objective

Replace the current `occupation_proxy` — an unweighted male share in SOC-2020
**major** groups 5, 8 and 9 — with an index that weights each occupational group
by its **observed male suicide SMR**, so that an area of elementary trades
(SMR ~3.0) is not scored the same as an area of chefs and textile workers,
which today it is.

This is a **latent-need component**, not a prediction. It scores the
occupational composition of an area's male population. It does not estimate
anyone's risk, and per CLAUDE.md's guardrail it is never named or presented as
one. The output column stays `occupation_proxy` so it drops into the existing
`need_index` at its declared weight of 0.35.

---

## 2. Data availability — measured (2026-08-31)

v1 left the pivotal question open ("**VERIFY** the finest SOC digit level
actually published per geography") and assumed sex and SOC resolution degraded
independently. An earlier draft of this file then claimed sub-major × sex was
available at LSOA with zero areas blocked. **That claim was wrong** — it came
from a query whose category filter the API silently ignored (§2.1), so what was
actually measured was a sex-only table. Re-measured properly:

| Route | Sex? | SOC level | Geography | Coverage |
|---|---|---|---|---|
| Nomis **RM107** (`NM_2207_1`) | yes | major (10) | LSOA | **100%** |
| ONS custom, `occupation_current_27a` × sex | yes | **sub-major (26)** | LSOA | **32%** — disclosure-blocked |
| ONS custom, `occupation_current_27a` × sex | yes | sub-major (26) | **MSOA** | **100%** |
| ONS custom, `occupation_current_27a` (persons) | **no** | sub-major (26) | LSOA | 100% |
| ONS custom, `occupation_current_105a` × sex | yes | minor (105) | MSOA | ~0.6% — blocked |
| ONS custom, `occupation_current_105a` × sex | yes | minor (105) | LTLA | 92% |
| Nomis **TS064** (`NM_2081_1`) | **no** | minor (105) | MSOA | 100% |

**Sex-crossed sub-major occupation does not exist at LSOA.** ONS blocks roughly
68% of areas (measured: 272 of 400, 338 of 500, 32 of 50 across samples). It
does exist, unblocked, at MSOA.

### 2.1 The design that follows: a hybrid

Take each piece from the grain where it is actually published:

- **LSOA** male shares by **major** group — exact, every area (Nomis RM107).
- **MSOA** male composition **within** each major group — ONS custom API.

```
occupation_proxy(lsoa) = Σ_M  major_share(lsoa, M) × composite_smr(msoa, M) / 100
composite_smr(msoa, M) = Σ_{g∈M} within_share(msoa, g) × smr_g
```

Between-area variation in *how many* men do skilled trades stays at LSOA, where
it is measured exactly; only *which* skilled trades is smoothed to the MSOA
(~4.9 LSOAs). **Both inputs are male**, so no sex-composition is assumed
anywhere — which is why this beats the other 100%-coverage option, LSOA
sub-major for all *persons*. That option would have to split each major group by
sex from an assumption, and sex composition varies sharply across exactly the
splits that carry the signal (91 elementary trades vs 92 elementary
administration; 61 caring vs 62 leisure).

### 2.2 API mechanics (the part that cost an hour, twice)

Base: `https://api.beta.ons.gov.uk/v1/population-types/UR/census-observations`

**The API has no category-level filtering.** Only two parameters exist:
`area-type=<type>,<code>,<code>…` to filter areas, and `dimensions=<a>,<b>` to
choose which variables to cross. A parameter like `occupation_current_27a=1` is
**silently ignored** — no error, no warning — and you get the unfiltered total
back with a plausible row count. That is what produced the false "0 blocked"
reading above. **Verify a filter changed the values, never just the row count.**

So chunking must be **by area**, over the full cross-tab:

```
?area-type=msoa,E02000001,E02000002,…&dimensions=occupation_current_27a,sex
```

- `limit` is **ignored** on this endpoint; there is no paging loop to write.
- **400 areas per chunk** (500 works; 1000 returns HTTP 520). 7,264 MSOAs → 19
  chunks, about a second each.
- Blocked areas are **excluded from the response, not fatal** — the response
  reports `blocked_areas`, and a chunk containing one still returns the rest.
  Check that count; do not infer coverage from row totals.
- Area codes must be valid for the area-type. Some tiny LSOAs (City of London)
  are blocked individually and return `{"errors":[""]}` — an empty error string,
  which is a disclosure block, not a syntax error.

**LSOA→MSOA lookup.** No England-and-Wales LSOA21→MSOA21 lookup is published on
the ONS Geo Portal (the `OA21…LSOA21_MSOA21` lookups are England-only). ONS's
2021 naming convention gives one — an LSOA label is its MSOA label plus a
trailing letter ("City of London 001A" → "City of London 001") — and it is
derived from the API's own area lists and then **validated**: all 35,672 LSOAs
must match and all 7,264 MSOAs must be hit (they are). The adapter raises rather
than scoring on a partial join.

---

## 3. Data inputs

| # | Dataset | Source | Role |
|---|---------|--------|------|
| 1 | Suicide by occupation: England, main data tables (2011–2015) | direct file URL below — **automatable** | Male SMRs by SOC 2010, major → unit level. The only official SMR source; the 2016–2020 update was cancelled. |
| 2 | Census 2021 sub-major occupation × sex | ONS custom API, §2.1 | Male workforce composition per LSOA. |
| 3 | ONS ad hoc counts (suicide by occupation 2011–2021; construction 2011–2024) | ONS adhocs 15043 / 3370 | Trend context only. **Counts must never become weights** — ONS is explicit that counts reflect population structure, not risk. |
| 4 | Hare, Lawani & McEwen (2024), JEPPM 14(2) | https://researchonline.gcu.ac.uk/ws/portalfiles/portal/82283223/82274781.pdf | Context for the construction trend. **No trend uplift in v1** (§9). |

**v1 called input 1 a manual download. It is not.** It has a stable direct URL
and is already cached in the repo:

```
https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/birthsdeathsandmarriages/
deaths/datasets/suicidebyoccupationenglandmaindatatables/2011to2015/dataforthecommentary.xls
```

- Cached at `data/raw/real/suicide_by_occupation_smr.xls` (350 KB, git-ignored).
- Fetch it with the existing `download_to` cache-if-present helper, following the
  `deprivation.py` pattern exactly: direct URL, cached, and if the fetch fails,
  `require_file` fails loudly with the URL and the expected path. No live
  dependency at run time once cached.
- The file is `.xls` (1997 format, not `.xlsx`), so **add `xlrd>=2.0` to
  `pyproject.toml`** alongside the existing `odfpy` (which is there for the same
  reason on the Welsh WIMD `.ods`).

Despite being named "data for the commentary", the workbook is complete: Table 2
is major groups, **Table 3 is sub-major**, Table 4 minor, Table 5 unit, each with
male and female deaths, SMR and 95% confidence limits for ages 20–64.

**v1's input 3, the SOC 2010 → SOC 2020 correspondence table, is not needed.**
See §4.

---

## 4. Risk weight vector

Parse **Table 3** (sub-major, male columns: deaths, SMR, LCL, UCL) into
`data/output/occupation_weights.csv`. Header row is at index 5, data rows 6–30.
Note the workbook misspells "Tranport" in group 82 — match on the SOC code, never
the label.

### 4.1 The SOC bridge collapses to identity

SOC 2010 sub-major has **25** groups; SOC 2020 sub-major has **26**. The only
structural difference is that SOC 2020 adds **63 Community and civil enforcement
occupations** (14,422 men nationally). Every other code matches one-to-one.

So the bridge is an identity join plus one unmatched group, and no correspondence
table, employment-weighted mean, or many-to-many handling is required. Under the
rule in §4.3, group 63 resolves to the neutral weight without needing a special
case at all. Record this in `WEIGHTS_PROVENANCE.md` — it is the reason a step v1
budgeted for disappeared.

### 4.2 The actual weights (parsed, male, England 2011–2015)

| SOC | Sub-major group | Deaths | SMR | 95% CI | Weight used |
|---:|---|---:|---:|---|---:|
| 11 | Corporate managers and directors | 337 | 28 | 26–32 | **28** |
| 12 | Other managers and proprietors | 474 | 93 | 85–102 | *100* |
| 21 | Science, research, engineering and technology professionals | 385 | 47 | 42–52 | **47** |
| 22 | Health professionals | 185 | 84 | 72–97 | **84** |
| 23 | Teaching and educational professionals | 215 | 68 | 59–78 | **68** |
| 24 | Business, media and public service professionals | 306 | 45 | 40–50 | **45** |
| 31 | Science, engineering and technology associate professionals | 162 | 61 | 52–71 | **61** |
| 32 | Health and social care associate professionals | 65 | 86 | 66–109 | *100* |
| 33 | Protective service occupations | 222 | 83 | 73–95 | **83** |
| 34 | Culture, media and sports occupations | 311 | 120 | 107–134 | **120** |
| 35 | Business and public service associate professionals | 463 | 58 | 52–63 | **58** |
| 41 | Administrative occupations | 366 | 72 | 65–80 | **72** |
| 42 | Secretarial and related occupations | 25 | 59 | 38–87 | *100* (deaths < 50) |
| 51 | Skilled agricultural and related trades | 325 | 169 | 151–188 | **169** |
| 52 | Skilled metal, electrical and electronic trades | 942 | 109 | 102–116 | **109** |
| 53 | Skilled construction and building trades | 1,409 | 163 | 155–172 | **163** |
| 54 | Textiles, printing and other skilled trades | 383 | 109 | 98–120 | *100* |
| 61 | Caring personal service occupations | 258 | 118 | 104–134 | **118** |
| 62 | Leisure, travel and related personal service | 136 | 94 | 79–111 | *100* |
| 63 | Community and civil enforcement (SOC 2020 only) | — | — | — | *100* |
| 71 | Sales occupations | 265 | 63 | 56–71 | **63** |
| 72 | Customer service occupations | 75 | 64 | 51–81 | **64** |
| 81 | Process, plant and machine operatives | 671 | 108 | 100–117 | *100* (see §4.3) |
| 82 | Transport and mobile machine drivers | 924 | 108 | 101–116 | **108** |
| 91 | Elementary trades and related occupations | 782 | **292** | 272–313 | **292** |
| 92 | Elementary administration and service occupations | 1,002 | 103 | 97–110 | *100* |

Major-group SMRs (Table 2), for reference only — **not** used as fallbacks (§4.3):
1: 48 · 2: 53 · 3: 73 · 4: 71 · 5: 135 · 6: 109 · 7: 63 · 8: 108 · 9: 144.

The spread is **28 to 292** across 26 groups, against today's unweighted count of
three major groups. That is the case for building the layer.

### 4.3 Weight rule — corrected

v1's rule was "deaths ≥ 50 and 95% CI excludes 100, **else fall back to the parent
major group**". Applied to the real table, the fallback is actively harmful:

- **92 Elementary administration and service** — SMR 103 (97–110) on 1,002 deaths,
  a *precise* estimate of "no different from average". The parent rule would
  replace it with major group 9's **144**, which is almost entirely produced by
  its sibling 91 (292). That would assign elevated risk to **1.2 million men** on
  the strength of a different occupation.
- **12 Other managers** — 93 (85–102) would be pulled down to major group 1's 48,
  again on the strength of a sibling (corporate managers, 28).
- 54 (109 → 135) and 62 (94 → 109) move the same wrong way.

The parent group is not more informative about a sub-major group than the
sub-major group's own well-estimated SMR; it is contaminated by its siblings.

**Corrected rule: if deaths < 50 OR the 95% CI includes 100, use SMR = 100
(neutral).** A CI spanning 100 means "no evidence this group differs from the
working-age male average", and neutral is what that sentence translates to. This
attenuates toward the mean rather than toward a sibling-driven parent, and it
resolves SOC 2020's new group 63 with no special case.

This retains 18 of the 25 SOC 2010 groups at their fitted value and neutralises
7; with SOC 2020's group 63 also neutral, **8 of the 26 applied weights are
neutral**. Group **81**
sits exactly on the boundary (CI 100–117); treat an inclusive bound as spanning,
neutralise it, and note that the choice moves group 81 by 8 SMR points — §7.2
sensitivity-tests it.

**Attenuation to document.** Sub-major weights understate the sharpest signals:
elementary construction is 3.7 at unit level inside group 91's 2.92, and care
workers ~2.0 inside group 61's 1.18. Table 5 has the unit-level figures; record
the comparison in `WEIGHTS_PROVENANCE.md` so the attenuation is a known,
quantified choice rather than a silent loss.

**Do not hand-add a farmer uplift.** Farmers were *not* statistically elevated in
2011–2015; agricultural *trades* were (group 51, 169), partly via firearm access
(12.6% of those deaths vs 1.7% nationally).

**v1 anchors that were wrong**, corrected above — do not re-import them: `12 Other
managers` is 93, not ~50 (the ~50 was major group 1); `61 Caring` is 118, not ~200
(the ~200 was care workers, a unit group inside it); `92` is 103, so major group
9's 144 is almost entirely group 91.

---

## 5. Denominator — decided

v1 inherited today's denominator (males **in employment**) without comment. That
means an area where 40% of men do not work is scored purely on the mix of the
60% who do, and joblessness — the strongest single correlate in this space —
enters the engine only via IMD's employment domain.

**Decision: keep males in employment as the denominator, and carry the
not-employed share as a separate diagnostic, not as part of this index.**

Rationale: folding non-employment into `occupation_proxy` would double-count
IMD's employment domain, which already sits in `need_index` at weight 0.40, and
would make the two most heavily weighted components substantially the same
variable — exactly the collinearity ADR-0001 says the LA fit cannot resolve.
This layer's job is *composition given employment*; deprivation's job is *how
much employment there is*.

Category `-8` is still fetched, because it is needed to compute
`male_not_employed_share` for the §7 diagnostics, and because the denominator
choice must be auditable rather than implicit.

---

## 6. Method

1. **Weights.** Parse Table 3 and apply the rule in §4.3.
2. **Bridge.** SOC 2010 → SOC 2020 sub-major is an identity join plus group 63
   (§4.1); group 63 resolves to neutral with no special case.
3. **Fetch composition** from the two grains (§2.1): LSOA male counts by major
   group (Nomis RM107, one request) and MSOA male counts by sub-major group
   (ONS custom API, 19 area-chunks). Derive and validate LSOA→MSOA (§2.2).
4. **Score** each area:

   ```
   composite_smr(msoa, M) = Σ_{g∈M} within_share(msoa, g) × smr_g
   occupation_proxy(lsoa) = Σ_M major_share(lsoa, M) × composite_smr(msoa, M) / 100
   ```

   where `major_share` is over males **in employment** (§5). An MSOA with no men
   at all in a major group has no local mix to use, so it falls back to the
   **national** within-major composition rather than being dropped.

   This is a composition index, not a rate — SMRs are indirectly standardised,
   so the sum is not interpretable as a rate and must never be labelled as one.
5. **Emit** to `data/interim/fact_occupation.parquet`, keyed `area_code`.
   `score.py` already converts every component to a within-nation percentile, so
   **do not pre-normalise, z-score or percentile here** — that would double-rank
   it.
6. **Wales.** The SMR source is **England-only**. Applying English SMRs to Welsh
   composition affects only the ordering *within* Wales (scoring is
   within-nation), and no Welsh alternative exists. Acceptable, but it is an
   assumption and must appear in `caveats.py`, not just in this file.

### 6.1 The deprivation residual — decided (v1's §5)

v1 asked for both a raw and an IMD-residualised score and never said which one
the engine should use. Scoring the residual would break ADR-0001: it makes a
**fitted** quantity the input to a **declared-prior** architecture, and it
residualises occupation against deprivation while isolation stays raw, which is
arbitrary.

**Decision: the raw index is scored. The residual is a diagnostic only.**

Compute the residual of `occupation_proxy` on the deprivation score and write it
to `data/output/occupation_diagnostic.json` — never to `fact_score.parquet`.
This follows the `no_car_share` precedent in [score.py](src/score.py): computed
outside `prepare_components`, structurally unable to reach `need_index`,
`priority_score`, the tiers or the sensitivity draws.

Its purpose is to answer *"does this layer earn its 0.35 weight, or is it IMD
wearing a hat?"* — the same job `calibrate.py` does for the declared weights.
Report the raw↔IMD correlation, the residual variance share, and the areas where
raw and residual diverge most. The expected divergences are the ones that
justify the layer: rural agricultural areas that are not income-deprived, and
affluent districts with large trades workforces.

### 6.2 Age interaction

Report `male_45_64_share` as a separate column (the peak male rate is 50–54).
`geography.py` already sums Nomis RM121 bands 7–17 for 16–64, so the bands are
in hand. **Not baked into the occupation score**, and not scored in v1.

---

## 7. Validation, in the engine's own currency

v1 proposed rank correlations. The engine judges every change by whether it
moves the set you would act on, so these are restated against the existing
[sensitivity.py](src/sensitivity.py) machinery (`decision_n: 20`,
`contention_band: 100`):

1. **Kill criterion, run first.** Spearman of new vs current `occupation_proxy`,
   and top-20 displacement of the final shortlist. **If the shortlist does not
   move, do not ship the layer** — 27 API calls, an Excel dependency and a new
   weights file are not worth a re-labelling. Record the number either way; a null
   result here is a genuine finding about the major-group proxy and belongs in
   the methodology notes.
2. **SMR uncertainty is a fourth sensitivity axis.** `weights.csv` carries
   `lcl_95`/`ucl_95`. Re-score with CI-filtered vs point-estimate weights and
   report decision-set displacement, not just rank correlation.
3. **Composition spot-check.** High construction / agricultural-trades areas
   should rank in the top deciles; commuter-professional areas near the bottom.
4. **Regional sanity, read correctly.** Aggregated scores should not contradict
   the North East / North West being high and London low. Treat this as a
   *failure detector only* — passing it is weak evidence, since London's low male
   suicide rate is not primarily occupational.
5. **Residual diagnostic** per §6.1.
6. **The calibration check is no longer independent — say so.** `calibrate.py`
   regresses LA suicide counts on the proxies. Now that occupation is built from
   a suicide-by-occupation gradient, a positive association is close to
   guaranteed, and on the first real run occupation became the *strongest*
   univariate term (RR 1.153, CI [1.121, 1.185]) ahead of deprivation (1.102).
   That is not evidence occupation belongs in the index. It still tests
   something real — whether the 2011-2015 national occupational gradient
   reproduces in 2020-2024 between-area variation — but the veto's meaning has
   narrowed, and `weights.json`, the PDF and the map copy must say so.

---

## 8. Engine plumbing (v1 omitted all of this)

The layer is not done when the parquet is written. Required:

- **Keys.** `area_code`, not `lsoa21cd`; output `fact_occupation.parquet` with
  `occupation_proxy` so `score.py` needs no change to consume it.
- **`src/caveats.py`** — the single source of caveat copy for both map and PDF.
  The existing entry stating that occupation is measured "at the broadest
  occupational grouping, which is the only occupation-by-sex breakdown published
  at this level" **becomes false with this change and must be rewritten**, not
  appended to. Add: 2011–2015 SMR vintage, England-only SMRs applied to Wales,
  residence-based, employed-only denominator.
- **`config.yaml`** — update the `vintages:` entry for occupation to name the ONS
  custom API, the SMR vintage, and the sub-major resolution.
- **`data/raw/README.md`** — replace the RM107 row with two rows in the
  *automatically fetched* table: the ONS custom API (§2.1) and the SMR workbook
  (§3). Neither is manual, so neither belongs in the "Manual / not-yet-automated
  sources" section.
- **`factor_breakdown`** — surface the top 3 contributing sub-major groups with
  their shares in the per-area JSON in `score.py`, so the map can explain *why*
  an area scores high on occupation. Without this the extra resolution is
  invisible to the user, which is most of its value.
- **Tests** — a fixture-based test that the SMR bridge covers all 26 groups with
  no nulls, that `63` uses its documented fallback, and that the synthetic mode
  still runs without network access.
- **Synthetic mode** must keep working: the fixture generator needs to emit the
  26-group shape, or the adapter must degrade to the current proxy under
  `mode: synthetic`.

---

## 9. Known limitations (carry into the methodology notes)

- SMRs are **2011–2015**, deaths-registered, **England only**, ages 20–64, SOC
  2010 — applied to 2021 SOC 2020 composition, and applied to Wales (§6.5).
- **Within-major composition is MSOA-smoothed** (§2.1). All LSOAs in an MSOA
  share the same answer to *which* skilled trades or *which* elementary
  occupations their men do; they differ only in *how many*. An LSOA whose
  occupational mix is unlike its MSOA neighbours' is measured imprecisely, and
  the layer will understate genuinely local concentrations.
- **8 of the 26 applied weights are neutral** (§4.3) because their CI spans 100,
  their death count is under 50, or (group 63) they postdate the SMR vintage.
  The index is therefore driven by the 18 groups with a signal — above all group 91 (2.92) and groups 51/53
  (1.69/1.63). Areas whose male workforce sits mostly in neutralised groups are
  scored near 1.0 by construction, which is the honest answer but a flat one.
- **~3 in 10 suicide records had no occupation recorded**, and that missingness
  is not random (it correlates with not being in work), which biases the SMRs
  themselves.
- Occupation at death registration is **informant-reported** and may reflect
  lifetime rather than current occupation.
- Census 2021 applies **cell-key perturbation**; small male workforces make
  small-area shares noisy. This bites hardest on the "top 3 contributing groups"
  in the factor breakdown — treat those as indicative, not exact.
- The census measure is **residence-based**: where high-risk workers live, not
  where they work. For siting a peer-support group, residence is the right base.
- Construction risk has **risen** since the SMR vintage (GCU 2024). v1 applies
  **no trend uplift** — it would be an assumption dressed as ONS data, and the
  engine's standard is that weights are declared and defensible. Revisit only if
  §7.1 shows the shortlist is insensitive to it, in which case it is moot anyway.
- **Ecological.** It scores areas' occupational mix, not individuals, and cannot
  establish causation. Selection effects (higher-risk men sorting into certain
  trades) are part of what it captures — which is fine for *targeting support
  provision*, this engine's only purpose.

---

## 10. Result of the first real run (2026-08-31)

**Built and shipped.** The layer behaves as designed, and the shortlist did not
move. Both halves of that sentence matter.

**It works.** Boston (Lincolnshire) tops the occupation factor on elementary
trades, process plant and drivers; Kensington and Chelsea sits at the bottom on
corporate managers and business/media professionals. London is the lowest region
(0.819), Wales, the North East and Yorkshire the highest. The biggest risers
against the old major-group proxy are rural agricultural areas — Pembrokeshire,
Northumberland, Gwynedd — exactly the places §6.1 predicted IMD misses. The
biggest fallers are Rugby and Birmingham, where the old proxy scored warehouse
and logistics workers as high-risk because they sit in major group 9; their
measured SMRs are 103 and 108, i.e. average. Correlation with deprivation fell
from 0.679 to 0.650.

**It changed no decision.**

| Measure | Result |
|---|---|
| Occupation proxy, Spearman old vs new | 0.962 |
| Decision set (top 20, reach — the shipped view) | **20/20 retained, 0 in, 0 out** |
| Priority Spearman (reach / per-capita) | 0.996 / 0.995 |
| Top-100 overlap (reach / per-capita) | 92% / 76% |
| Three-axis stability verdict | STABLE, unchanged |

§7.1's kill criterion says do not ship a layer that does not move the shortlist.
It was **overruled deliberately**, on a ground the criterion did not cover: the
old proxy is not merely differently ordered, it is *wrong* about logistics and
warehouse areas, calling them elevated where the evidence says average. That is
a correctness and explainability defect a domain expert would spot on the map,
and the factor breakdown can now name the occupations driving a score instead of
showing an unexplained percentile.

The null result is itself a finding, and belongs in the methodology notes: **at
the resolution the census actually publishes, the male occupational mix of an
area is nearly a monotone transform of the crude manual-work share.** Weighting
26 groups by their measured suicide rates sharpens what the factor *means*
without much changing whom it ranks.

---

## 11. Step 2 result — the residual diagnostic (2026-08-31)

Implemented as `src/occupation_diagnostic.py`, wired into the pipeline as a
non-blocking step, writing `data/output/occupation_diagnostic.json`. Isolation is
measured the same way, because singling out occupation would be arbitrary.

**Both factors carry real independent information.** On within-nation
percentiles — the scale `score.py` actually weights:

| Factor | Overlap with deprivation | Variance explained | **Independent** |
|---|---:|---:|---:|
| Occupation | 0.654 | 42.8% | **57.2%** |
| Isolation | 0.629 | 39.5% | **60.5%** |

And measuring occupation properly *increased* its independence: the old
major-group proxy was 53.7% independent, the SMR-weighted one is 57.2%. The
0.35 weight is defensible on this evidence.

**Where occupation adds most** is exactly where §6.1 predicted — rural
agricultural areas that are not income-deprived:

| Area | Occupation pctl | Deprivation pctl |
|---|---:|---:|
| Powys 014C | 0.99 | 0.06 |
| Richmondshire 005B | 1.00 | 0.09 |
| Eden 005D | 0.99 | 0.10 |

And where it says *less* than deprivation: Manchester, Greenwich, Southwark,
Westminster — deprived inner-city areas whose men are not in manual trades
(occupation 0.01–0.05, deprivation 0.85–0.95).

### 11.1 The finding that matters more than the weight

**The independent signal is real, and it cannot reach the shortlist.**

The ten areas where occupation adds most rank at a **median of 11,763 of 35,672**
on the per-capita view. The best of them is 5,203rd. None is remotely near the
decision set.

The arithmetic is not subtle. An area at the 99th percentile on occupation and
the 6th on deprivation scores `need_index ≈ 0.42` — below the median — because
deprivation (0.40) and isolation (0.25) together outvote occupation (0.35) nearly
two to one, and rural areas that score high on occupation tend to score low on
both. Occupation cannot carry an area on its own by construction.

So this is also the answer to why §10's shortlist did not move: not because the
layer lacks independent information, but because the weighting structure
systematically outvotes the part that is independent.

**That is a weighting question, not a data question, and it is a decision for
the client, not this spec.** Two readings, both defensible:

- *Working as intended.* A prosperous, well-connected rural area with a lot of
  agricultural trades may genuinely not be where the next group should open.
- *A blind spot.* Agricultural trades carry one of the highest measured male
  suicide SMRs (169), rural isolation is real, and the engine structurally
  cannot surface these places whatever their occupational risk. If AMC wants to
  see them, the fix is an explicit change — a rural/agricultural view, or a
  floor rule — not a quiet re-weighting.

Either way it must be **stated on the map face**: the ranking is driven by
deprivation and isolation, and a high occupational-risk area with low deprivation
will not appear on it.
