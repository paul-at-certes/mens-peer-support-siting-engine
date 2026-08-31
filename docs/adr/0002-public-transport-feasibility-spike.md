# ADR 0002 — Public transport is a feasibility question, not a second travel-time column

**Status:** accepted · **Date:** 2026-08-31 · **Evidence:** `spikes/pt_evening_access.py` ·
**Amends:** the public-transport note in `data/raw/README.md` and the "Still open" bullet of [ADR 0001](0001-calibration-as-veto.md)

## Context

The supply surface is car-only. Since 2026-08-31 it uses real OSRM road driving
times, which is an honest number for a man with a car and a misleading one for a
man without. `src/ingest/car_access.py` was built as the groundwork: it records
the share of households with no car or van per LSOA, flags on the map face where
the drive time is least representative, and states the intended next step —

> It is also the natural weight for blending car and public-transport access
> once that lands, so this is groundwork rather than a throwaway caption.

That plan had three named blockers: a time-of-day parameter on
`TravelTimeProvider`, round-trip feasibility, and a routing engine for the
35,672 × 354 matrix. The obvious next move was to change the interface and start
wiring an engine.

**It was the wrong next move.** The interface shape depends on what the answer
turns out to look like, and nobody had checked. So: a spike first, on five local
authorities, before any interface or scoring change.

## What was measured

Bus Open Data Service **national** GTFS (`feed_version 20260831_023903`, 63.5M
stop_times, 312,623 stops), Monday 2026-09-14, sessions 19:00–21:00. A Connection
Scan over the timetable, two scans **per group** — backward from the group arriving 19:00,
forward from it leaving 21:00 — with origins joining on by a walk to nearby
stops. An area counts as served only if a man can do **both** halves: arrive by
19:00 having left home no earlier than 17:30, and get home after the finish.

The scan itself is unit-tested against a hand-computable timetable
(`tests/test_pt_spike_csa.py`) — the only part of `spikes/` that is, because
every number below depends on it being right. Those tests earned their place
immediately: the first implementation charged an interchange penalty for
boarding your *first* vehicle, treating the start of a journey as a change of
vehicle. Fixing it moved Mansfield 27.9% → 30.9% and left every other headline
unchanged, but it also flipped Mansfield's binding constraint from the journey
in to the journey home — see finding 9.

Five LAs, all inside the current reach shortlist's top 1000, deliberately
spanning a core city to a rural district:

| LA | best `rank_reach` | median drive | median no-car |
|---|---|---|---|
| Nottingham | 6 | 11.0 min | 37.5% |
| Newark and Sherwood | 38 | 24.4 min | 14.0% |
| Mansfield | 54 | 17.7 min | 19.6% |
| Boston | 75 | 25.7 min | 12.2% |
| South Holland | 174 | 28.1 min | 10.9% |

## Findings

**1. The data exists, is free, and needs no API key.** BODS publishes GTFS for
all of GB at a stable URL, regenerated daily. That blocker was smaller than
assumed.

**2. Round-trip feasibility is bimodal by place, not continuous like drive time.**

| LA | round trip possible | dominant reason where not |
|---|---|---|
| Nottingham | **100%** | — |
| Mansfield | 30.9% | no way home |
| Newark and Sherwood | 0% | inbound too long/late |
| Boston | 0% | inbound too long/late |
| South Holland | 0% | inbound too long/late |

Within Nottingham the bus time *is* continuous (IQR 30–50 min). Between places
it is close to all-or-nothing. A percentile rank over a "public transport
minutes" column would be scoring a censored variable as if it were continuous,
and the areas with no service at all would pile up at one end with nothing to
separate them.

**3. Where evening buses exist at all, the trip takes about four times the drive.**
Median ratio 3.79×, IQR 3.20–4.37. Stable enough to quote.

**4. The extremes are robust; the middle band is a value judgement, not a
measurement.** Re-running under different rules for what a man would tolerate:

| | strict<br>(dep ≥18:00, ≤60 min) | baseline<br>(dep ≥17:30, ≤90 min) | generous<br>(dep ≥16:00, ≤120 min) | very generous<br>(dep ≥15:00, ≤150 min) |
|---|---|---|---|---|
| Nottingham | 82.7% | **100%** | 100% | 100% |
| Mansfield | 0% | **30.9%** | 73.5% | 94.1% |
| Newark and Sherwood | 0% | **0%** | 9.5% | 20.3% |
| Boston | 0% | **0%** | 0% | 0% |
| South Holland | 0% | **0%** | 0% | 0% |

Nottingham is served and Boston is not, under every rule tested. Mansfield swings
from 0% to 94% on assumptions no dataset can settle. This is the single most
important result: **a public-transport surface would import a new, powerful,
undeclared constant into a shortlist that is already gated hardest by its supply
side.**

**5. `no_car_share` is the wrong blend weight.** It was earmarked for exactly
this job. But across the 411 LSOAs, `corr(no_car_share, round_trip_ok) = +0.66`:

| | n | median no-car | median drive |
|---|---|---|---|
| round trip possible | 200 | **36.9%** | 11.9 min |
| not possible | 211 | **13.4%** | 24.6 min |

Where the buses work, a third of households have no car. Where they do not,
seven in eight households have one. The weight is confounded with the thing it
would be weighting — blending on it would quietly cancel most of the correction
it was introduced to make. Household car access measures *exposure*; it says
nothing about the quality of the alternative, and the two are strongly and
inversely related.

**6. The feed is buses, and both gaps push the same way.** Nationally BODS
carries 3 rail routes and 57 tram, and the tram is Edinburgh, Blackpool, London
Tramlink, Tyne and Wear Metro and the DLR. **National Rail is absent, and so is
Nottingham's NET tram.** Meanwhile walk access here is straight-line × 1.3, which
*overstates* walkability. So Nottingham's 100% is a floor — achieved on buses
alone, before its tram is counted — and the rural zeroes are the pessimistic end:
Boston and Sleaford both have stations, and that journey is not in this data.

**7. The shape scales, and the cost is minutes.** The national evening network
is 7,432,160 connections over 312,623 stops. Two scans per **group**, not per
origin, so cost grows with the ~354 groups rather than the 35,672 small areas:
**2.7 s per group** for both scans, so a full national surface is roughly **16
minutes** of compute. This was the blocker `car_access.py` rated largest, and it
is the smallest one.

**8. The regional-feed worry was wrong, and it was worth checking.** BODS also
publishes per-region files, which are ~10× smaller and much faster to iterate on,
but they truncate journeys crossing a region boundary — South Holland's nearest
group is Peterborough, in a different region. Run on both the national file and
the East Midlands file, the results are **identical on all 411 LSOAs** — same
feasibility, same reason code, journey times agreeing to within 0.7 seconds.
South Holland's 0% is real, not an artefact of clipping. The regional files are safe
for iteration on these geographies; that is a measured statement, not an
assumption.

**9. Loosen the rules and the binding constraint becomes the last bus home.**
Reason codes across the four non-Nottingham LAs (272 LSOAs), by setting:

| | can't get there | no service | **can't get home** | served |
|---|---|---|---|---|
| strict | 225 | 5 | 2 | 0 |
| baseline | 149 | 5 | 57 | 21 |
| generous | 63 | 5 | 107 | 57 |
| very generous | 12 | 3 | **138** | 79 |

Assume a man will travel further and leave earlier and "cannot get there"
collapses from 225 to 12 — but "cannot get home" rises from 2 to 138 and becomes
the dominant reason. Boston under the loosest rule is 37 areas that can reach the
group and 0 that can get back. **The return leg is the irreducible constraint
outside the city**, which is the whole case for modelling a round trip rather
than a travel time. A one-way surface would have called these areas served.

## Decision

**Public transport does not enter `supply_index`, and `travel_minutes` stays
car-only.** Findings 4 and 6 are disqualifying on their own: an input whose
rural values are known to be too pessimistic, and whose middle band moves 0%→94%
on a judgement call, is not fit to move a shortlist that names real
neighbourhoods.

When it does land:

1. **It enters as a feasibility surface, not a minutes column.** `pt_round_trip_ok`
   plus `pt_in_min` where feasible — never a single blended travel time. The
   two-mode reality is bimodal and a percentile rank would erase that.
2. **`no_car_share` is not the blend weight** (finding 5). Exposure and
   alternative-quality are different quantities and must be carried separately.
3. **The acceptable-journey parameters become a fourth sensitivity axis**,
   alongside the weighting scheme, the CI envelope and the supply constants.
   Given finding 4 they would likely be the *least* stable axis, and that has to
   be visible on the map face rather than buried.
4. **Rail is a prerequisite, not an enhancement.** No rural public-transport
   claim gets published off a bus-only feed.
5. **The `TravelTimeProvider` interface change is deferred, and specified:** a
   round trip is two time-anchored searches, so the signature it needs is
   `matrix_minutes(origins, destinations, arrive_by=None, return_after=None)`.
   Haversine and OSRM would ignore both arguments, which is correct for car and
   must be said in `src/caveats.py` rather than left implicit.

Until then `car_access.py` keeps doing its job unchanged: it says where the drive
time is least worth trusting, without pretending to model the alternative.

## Consequences

**The map face and the PDF do not change.** They already say public transport is
not modelled, and that remains exactly true. Five local authorities chosen to
span a range are not a basis for a claim on a surface that names real
neighbourhoods, so nothing from this spike is published there. What changes is
that the statement is now backed by measurement rather than assumption, and the
measurement is written down here and in `data/raw/README.md`. If a national run
is ever done, the copy in `src/caveats.py` is where it would land — as a
quantified claim ("about four times the drive where it works at all") rather
than the present bare disclaimer.

**The shortlist is not invalidated, and may be reinforced.** The rural districts
where buses fail (Boston 75, Newark 38, South Holland 174) are already ranked as
under-served by the car surface. Public transport does not appear to be quietly
rescuing areas the tool marks as needy. But this was tested on five LAs, not
nationally, and it is not a claim the tool makes.

**One result cuts the other way and is worth keeping in view.** Nottingham holds
`rank_reach` 6 — near the top of the shortlist — and is the one place tested
where a man without a car can actually get to the group and home. Its need is
real; its *supply* is better than the car-only surface can see, because the car
surface cannot see that 37% of its households have no car and a bus network that
serves them.

## Alternatives rejected

- **Blend car and bus minutes weighted by `no_car_share`.** The plan of record,
  rejected on finding 5: the weight is confounded with bus availability.
- **Ship one-way travel time and skip the round trip.** The return leg is what
  binds; a one-way number scores an area as served when nobody can get home.
  Cheap to compute and actively misleading.
- **Use OpenTripPlanner or R5 rather than writing a Connection Scan.** Right
  answer for production, wrong for a spike — a JVM, a graph build and a
  multi-gigabyte memory footprint to answer a yes/no question that a single
  sorted scan answers in seconds.
- **Score feasibility as a binary penalty now, caveated.** Tempting, and the
  Boston/Nottingham extremes would survive it. But it would set the middle band
  (finding 4) by fiat, and the middle band is where most of England lives.

## Still open

- **A rail feed.** The prerequisite for any rural claim. BODS does not carry it;
  a separate source is needed and has not been scoped.
- **Routed walk access.** Straight-line × 1.3 ignores rivers, railways and dual
  carriageways. The OSM extract for OSRM is already on disk and could serve a
  foot profile.
- **What journey a man will actually make on a Monday evening after work.** The
  parameter the answer is most sensitive to, and the one no dataset contains.
  This is a question for the charity, not for the pipeline.
- ~~**National extent.** Five LAs, chosen to span the range, not a sample.~~
  Answered by the addendum below — 27 LAs, 3,346 LSOAs — and the five-LA reading
  did not survive it.

## Addendum, 2026-08-31 — the national sample, and what it overturned

The "Still open" bullet below asked for national extent. It was run: **27 local
authorities, 3,346 LSOAs**, one per region × drive-time-tercile cell, seeded and
stratified on the variable under test, spanning 5.5 min (Exeter) to 49.0 min
(Powys) median drive across all ten regions. Feed coverage was checked first and
is even — 97.8% of English and 96.1% of Welsh LSOA centroids have a stop within
800 m — so Wales is measured, not assumed. Powys has no group within 45 km at
all, so its 79 LSOAs are excluded from the bus comparison: they are unreachable
for a reason that has nothing to do with buses.

**The five-LA result did not generalise, and the error was mine.** All five sat
in the East Midlands, four of them rural, and they shared one pattern. On the
national sample the case that public transport merely restates the car surface
falls over:

| | 5 LAs (East Midlands) | 27 LAs (national) |
|---|---|---|
| corr(car minutes, robustly infeasible) | 0.765 | **0.614** |
| corr(no-car share, feasible) | 0.664 | **0.415** |
| areas where car and bus disagree | 6 of 411 (1.5%) | **447 of 3,267 (13.7%)** |
| of those, inside the top-100 shortlist | 0 | **2** |

The drive-time gradient is far flatter than the regional sample implied — the
closest quartile is 3.3% infeasible and the furthest only 54.9%, against 0% and
100% before. **Public transport carries real information the car surface does
not.**

**And the information is systematic, not noise.** Of the 340 areas the car
surface calls poorly-served where the bus always works, 263 are Tower Hamlets
(166) and Greenwich (97), with Wirral, Cardiff and Oxford behind them. These are
dense urban areas where driving is slow *because* the place is dense, and where
the transit that density pays for is invisible to a car-only surface. The
correction runs the opposite way to the one this ADR anticipated: it does not
promote rural areas the tool is missing, it **demotes urban areas whose unmet
need the car surface overstates**. Of the ten sampled areas inside the top-100
reach shortlist, five are comfortably reachable by bus (Oxford at ranks 4, 12 and
98, Greenwich 21, Tower Hamlets 55) and four are not (Wiltshire 68 and 78, Boston
75, Chorley 9).

**What did not change is the objection that actually mattered.** Across the four
acceptable-journey rules:

| verdict | share of the 3,267 |
|---|---|
| served under **every** rule | 48.1% |
| infeasible under **every** rule | 20.9% |
| **depends on the assumption** | **30.9%** |

Nearly a third of areas have no data-determined answer — worse than the 26.8% the
regional sample showed, and it lands hardest where it matters most. Oxford holds
rank 4 and is **88% contested**: its favourable verdict is almost entirely an
artefact of assuming a man will travel far enough. Scoring that would move the
top of the shortlist on a number nobody measured.

**Where this leaves the decision.** The Decision above stands on every point
except the reasoning in "Consequences" that the shortlist "may be reinforced" —
nationally it would be *changed*, in a direction worth taking seriously. But the
finding also suggests a shape this ADR had not seen. The first sketch was a
three-valued flag (served / not served / uncertain); better than a score, but it
still asserts too much. The sharper form rests on an **asymmetry**:

* Finding a journey **proves** feasibility. Adding rail, tram, or a wider walk
  radius could only ever *add* served areas, never remove one. It is a floor.
* Failing to find one proves only that none exists **in a bus-only dataset**.
  Printing "no public transport to this group" beside a neighbourhood with a
  railway station would be a factual error on a tool that names real places.

So the defensible per-area claim is the positive one alone — *a man without a car
can reach this group and get home, under every assumption tested* — true of 48.1%
of sampled areas, parameter-invariant and rail-proof by construction, and sitting
exactly where the decision-relevant disagreement is. The rural half stays a
general statement, where it does not have to be right per area. Rail is still the
prerequisite for ever asserting the negative: Wiltshire and Richmondshire, two of
the most infeasible LAs in the sample, are where a station would change it.

## What was shipped, 2026-08-31

**Not the per-area column.** It needs a full national run, and it would be the
first input in the tool with a shelf life — roads do not change, timetables change
several times a year — so it carries a re-run cadence nothing else here does. It
remains available and unbuilt.

**The disclosure, which is not optional.** The ranking leans in a measured
direction whether or not that is stated, and it was being read off a map that said
only "public transport is not modelled":

- `vintages.public_transport` in `config.yaml`, so the claim carries a vintage
  like every other input, even though nothing ingests it.
- `public_transport_note()` in `src/caveats.py` as the single source of the copy,
  with the three provider travel notes reduced to pointing at it rather than
  restating it.
- A named **Public transport** caveat on the map face and in the PDF, stating the
  direction: this ranking probably *overstates* unmet need in dense city
  neighbourhoods.
- A row in the plain-English guide (`app/views/guide.py`).
- `tests/test_caveats_public_transport.py` pins the direction, the never-scored
  claim, the vintage, and that the rural half stays general — so the disclosure
  cannot quietly disappear in a copy edit.

Reproduce with:

```bash
python spikes/pt_evening_access.py --feed all --sweep \
  --las "Boston|North Northamptonshire|Bolsover|Stevenage|South Cambridgeshire|Chelmsford|Greenwich|Tower Hamlets|Hammersmith and Fulham|County Durham|Northumberland|Wirral|Chorley|Oxford|Runnymede|Eastleigh|Wiltshire|East Devon|Exeter|Powys|Cardiff|Merthyr Tydfil|Rugby|Newcastle-under-Lyme|Coventry|Richmondshire|York"
```
