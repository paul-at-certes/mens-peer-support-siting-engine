# `spikes/` — throwaway research code

Nothing in here is imported by `src/` or `app/`, and nothing here runs as part
of `python -m src.pipeline`. These scripts exist so that a claim made in an ADR
can be re-derived rather than taken on trust.

A spike graduates by being **deleted** and replaced with a real module under
`src/`, or by being recorded as a dead end in the ADR that commissioned it.

| Spike | Commissioned by | Question | Verdict |
|---|---|---|---|
| `pt_evening_access.py` | `docs/adr/0002-public-transport-feasibility-spike.md` | Can a man get to a Monday-evening group by bus and back? | Data exists; not yet fit to score. See ADR 0002. |
| `group_need_concordance.py` | `docs/adr/0003-concordance-with-existing-groups.md` | Does the need surface agree with where AMC already opened 354 groups? | Yes within a town (64.1st percentile vs a null of 50); **no evidence between towns** once region is held constant. See ADR 0003. |

## `pt_evening_access.py`

```bash
# the headline run — national feed, urban vs rural contrast
python spikes/pt_evening_access.py --las "Nottingham|Boston|Newark and Sherwood|Mansfield"

# does the answer survive changing what counts as an acceptable journey?
python spikes/pt_evening_access.py --las "Nottingham|Boston" --sweep

# faster iteration on one BODS region (WARNING: truncates cross-boundary journeys)
python spikes/pt_evening_access.py --feed east_midlands --las Nottingham
```

The first run downloads the ~1.4GB national GTFS from the Bus Open Data Service
and caches it under `data/raw/real/gtfs/` (git-ignored). No API key is needed.

It answers a **round trip**, not a travel time: can you arrive by 19:00 having
left home no earlier than 17:30, *and* get home after the 21:00 finish? The last
bus home is usually what actually binds, and a one-way number scores an area as
well-served when nobody can get back.

Method is a Connection Scan over the timetable, two scans **per group** —
backward from the group arriving 19:00, forward from it leaving 21:00 — with
origins joining on by a walk to nearby stops. Cost grows with the ~354 groups,
not the 35,672 small areas, which is the only shape that scales.

Read the limitations in the module docstring before quoting any number from it.
The important ones: BODS is a **bus** feed and GB rail is effectively absent, so
rural feasibility is understated; and walk access is straight-line, so
walkability is overstated.

## `group_need_concordance.py`

```bash
python spikes/group_need_concordance.py                 # the headline run
python spikes/group_need_concordance.py --draws 20000   # tighter permutation p
python spikes/group_need_concordance.py --refresh-onspd # re-fetch the postcode lookup
```

Answers the first question anyone asks of this tool: **does it agree with what
we already know?** AMC has opened 354 groups over a decade of local judgement.
The need surface has never seen any of them. Do they land where it says need is?

It is **not** the back-test in `docs/design.md` §7 check 3 — that needs opening
dates and attendance figures, which no open source carries and only AMC could
supply. This measures *concordance*, which is weaker and available today.

Everything it reports uses `need_index` and its components, never
`priority_score`. The supply surface is built **from** these 354 groups, so
scoring them against the priority surface would be marking the model's homework
with its own answers.

Four measurements, in increasing resistance to confounding:

| | asks | confound it removes |
|---|---|---|
| **A** between LA | did they pick the higher-need towns? | run twice — the second permuting only within region, so founder geography is held constant |
| **B** within LA | did they pick the higher-need neighbourhoods? | never compares one LA with another, so founder geography cannot touch it |
| **C** venue vs catchment | is the venue's own area representative of who it serves? | a venue sits in a town centre with a hall to hire, not in anybody's home |
| **D** by component | which part of the surface do their choices corroborate? | says whether concordance is deprivation alone |

Groups are assigned to small areas **by postcode** through the ONS Postcode
Directory (cached under `data/raw/real/`, git-ignored; the first run takes a few
minutes). Nearest population-weighted centroid is computed too, purely as a
cross-check, and the disagreement rate is printed — in a dense town the nearest
centroid is routinely the next LSOA over, which is not good enough for
measurement B.

The statistics are unit-tested (`tests/test_concordance_spike.py`), including a
calibration test that venues placed at random within their LA do **not** come
out significant.
