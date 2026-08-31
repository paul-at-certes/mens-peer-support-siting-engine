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


def _entry(label: str, body: str) -> dict:
    return {"label": label, "body": " ".join(body.split())}


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
                "engine we host ourselves. Car only. Public transport is not "
                "modelled, which matters here: sessions run in the evening, and men "
                "without cars in deprived areas face a journey this does not show."
                + _NO_CAR_TAIL)
    if provider == "ors":
        return ("OpenRouteService driving times. Car only. Public transport is not "
                "yet modelled." + _NO_CAR_TAIL)
    return (f"the {provider} provider, which uses straight-line distance at a "
            f"constant assumed speed. Measured against real road routing on this "
            f"data it errs in both directions. It understates typical journeys, "
            f"putting the nearest group 10.2 minutes away against 13.8 by road, and "
            f"overstates the worst ones, because a flat speed ignores motorways: 41.4 "
            f"minutes at the 90th percentile against 35.1. Car only, and public "
            f"transport is not modelled." + _NO_CAR_TAIL)


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
            {v['census']}. Based on where high-risk workers live rather than where they
            work, and at the broadest occupational grouping, which is the only
            occupation-by-sex breakdown published at this level. A broad measure."""),
        _entry("Isolation", """
            Male single/separated/divorced (sex-specific) plus the one-person-household
            share, which is a household measure: Census 2021 publishes no sex-broken
            living-alone figure at this grain."""),
        _entry("Population", f"{v['population']}. Provision: {v['provision']}."),
        _entry("Travel time", travel_note(cfg)),
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
        contradicts, not to supply one.""")]

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
