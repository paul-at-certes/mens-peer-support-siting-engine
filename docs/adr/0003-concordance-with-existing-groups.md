# ADR 0003 — The need surface agrees with AMC about neighbourhoods, not about towns

**Status:** accepted · **Date:** 2026-09-01 · **Evidence:** `spikes/group_need_concordance.py`, `data/output/concordance.json` ·
**Amends:** `docs/design.md` §7 (adds a third validation check, ahead of back-testing) and §8 (adds one limitation)

## Context

Every review of this tool opens with the same question, and until today the repo
could not answer it: **does it agree with what we already know?**

Andy's Man Club has opened 354 groups over a decade of local judgement — venue
offers, volunteer availability, someone who knew someone. The need surface is
built from open data and has never seen any of it. If the surface rates those
354 places as unremarkable, the surface has a problem. If it rates them highly,
that is corroboration from a direction the model cannot have learned from.

`docs/design.md` §7 lists three validation checks. #2 (sensitivity) is shipped.
#1 (face validity) needs people in a room. #3 (back-testing) asks whether areas
the model prioritised went on to sustain good attendance — it needs opening
dates and attendance figures, **neither of which exists in any open source**;
only AMC could supply them. So #3 remains out of reach, and this ADR does not
close it. What is measurable today is the weaker *concordance* question, and
nobody had asked it.

## The trap this had to avoid

`priority_score` subtracts a supply surface built **from these 354 groups**.
Scoring the groups against it would be marking the model's homework with its own
answers: every group sits, by construction, where supply is maximal. The size of
that artefact is worth stating, because it is large — the same 292 venue areas
sit at the **68th** percentile of `need_index` nationally and the **25th** of
`priority_score`. Everything below therefore uses `need_index` and its
components only. The supply half of the model is not under test here and cannot
be.

Existing groups are also **corroboration, not ground truth**. AMC's siting
reflects who offered a hall as much as where need is. Agreement raises
confidence; disagreement is not automatically the model's error, and is not read
as one here.

## What was measured

354 groups (harvest of 2026-09-01), assigned to small areas **by postcode**
through the ONS Postcode Directory (May 2026, LSOA 2021). 353 matched; the one
failure is recorded as `AB54 8J`, which is not a postcode. 61 groups are in
Scotland or Northern Ireland and fall outside the scored surface, leaving
**292** in England & Wales. Permutation tests, 20,000 draws, seed 11.

Two things about the assignment are worth recording:

- **Nearest population-weighted centroid agrees with the postcode only 47.9% of
  the time.** In a dense town the nearest centroid is routinely the next LSOA
  over. Centroid assignment would have been wrong for more than half the groups
  and is not fit for a neighbourhood-grain measurement. Nothing in `src/` does
  this — the pipeline uses group coordinates for *distance*, never to assign a
  group to an area — but it is the obvious shortcut and it does not work.
- **Six postcodes were hand-entered without their space** (`SS155NX` for
  `SS15 5NX`). An exact join drops them silently, and a dropped group is
  indistinguishable from a Scottish one: both are simply absent from an England
  & Wales analysis. Normalising recovered two E&W groups, 290 → 292.

## Findings

### 1. Within a local authority, the surface and AMC agree — strongly

The venue's own small area sits at a mean **64.1st percentile** of `need_index`
among the small areas of its own LA, against a null of 50.0 (p < 5e-05, at the
20,000-draw floor). The median is the 71.1st. 71.2% of venues are above their
LA's midpoint and 21.6% are in its top decile.

| venue's rank within its own LA | groups |
|---|---:|
| bottom decile | 17 |
| 0.10 – 0.25 | 26 |
| 0.25 – 0.50 | 40 |
| 0.50 – 0.75 | 79 |
| 0.75 – 0.90 | 67 |
| top decile | 63 |

This is the finding that carries weight, because it **never compares one local
authority with another** and so cannot be explained by where AMC happens to
operate. Given a town, the surface and a decade of local judgement pick the same
part of it.

### 2. Between local authorities, the agreement is entirely regional

Nationally, the 151 LAs with a group average 0.527 on need against 0.463 for the
180 without (+0.064, p < 5e-05) — which looks like strong agreement about *which
towns*. It is not. Permuting the has-a-group label **only within region** leaves
the same +0.064 difference indistinguishable from chance (**p = 0.21**).

The reason is visible in the raw table: AMC began in Halifax and grew outward,
and in its heartland there is nothing left to choose between.

| region | LAs | with a group | mean need, with | without |
|---|---:|---:|---:|---:|
| North East | 12 | **12** | 0.636 | — |
| Yorkshire and The Humber | 21 | **21** | 0.563 | — |
| North West | 39 | 32 | 0.583 | 0.590 |
| West Midlands | 30 | 11 | 0.556 | 0.491 |
| South West | 30 | 15 | 0.541 | 0.475 |
| East Midlands | 35 | 15 | 0.530 | 0.501 |
| Wales | 22 | 8 | 0.489 | 0.531 |
| London | 33 | 6 | 0.461 | 0.432 |
| East of England | 45 | 9 | 0.451 | 0.450 |
| South East | 64 | 22 | 0.392 | 0.416 |

Two whole regions are saturated, so they contribute no signal at all. Where
there is a choice the direction is mixed: positive in the West Midlands, South
West, East Midlands and London; **negative** in Wales, the South East and the
North West.

This is the honest reading: **the national result is founder geography, not
corroboration.** The tool's between-town judgement is untested by this exercise,
which is exactly the judgement a national expansion shortlist is being asked
for.

### 3. A venue's own small area over-states the need it serves, by 10 points

On the national `need_index` scale, the 292 venue areas average the **68.0th**
percentile — but the areas each group is actually nearest to (median catchment:
54 small areas) average the **57.7th**. A drop of **10.3 percentile points**.

This is not a flaw in AMC's choices; it is what a catchment is. A hall sits in a
high-need pocket and draws from the surrounding, more average, area. But it does
qualify how the shortlist should be read: **an LSOA-grain rank names a pocket,
and a group opened in that pocket will serve a wider and blander area than the
rank implies.** Nothing on the map face says so today.

### 4. All three components are corroborated, occupation least

Repeating finding 1 per component, within LA:

| component | mean percentile of the venue's own area | weight in `need_index` |
|---|---:|---:|
| isolation | **68.0** | 0.25 |
| deprivation | 62.2 | 0.40 |
| occupation | **57.5** | 0.35 |

All three beat the null at p < 5e-05, so none of them is dead weight — the
surface is not agreeing with AMC on deprivation alone.

The ordering is the interesting part, and it is close to an inversion of the
declared weights: the component AMC's choices corroborate **most** carries the
**lowest** weight, and the most heavily weighted one comes second. This is
suggestive and it is **not** grounds to move a weight. Per
[ADR 0001](0001-calibration-as-veto.md) the weights are a declared prior, and
letting a fitted quantity set them is the thing that ADR exists to forbid; a
concordance ranking is a fitted quantity wearing a different hat, and 292 venue
placements shaped by hall availability are a weaker basis than the LA-level
outcome model that was already refused this job. Recorded as a question for the
face-validity conversation, not as a change.

## Decision

1. **Accept finding 1 as corroboration of the neighbourhood-grain surface, and
   record finding 2 as a named blind spot in the validation, not as support.**
   The repo may claim that the surface agrees with a decade of local judgement
   *about which part of a town*. It may **not** claim the surface is validated
   about which town, and no README or app copy will say so.
2. **`docs/design.md` §7 gains a third check** — *concordance*, sitting above
   sensitivity and below back-testing in strength, and explicitly distinct from
   the back-test it is easy to mistake for.
3. **§8 gains finding 3** as a limitation: a rank names a pocket, a group serves
   a catchment.
4. **The spike stays a spike.** It changes no weight, no veto and no rank, and
   it does not run in `python -m src.pipeline`. Its numbers move only when the
   provision harvest does.
5. ~~**No app copy yet.**~~ **Amended 2026-09-01, same day — see below.**
   The findings are now on the map face, in the PDF and in the guide, reading
   `concordance.json` live.

## Consequences

- The first question in the room now has an answer, with its own limits attached
  rather than discovered by whoever is being pitched to.
- Finding 2 sharpens what the face-validity conversation is *for*. The check the
  data cannot supply is precisely the between-town one, so the top of the
  shortlist — which is mostly towns AMC is not in — is the part that most needs
  human review. That is now the stated purpose of check #1, not a formality.
- Finding 4 gives that conversation a specific question to put: the weighting
  leans hardest on deprivation, and AMC's own choices lean hardest on isolation.

## What would change this

- **Opening dates.** With them, finding 1 becomes a genuine back-test: rank the
  areas as they stood before each group opened, and the concordance is
  predictive rather than contemporaneous. This is a request to make of AMC, and
  it is the single piece of non-open data that would most improve the tool.
- **Groups outside the North.** As AMC expands, the saturated regions in
  finding 2 stop being saturated and the between-town question becomes
  answerable. Re-run then.
- **A second organisation's locations.** Concordance with one charity's history
  is concordance with one charity's history. A second peer-support network,
  sited independently, would separate "agrees with AMC" from "agrees with where
  need is".

---

## Amendment, 2026-09-01 — decision 5 reversed

Decision 5 originally withheld all app copy, reasoning that the guide promises
every figure is read live from pipeline outputs and a hardcoded `64.1` would
break that. **The reasoning about hardcoding was right; the conclusion did not
follow.** Reading `concordance.json` when it exists is the same optional-diagnostic
pattern `assurance_notes` already uses for `weights.json` and `sensitivity.json`
— present, it supplies the figures; absent, its absence is itself reported. No
drift is possible, because no number is typed anywhere.

The original decision also required the file to be a **pipeline** output before
it could be read. That bar was stricter than the architecture needs and stricter
than the repo applies elsewhere: `_diagnostic_path` exists precisely so a config
written before a diagnostic existed degrades the copy instead of taking down the
map. Keeping the finding off the tool's face to satisfy it had a real cost —
`CLAUDE.md`'s guardrail is that uncertainty is surfaced **on the map face**, and
finding 2 is the most decision-relevant uncertainty this tool has. Someone
reading the shortlist is reading a list of towns, which is exactly what the
check could not corroborate. Withholding that is not caution.

What shipped:

- `caveats.py` gains `concordance_note` (an assurance note, alongside the
  calibration veto and the stability verdict) and `catchment_note` (a data
  caveat). Because that module is the single source for both renderers, the
  findings reach the map face and the PDF together.
- `catchment_note` states the pocket-vs-catchment limitation **unconditionally**
  — it follows from the grain of the ranking, not from any measurement — and
  adds the measured figures only when the diagnostic supplies them. It asserts
  no percentage it has not read.
- The guide gains §11, *Does it agree with where groups already are?*, written
  as two headed halves so the one that failed is not a footnote to the one that
  passed. The old §11 becomes §12.
- Decision 4 stands: the spike is still not a pipeline step, and `python -m
  src.pipeline` does not run it. Most builds will have no `concordance.json`,
  and both notes say so plainly rather than falling silent.

`tests/test_concordance_caveats.py` (14) covers both branches of both notes, an
unreadable diagnostic, a config predating the key, and — the point of the file —
that the copy never reports the flattering half alone.

Writing those tests found a live bug in `assurance_notes`: it returned early when
`sensitivity.json` was missing, so **any note appended after the stability check
was silently dropped on exactly the builds with the least assurance to spare.**
The early return is gone. Same shape as the two failures PR #4 closed: not a
wrong number, but a true page with something missing from it.
