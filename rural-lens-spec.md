# Rural Lens + Occupational Blind-Spot Flag — Implementation Spec

**Project:** Men's Peer-Support Siting Engine (see `CLAUDE.md`)
**Status:** Scoped, measured, not built. All figures below verified 2026-08-31.
**Scope:** Two things only — a **view** and a **flag**. No weight changes.

---

## 1. Read these first

- `CLAUDE.md` — the brief, the locked decisions, the guardrails.
- `docs/adr/0001-calibration-as-veto.md` — why weights are a declared prior and
  a fitted quantity may never set them.
- `occupational-risk-layer-spec.md` §10 and §11 — the work this follows from.
- `src/score.py` — note how `no_car_share` is attached *after* scoring,
  deliberately outside `prepare_components`, so it cannot reach a rank. Both
  things built here follow that precedent.

Regenerate the numbers any time with `python -m src.occupation_diagnostic`
(writes `data/output/occupation_diagnostic.json`).

---

## 2. Why this exists

The occupation factor was rebuilt to weight 26 SOC sub-major groups by their
measured male suicide SMRs (28–292). It carries real independent information —
57.2% of it is not explained by deprivation. **But that information cannot reach
the shortlist.**

The areas where occupation says most sit at:

| Factor | Median percentile |
|---|---:|
| Occupation | **0.96** |
| Deprivation | 0.22 |
| Isolation | **0.22** |

Occupation (0.35) is outvoted two-to-one by deprivation (0.40) and isolation
(0.25), *both* of which say "low need". The isolation half is the interesting
part: our isolation proxy is male single/separated/divorced plus one-person
households. Farming areas are married-couple households, so they score
bottom-quartile — while the thing that actually isolates a man there is distance,
working alone, and no reason to be in a room with other men. **The engine
measures social-relationship isolation and calls it isolation.** For remote areas
that is the wrong construct, and it costs them twice.

Consequence: those areas rank at a **median of 8,541 of 35,672** (best 1,678),
and only **6 of the current top 100 are rural** against 17% of all areas.

This spec does **not** fix that. Fixing it means changing what `need_index`
measures, which is a bigger, separate decision (§7). This makes the problem
**visible** instead.

---

## 3. What has already been measured (do not re-derive)

### 3.1 The classification

`LSOA21_RUC21_EW_LU` on the ONS Open Geography Portal ArcGIS host
(`https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/`),
fields `LSOA21CD, LSOA21NM, RUC21CD, RUC21NM, Urban_rural_flag`. Published on
**LSOA 2021 for England and Wales** — joins straight onto the spine, no
crosswalk. Fetch it with the existing `arcgis_query_all` helper in `src/fetch.py`
(`page_size=2000`), cached like every other lookup.

Two axes crossed — settlement size × **remoteness**:

| RUC21CD | Class | LSOAs |
|---|---|---:|
| UN1 | Urban: Nearer to a major town or city | 27,106 |
| UF1 | Urban: Further from a major town or city | 2,451 |
| RLN1 | Larger rural: Nearer to a major town or city | 2,127 |
| RSN1 | Smaller rural: Nearer to a major town or city | 1,735 |
| RSF1 | Smaller rural: Further from a major town or city | 1,232 |
| RLF1 | Larger rural: Further from a major town or city | 1,021 |

6,115 Rural / 29,557 Urban by `Urban_rural_flag`.

### 3.2 Remoteness, not rurality, is the axis that matters

| Class | occ | dep | iso | drive | median rank |
|---|---:|---:|---:|---:|---:|
| Smaller rural: **Further** | 0.76 | 0.32 | 0.21 | 34m | 9,438 |
| Smaller rural: **Nearer** | 0.35 | 0.21 | 0.16 | 20m | 23,043 |
| Larger rural: **Further** | 0.79 | 0.50 | 0.46 | 29m | 5,913 |
| Larger rural: **Nearer** | 0.45 | 0.34 | 0.34 | 17m | 19,296 |
| Urban: **Further** | 0.69 | 0.59 | 0.56 | 25m | 10,509 |
| Urban: **Nearer** | 0.48 | 0.55 | 0.56 | 12m | 18,838 |

"Nearer" rural areas have *low* occupational risk. The signal is entirely in the
"Further" classes — and remote **urban** areas (UF1) carry the highest
deprivation of any class. **Cut the view on remoteness (`*F1`), not on
`Urban_rural_flag`.** A rural-only cut would drop 2,451 remote urban LSOAs that
belong in the same conversation.

### 3.3 The blind spot, as explored

Using an exploratory cut of occupation ≥ 90th percentile and deprivation ≤ 30th:
**158 areas of 35,672 (0.4%)**, of which 104 (66%) are the single class RSF1 and
87% are rural. Medians: supply 0.15 (all areas 0.37), drive 32 min (14), male
16–64 population 428 (488). Concentrated in a handful of councils — Powys 30,
Eden 17, Shropshire 11, Richmondshire 10, Herefordshire 7, Gwynedd 6,
Pembrokeshire 5, Allerdale 5.

> **That 90/30 cut was exploratory and is NOT a specification.** `CLAUDE.md`
> requires thresholds set from what the number means for the decision, never
> tuned until the data passes. Choose and defend the flag's threshold on its own
> terms, state the reasoning in the code, and report how many areas it catches.
> If a defensible threshold catches 40 areas or 400, that is the answer.

---

## 4. Build this

### 4.1 The lens — a remoteness view

A third view alongside per-capita and reach: rank areas **within** the remote
classes, on the existing `priority_score`. It re-ranks a subset; it does not
re-score anything.

- Ingest RUC21 as a descriptive attribute. Follow the `car_access` precedent:
  it must be **structurally unable** to reach `need_index`, `priority_score`,
  the tiers or the sensitivity draws. A reviewer should be able to see that from
  the code, not have to trust it.
- Surface in `app/views/priority_map.py` as a view toggle, and as a section in
  `src/report.py`.
- **Use the per-capita view, not reach.** Reach multiplies by population, so
  remote areas always lose — and for a weekly physical group that is arguably
  correct, not a bug.

### 4.2 The flag — name what the ranking cannot see

Mark areas that are high on occupation and low on deprivation *and* isolation —
the condition described in §2 — wherever they rank. Show it in the per-area
breakdown and on the map.

- **Key the flag off the condition, not off geography.** It is mostly rural
  (87%) but also catches affluent districts with large trades workforces. "Rural"
  is a legible label for the *view*; the flag underneath is about the factor
  profile. Name it accordingly.
- `data/output/fact_tier.parquet` and the existing tier machinery may be the
  right home; check before inventing a parallel mechanism.

---

## 5. Constraints (these are the ones that will bite)

1. **No weight changes.** Not for rural, not for anything. Context-dependent
   weights would break the declared-prior architecture and invalidate the
   three-axis sensitivity harness.
2. **Do not put remoteness into `need_index`.** Tempting, and wrong here — see
   §7. The supply surface already rewards remoteness heavily (remote areas'
   median supply 0.12 vs 0.40; remoteness correlates 0.407 with drive time), so
   adding it to need would double-count distance. That is exactly the error
   step 2 existed to check for between occupation and deprivation.
3. **Do not score the residual.** Same reason as
   `occupational-risk-layer-spec.md` §6.1.
4. **`src/caveats.py` is the single source of caveat copy** for both map and PDF.
   The existing entry **"What this list will not show you"** currently ends *"If
   those places matter to you, they need looking for separately. This list will
   not surface them."* Once the flag exists **that becomes false and must be
   rewritten**, not appended to. This project has already shipped stale caveat
   copy twice; check it deliberately.
5. **Viability caveat.** A weekly peer-support group needs enough men in a room.
   These areas have normal-ish LSOA populations (median 428 male 16–64) but are
   geographically large. The view must say plainly that a conventional group may
   not be viable and the honest answer may be a peripatetic group or one in the
   market town.
6. **`mode: synthetic` must still run end-to-end with no network.** Either the
   fixture generates a RUC column or the adapter degrades cleanly.
7. **Tests**: the flag's threshold logic, the "cannot reach a score" guarantee,
   the synthetic path, and the RUC join covering all 35,672 areas with no nulls.

---

## 6. Definition of done

- `python -m src.pipeline` runs clean; `pytest` green.
- The map offers the remoteness view and shows the flag in the per-area
  breakdown; the PDF carries both.
- The number of flagged areas, and the threshold's justification, are printed by
  the pipeline and recorded.
- The "What this list will not show you" caveat is rewritten to match reality.
- A short results section appended to this file: what the view surfaces, which
  councils dominate, and whether it changes what you would actually do.

---

## 7. Explicitly out of scope

- **Putting remoteness or a sparsity measure into `need_index`.** This is the
  real fix for §2 and a genuine candidate, but it is a new factor: it needs its
  own declared-weight justification, it re-opens the calibration veto, it changes
  `need_index` for all 35,672 areas, and everything already validated needs
  re-validating. Decide it deliberately after seeing what the view surfaces.
- **Changing the siting unit for remote areas** (ranking market towns or
  travel-to-work areas instead of LSOAs). Plausible and separate.
- **Re-weighting anything.**

---

## 8. Results (built 2026-08-31)

Both things shipped. `python -m src.pipeline` runs clean, `pytest` is green (81
tests), and the map and the PDF carry the view and the flag.

### 8.1 The flag: 285 areas of 35,672 (0.80%)

**Rule: `occupation_proxy >= 1.00` AND `need_index < 0.50`.** Both numbers are
read off quantities that already mean something, and neither moved after the
count was seen.

`occupation_proxy` is a sum of male occupational shares weighted by each group's
measured male suicide SMR, so **1.00 is the identity value of the index, not a
percentile**: an area at 1.00 has an occupational mix carrying exactly the
average male suicide risk for men in work in England and Wales — an average that
includes every desk job in the country. There is nowhere for that number to
drift to. It happens to land at the 80th percentile nationally (proxy at p80 =
0.998), which is also the bar `report.py::_band()` already uses to call a factor
"high" in the prose printed for every shortlisted area, so the flag and the
breakdown beside it use the word to mean the same thing.

It is deliberately an **absolute** cut rather than a within-nation percentile.
`CLAUDE.md`'s within-nation rule exists because IMD, WIMD and SIMD are not
comparable across borders; this index is, being one census and one national SMR
schedule. Wales genuinely has more of these areas — 41% of Welsh LSOAs sit above
the national average against 18% of English ones — and a within-nation
percentile would hide that behind the border. It is the difference between 114
Welsh flags and 54.

`need_index < 0.50` is the midpoint of an index built from percentile ranks
(median on this run: 0.4986) — the ranking's own verdict of below-average need.
This **replaces §4.2's separate "low deprivation AND low isolation" cuts**, which
were two more invented constants standing in for the thing they were trying to
detect. Measuring the mechanism directly costs nothing in fidelity: the selected
areas sit at a median 23rd percentile on deprivation and 16th on isolation
anyway. The exploratory 90/30 grid was re-run and discarded; it fed nothing.

| | flagged | all areas |
|---|---:|---:|
| median rank (per-capita) | 10,833 | — |
| best rank of any flagged area | 5,282 | — |
| inside the national top 100 | **0** | — |
| in the shortlist tier | **0** | — |
| median supply index | 0.17 | 0.37 |
| median nearest group | 27 min | 14 min |
| median male 16–64 | 439 | 488 |

Councils: Powys 30, Carmarthenshire 20, Pembrokeshire 18, Brent 12, Ceredigion
10, Herefordshire 10, Dorset 10, Harrow 10, South Lakeland 9, Shropshire 9.
England 171 / Wales 114.

The flag makes one falsifiable claim about itself — that it is not restating the
shortlist — and it holds: nothing flagged reaches the top 100 under any tested
configuration. **85 of the 285 are not remote at all**, which is the check that
it is keyed to the factor profile and not to geography: Brent and Harrow are
outer-London neighbourhoods of skilled construction and elementary trades with
drive times of 5 to 13 minutes. By class, the flag concentrates hard in
`RSF1` — 13.5% of smaller-rural-further areas carry it, against 0.19% of
urban-nearer.

### 8.2 The view surfaces less than expected, and that is the finding

**Remote areas are already over-represented in the shortlist.** They are 13.2% of
all areas but **32 of the 57 shortlist-tier areas** and 49 of the national top
100. The supply surface is already doing this work, and doing it hard.

So the remoteness view's top 20 is, taken as a whole, **20/20 already inside the
national top 100** — Cornwall 11, Thanet 7, North Devon 2, all of them `UF1`
remote *urban* coastal. Cut on remoteness rather than rurality, per §3.2, and
the remote-urban classes dominate the very ranking the view was meant to escape.
The view only starts saying something new when narrowed within itself:

| cut | median national rank of its top 20 | already in top 100 |
|---|---:|---:|
| all remote (`*F1`) | 15 | 20/20 |
| rural-further (`RSF1`+`RLF1`) | 157 | 4/20 |
| smaller-rural-further (`RSF1`) | 976 | 0/20 |

That is why the map gained a **class filter** inside the remoteness view and the
PDF gained a **per-class table** underneath the ranked one. Without them a reader
would look at the view, see Cornwall and Thanet at the top, and reasonably
conclude it adds nothing. §3.2's call to cut on remoteness rather than rurality
was still right — dropping the 2,451 remote urban LSOAs would have been wrong —
but the classes have to stay separable inside the view or the point is lost.

**The view and the flag barely overlap.** Zero of the remoteness view's top 20
is flagged, under any of the three cuts above. They answer different questions:
the view asks "who is far away", which supply already largely captures; the flag
asks "whose risk is invisible to the need index", which nothing captures. Keying
the flag off the condition rather than off geography (§4.2) was load-bearing, not
stylistic.

### 8.3 Does it change what you would do?

**The view, on its own: not much.** It confirms the supply surface is already
finding remote coastal England, and it gives a way to look inside the remote
classes rather than a new set of candidates.

**The flag: yes, modestly, and in a specific way.** It converts "the ranking
cannot see these places" from a caveat into a 285-row list with names on it, led
by mid-Wales and the Welsh coast. Several are extreme on supply as well as
unseen — Isles of Scilly 001A (251 minutes to the nearest group), Pembrokeshire
008D (119), four Ceredigion areas between 112 and 115. Those are not shortlist
candidates by this tool's arithmetic and would not have come up in any
conversation it generated. The viability caveat matters here and is on both
surfaces: a median of 439 working-age men spread over a very large area may not
support a weekly group, and the honest answer may be a travelling group or one
in the market town.

**What it sharpens for §7.** The temptation to put remoteness into `need_index`
should now be weaker, not stronger. §5.2 warned it would double-count distance;
the measurement above shows the double-count would be severe, because remoteness
is *already* reaching the shortlist through supply at four times its base rate.
The dimension that genuinely cannot get through is the one ADR 0001 already
named as the open question: **how much weight high-risk-occupation share should
carry, and whether it measures need or measures "working-class male area"**. The
flag makes the cost of that answer visible — 285 areas — without pretending to
settle it.

### 8.4 Housekeeping

- `src/caveats.py` — **"What this list will not show you" is rewritten.** It
  ended *"This list will not surface them"*, which the flag made false. The new
  copy names the count, says the mark is a statement about the ranking rather
  than about the place, and says explicitly that it is not a recommendation to
  open a group. Three tests pin it: the false clause is gone when the flag has
  run, the old wording returns when it has not (on such a run it is true again),
  and a run with nothing flagged says so.
- Structural guarantee: `is_remote`, `ruc21_code` and `occupation_blind_spot`
  are attached in `score.py` after `apply_weights` returns, outside
  `prepare_components` — the `no_car_share` placement. `test_rural_lens.py`
  inverts every area's remoteness, re-scores, and asserts every ranking column is
  identical, rather than trusting the code to be read.
- `mode: synthetic` runs with no network: the fixture emits `remoteness.csv` from
  the real national class shares, and its `occupation.csv` now also carries an
  `occupation_proxy` on the SMR-index scale so the flag is not silently
  unreachable there.
