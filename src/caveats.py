"""Single source of the honesty copy shown on the map face and in the PDF.

Both surfaces must say the same thing. They previously carried their own copies
and drifted — the app asserted "straight-line distance over-states accessibility"
regardless of which routing provider had actually produced the numbers.

Text here is markup-free: each renderer bolds the label in its own dialect
(Markdown in Streamlit, reportlab's mini-HTML in the PDF).
"""

from __future__ import annotations

import json

from .config import Config


# Share of households with no car or van at which the drive time stops describing
# most of the journeys people would actually make to an evening session. About
# 23% of households across England and Wales have no car (Census 2021 TS045) and
# the median neighbourhood sits near 19%, so this bar is set clear of typical
# rather than a whisker above it. It is a display threshold: nothing in the
# ranking depends on it.
HIGH_NO_CAR_SHARE = 0.40

# Appended to every provider's travel note, so the map face and the PDF say the
# same thing about the car-only assumption whichever routing is in use.
_NO_CAR_TAIL = """
    How much that matters varies sharply from one place to the next. About one
    household in four across England and Wales has no car or van, but between
    neighbourhoods the share runs from under 5% to nearly 90%, so each area's own
    figure is shown with its breakdown. Read the drive time as least representative
    of real journeys where that share is highest."""


def _diagnostic_path(cfg: Config, key: str):
    """Resolve an OPTIONAL diagnostic path, or None if this config has no such key.

    Every diagnostic these notes read is optional and its absence is itself
    reported, so a config written before one of them existed must degrade the
    copy rather than take down the map that renders it.
    """
    try:
        return cfg.path(key)
    except KeyError:
        return None


def _entry(label: str, body: str) -> dict:
    return {"label": label, "body": _words(body)}


def _ordinal(n: int) -> str:
    """1 -> '1st', 11 -> '11th', 5203 -> '5,203rd'. Ranks read as ranks."""
    suffix = "th" if 11 <= (n % 100) <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:,}{suffix}"


def _words(body: str) -> str:
    """Collapse an indented triple-quoted block into one clean paragraph."""
    return " ".join(body.split())


def car_access_note(share: float | None) -> str:
    """One plain sentence about an area's car access, for the per-area breakdown.

    Descriptive only. The share never enters a score (see ingest/car_access.py);
    it says where the car-only travel time above is least worth trusting.
    """
    if share is None or share != share:      # None or NaN
        return ("No car or van figure for this area, so there is no way to tell how "
                "well the drive time above describes journeys made here.")
    line = f"Households with no car or van: {share:.0%}."
    if share >= HIGH_NO_CAR_SHARE:
        line += (" That is high. The drive time above overstates how reachable a group "
                 "is for those households: getting to an evening session without a car "
                 "is a different journey, and this tool does not measure it.")
    return line


def travel_note(cfg: Config) -> str:
    """Describe the routing actually used — never a hard-coded assumption."""
    provider = cfg["accessibility"].get("provider", "haversine")
    if provider == "osrm":
        return ("real road driving times on the GB road network, from a routing "
                "engine we host ourselves. Car only, which matters here: sessions run "
                "in the evening, and men without cars face a journey this does not "
                "show. See the public transport note." + _NO_CAR_TAIL)
    if provider == "ors":
        return ("OpenRouteService driving times. Car only. See the public "
                "transport note." + _NO_CAR_TAIL)
    return (f"the {provider} provider, which uses straight-line distance at a "
            f"constant assumed speed. Measured against real road routing on this "
            f"data it errs in both directions. It understates typical journeys, "
            f"putting the nearest group 10.2 minutes away against 13.8 by road, and "
            f"overstates the worst ones, because a flat speed ignores motorways: 41.4 "
            f"minutes at the 90th percentile against 35.1. Car only. See the "
            f"public transport note." + _NO_CAR_TAIL)


def public_transport_note(cfg: Config) -> str:
    """What the evening-bus measurement found, and what it must not be read as.

    Measured on a sample, never scored (docs/adr/0002-*). Two deliberate choices
    in this copy. The DIRECTION of the bias is stated, because the ranking leans
    that way whether or not we admit it. And the rural half is written as a
    general statement rather than a per-area claim: the timetables cover buses
    and not trains, so "no way there" is absence of evidence, while "there is a
    way" is evidence. We assert only the half we can prove.
    """
    return " ".join(f"""
        {cfg['vintages']['public_transport']}. Not modelled for any individual area,
        and nothing here changes a score or moves an area up or down. Three things
        that sample showed, which change how the ranking should be read. Where
        evening buses run at all, the journey takes about four times the drive. In
        dense city neighbourhoods the buses generally do work, so this ranking
        probably OVERSTATES unmet need there: driving is slow because the place is
        dense, and the frequent transit that density pays for is invisible to a
        car-only measure. In rural areas an evening round trip is frequently
        impossible, but that is not claimed of any particular area, because the
        timetables cover buses and not trains, so somewhere with a station may be
        better served than this can see.
    """.split())


def outvoted_note(cfg: Config) -> str:
    """What the ranking structurally cannot show, and what we do about it.

    Deprivation (0.40) and isolation (0.25) together outweigh occupation (0.35)
    close to two to one, and areas that score high on occupation alone tend to
    score low on both. So an area can carry the highest occupational risk in the
    country and still rank nowhere. That is a consequence of the declared
    weights, not a fault in the data, but a reader cannot infer it from the map,
    so it is stated here.

    This note USED TO END "this list will not surface them", which was true when
    it was written and stopped being true the moment the blind-spot flag shipped.
    It is rewritten rather than appended to, and it is now conditional on the
    flag having actually run: with no blind_spot.json the old ending is the
    honest one again. Figures come from occupation_diagnostic.json and
    blind_spot.json when they exist; the opening claim depends on neither.
    """
    base = _words("""
        The ranking is driven mainly by poverty and by men living alone. Those two
        together outweigh the jobs factor by roughly two to one, and places that
        score high on jobs alone tend to score low on both of the others. So an area
        where men do the most dangerous work, but which is not poor, will not appear
        near the top of this list however risky that work is.""")

    diag = _diagnostic_path(cfg, "occupation_diagnostic")
    ov = json.loads(diag.read_text()).get("outvoted") if diag and diag.exists() else None
    if ov:
        las = ", ".join(ov["example_las"])
        base += " " + _words(f"""
            The clearest cases are farming and building trades in places such as
            {las}: among the highest in the country for occupational risk and among
            the lowest for poverty. They sit around {_ordinal(ov['median_rank'])} of
            {ov['n_areas']:,} here, and the best-placed of them is
            {_ordinal(ov['best_rank'])}. That follows from the weights we chose, not
            from anything in the data.""")

    flag = _diagnostic_path(cfg, "blind_spot")
    if flag is None or not flag.exists():
        return base + " " + _words("""
            If those places matter to you, they need looking for separately. This
            list will not surface them.""")

    bs = json.loads(flag.read_text())
    n, total = bs["n_flagged"], bs["n_areas"]
    if not n:
        return base + " " + _words("""
            Every area is checked for this, and on this run no area met the test.""")
    rank = bs.get("ranking", {})
    return base + " " + _words(f"""
        Those places are no longer left invisible. Every area is now tested for the
        pattern, and {n:,} of {total:,} are marked: the work their men do carries at
        least the average suicide risk for men in work across England and Wales,
        while this index still puts them in its bottom half. Each one carries the
        mark in its own breakdown, and the map can show them on their own. Read the
        mark as a statement about this ranking rather than about the place. It says
        the ranking cannot see the risk there — the best-placed of them sits
        {_ordinal(rank.get('best_rank', 0))} of {total:,} and none reaches the
        shortlist. It does not say a group should open there: being hard to reach is
        a separate question, and some of the marked areas already have a group
        nearby.""")


def blind_spot_definition(cfg: Config) -> str:
    """What the mark means, stated in general. The per-area wording is below.

    Two forms of the same copy because a section heading and a single area's
    breakdown are different sentences — "areas where" versus "here" — and writing
    one and bending it produced "Marked as an occupational blind spot" under a
    heading that already said so.
    """
    return _words("""
        Some areas carry a mix of jobs that is more dangerous than average without
        being poor. Where the work men do carries at least the average suicide risk
        for men in work across England and Wales, and this ranking still scores the
        area below average need, the area is marked. The mark says the ranking is
        blind there, because poverty and men living alone outweigh the jobs factor
        about two to one. It does not by itself say a group should open there.""")


def blind_spot_note(cfg: Config, flagged: bool | None) -> str:
    """One plain sentence for a single area's breakdown. Descriptive only."""
    if flagged is None:
        return ""
    if not flagged:
        return _words("""
            Not marked as an occupational blind spot: either the work done here does
            not carry above-average risk, or the ranking is already picking that risk
            up through the other factors.""")
    return _words("""
        Marked as an occupational blind spot. The mix of jobs men do here carries at
        least the average suicide risk for men in work across England and Wales, and
        this ranking still scores the area below average need, because poverty and
        men living alone outweigh the jobs factor about two to one. The mark says the
        ranking is blind here. It does not by itself say a group should open here.""")


def remoteness_note(cfg: Config, median_male_pop: float | None = None) -> str:
    """What the remoteness view is, what it is not, and whether a group fits.

    Three things a reader has to be told, or the view misleads. It re-ranks; it
    does not re-score. The cut is remoteness, not rurality, and the reason is
    measured rather than assumed. And a weekly group in a room needs enough men
    in that room, which is the part a ranking cannot answer.
    """
    v = cfg["vintages"].get("remoteness", "ONS Rural-Urban Classification 2021")
    pop = (f" These areas hold a fairly ordinary number of working-age men — a median "
           f"of about {int(round(median_male_pop)):,} — but spread over far more "
           f"ground than an urban neighbourhood of the same count."
           if median_male_pop and median_male_pop == median_male_pop else "")
    return _words(f"""
        {v}. This view re-ranks a subset of the map. It does not re-score anything:
        every figure here is the same figure the main list uses, and no area moves up
        or down because it is remote. The cut is on remoteness — whether an area is
        further from a major town or city — and not on whether it is rural, because
        measured on this data the signal sits entirely in the "further" half. Rural
        areas near a town score low on occupational risk, and remote urban
        neighbourhoods carry the highest poverty of any class, so a rural-only cut
        would have kept the wrong 2,127 areas and dropped the right 2,451. It uses
        the per-capita ranking, not reach: reach multiplies by population, so remote
        areas can never win on it, and for a weekly group meeting in a room that is
        arguably the right answer rather than a fault.{pop} A conventional weekly
        group may simply not be viable in one of them, and the honest answer may be a
        travelling group, or one group in the market town that several of these areas
        can reach between them. That is a judgement for people who know the place.
    """)


def data_caveats(cfg: Config) -> list[dict]:
    v = cfg["vintages"]
    return [
        _entry("Suicide signal", f"""
            {v['suicide']}. Published for local authorities only, and registered
            roughly 200 to 270 days after the death. It checks the weighting and
            contributes one lightly weighted term. It never ranks areas on its own,
            and no small-area suicide rate is invented. England and Wales are both
            covered. Counts are for men of all ages rather than working age, because
            the publisher zeroes any figure below 5: at working-age granularity that
            loses about half the deaths, and loses them mostly in small local
            authorities. All ages recovers 96.6% of the published national total, with
            the remaining 3.4% lost to the same rule. The other factors are working-age
            measures, so this outcome is broader than the population targeted."""),
        _entry("Deprivation", f"""
            {v['deprivation']}. Ranked within each nation only. England publishes
            scores and Wales publishes ranks, and the two are not comparable across
            the border. It also overlaps heavily with both other factors, correlating
            0.72 with isolation and 0.63 with occupation at council level, which is
            why the council-level fit cannot be used to set the weights."""),
        _entry("Occupation", f"""
            {v['occupation']}. Each occupational group is weighted by the suicide rate
            actually recorded for men in it, so elementary trades count for roughly
            three times the average and corporate managers for less than a third,
            rather than every manual job counting the same. Four things to hold in
            mind. The rates are English, from deaths registered between 2011 and 2015,
            and are applied to Wales because nothing Welsh exists. They are the last
            of their kind: the 2016 to 2020 update was cancelled. Eight of the
            twenty-six groups showed no difference from the average, or too few deaths
            to tell, and are counted as average rather than guessed at. And the
            detailed mix of trades is published only for areas about five times larger
            than the ones ranked here, so neighbourhoods within one of those larger
            areas share an answer for which trades their men do, differing only in how
            many. It also counts where those men live, not where they work."""),
        _entry("Isolation", """
            Male single/separated/divorced (sex-specific) plus the one-person-household
            share, which is a household measure: Census 2021 publishes no sex-broken
            living-alone figure at this grain."""),
        _entry("Population", f"{v['population']}. Provision: {v['provision']}."),
        _entry("Remoteness", remoteness_note(cfg)),
        _entry("Travel time", travel_note(cfg)),
        _entry("Public transport", public_transport_note(cfg)),
        _entry("What this list will not show you", outvoted_note(cfg)),
        _entry("Likely need, not prediction", """
            Area-level only, and never to be read as a statement about any individual.
            This is a shortlist for local judgement. Venue, volunteers and partner
            appetite decide where a group opens, not this ranking."""),
    ]


def assurance_notes(cfg: Config) -> list[dict]:
    """How the weights were set, and whether the checks on them passed.

    Reads the calibration diagnostic and the sensitivity report if they exist;
    both are optional, and their absence is itself reported.
    """
    notes = [_entry("How the weights were set", """
        The weights are stated in config.yaml rather than produced by a model. With
        roughly 300 local authorities and three factors that overlap heavily, the
        council-level model cannot separate them. Its job is to veto a weight the data
        contradicts, not to supply one."""),
        _entry("One check is not independent", """
        The occupation factor is built from recorded suicide rates by occupation, so
        the council-level check finds it strongly associated with recorded suicides
        partly by construction. That association is not independent evidence that
        occupation belongs in the ranking. What it does still test is whether the
        national occupational pattern from 2011 to 2015 shows up in recent
        differences between council areas, which is a real question, and it does.""")]

    weights_path, sens_path = cfg.path("weights"), cfg.path("sensitivity")

    if not weights_path.exists():
        notes.append(_entry("Calibration check", """
            Not run. No council-level outcome data was available for this build, so
            the stated weights are unchecked. They still produced this ranking."""))
    else:
        veto = json.loads(weights_path.read_text()).get("veto", {})
        status, findings = veto.get("status", "unknown"), veto.get("findings", [])
        headline = {
            "pass": "found nothing to contradict any of the stated weights.",
            "collinearity": ("found nothing to contradict the stated weights, but noted "
                             "that the factors overlap one another."),
            "unsupported": ("flagged a factor that carries weight without being "
                            "evidenced on its own at council level."),
            "contradicted": ("flagged a stated weight that the fit points the opposite "
                             "way to."),
        }.get(status, "of unknown status.")
        body = "The council-level check " + headline
        if findings:
            body += " " + " ".join(f["message"] for f in findings)
        notes.append(_entry("Calibration check", body))

    if not sens_path.exists():
        notes.append(_entry("Stability check", "NOT RUN for this build."))
        return notes

    sens = json.loads(sens_path.read_text())
    st = sens.get("stability", {})
    checks, unstable = st.get("checks", {}), st.get("unstable_axes", [])
    D, band = sens.get("decision_n"), sens.get("contention_band")
    readable = {"schemes": "the choice of weighting scheme",
                "envelope": "the weights moving within what the data supports",
                "supply": "the travel-time and catchment constants"}
    if unstable:
        detail = "; ".join(
            f"{readable.get(k, k)} (only {checks[k]['worst_held']:.0%} held)" for k in unstable)
        notes.append(_entry("Stability check", f"""
            Unstable with respect to {detail}. Areas we would act on drop out of
            contention under an alternative configuration, so read this as a starting
            point for local judgement rather than as a ranking."""))
    else:
        worst = max((c for c in checks.values() if c.get("worst_rank")),
                    key=lambda c: c["worst_rank"], default=None)
        tail = (f" Across every alternative tested, the furthest any of them fell was "
                f"to rank {worst['worst_rank']}." if worst else "")
        notes.append(_entry("Stability check", f"""
            Stable. Of the top {D} areas, the ones you would actually act on, all stay
            inside the top {band} under every alternative weighting, every draw from the
            range the council-level fit supports, and every travel-time and catchment
            setting tested.{tail} The order within the leading group is far less certain
            than its membership, which is why the output is banded into tiers rather
            than read as a strict ranking."""))

    tiers = sens.get("tiers") or {}
    if tiers:
        c, rc = tiers.get("counts", {}), tiers.get("reach_counts", {})
        reach_bit = (f" The reach view is tiered separately, at {rc.get('shortlist', 0)} "
                     f"and {rc.get('contention', 0)}, because reach multiplies by "
                     f"population and a tier from one ranking says nothing about the "
                     f"other." if rc else "")
        notes.append(_entry("How to read the tiers", f"""
            {c.get('shortlist', 0)} areas are in the shortlist tier on the per-capita
            ranking, meaning they sit inside the top {sens.get('shortlist_n')} under
            every one of the {tiers.get('n_configurations')} configurations tested. A
            further {c.get('contention', 0)} are in contention, reaching that under some
            configurations but not all.{reach_bit} Within a tier, treat the areas as
            jointly prioritised. The evidence does not separate them, and local
            judgement about venue, volunteers and partner appetite should decide
            between them."""))
    return notes
