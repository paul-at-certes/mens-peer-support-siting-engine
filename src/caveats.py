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


def _entry(label: str, body: str) -> dict:
    return {"label": label, "body": " ".join(body.split())}


def travel_note(cfg: Config) -> str:
    """Describe the routing actually used — never a hard-coded assumption."""
    provider = cfg["accessibility"].get("provider", "haversine")
    if provider == "osrm":
        return ("real road driving times on the GB road network via a self-hosted OSRM "
                "routing engine. Car only — public-transport access is not modelled, "
                "which matters for this population: evening sessions and men without "
                "cars in deprived areas face a journey this does not represent.")
    if provider == "ors":
        return ("OpenRouteService driving times (car only; public-transport access "
                "is not yet modelled).")
    return (f"the {provider} provider — straight-line distance at a constant assumed "
            f"speed. Measured against real road routing on this data it is wrong in "
            f"BOTH directions: it under-states typical journeys (median nearest group "
            f"10.2 min against 13.8 by road) while over-stating the worst ones, because "
            f"a flat speed ignores motorways (90th percentile 41.4 min against 35.1). "
            f"Car only; public transport is not modelled.")


def data_caveats(cfg: Config) -> list[dict]:
    v = cfg["vintages"]
    return [
        _entry("Suicide signal", f"""
            {v['suicide']}. Local-Authority grain only, with a ~200-270 day registration
            lag, so it CHECKS the weighting and contributes one low-weighted term — it
            never ranks areas on its own. No small-area suicide rate is fabricated.
            Covers England AND Wales. Counts are male ALL AGES, not working age: the
            publisher zeroes any cell below 5, which at working-age-band granularity
            loses about half the deaths and loses them disproportionately in small local
            authorities. All-ages recovers 96.6% of the published national total; the
            remaining ~3.4% is lost to that same rule. The proxies are working-age
            measures, so the outcome is broader than the population targeted."""),
        _entry("Deprivation", f"""
            {v['deprivation']}. Within-nation percentiles only (England publishes scores,
            Wales publishes ranks — not comparable across the border). Collinear with both
            other proxies (0.72 with isolation, 0.63 with occupation at LA level), which
            is why the LA-level fit cannot be used to set the weights."""),
        _entry("Occupation", f"""
            {v['census']}. Residence-based — where high-risk workers live, not where they
            work — and at SOC major-group resolution, the only occupation-by-sex cut
            available at this grain. A broad proxy."""),
        _entry("Isolation", """
            Male single/separated/divorced (sex-specific) plus the one-person-household
            share, which is a household measure: Census 2021 publishes no sex-broken
            living-alone figure at this grain."""),
        _entry("Population", f"{v['population']}. Provision: {v['provision']}."),
        _entry("Travel time", travel_note(cfg)),
        _entry("Latent need, not prediction", """
            Area-level only, and never to be read as a statement about any individual.
            This is a shortlist for local judgement — venue, volunteers and partner
            appetite decide siting, not this ranking."""),
    ]


def assurance_notes(cfg: Config) -> list[dict]:
    """How the weights were set, and whether the checks on them passed.

    Reads the calibration diagnostic and the sensitivity report if they exist;
    both are optional, and their absence is itself reported.
    """
    notes = [_entry("How the weights were set", """
        The component weights are a DECLARED PRIOR stated in config.yaml, not a
        regression output. With ~292 Local Authorities and three mutually collinear
        proxies, the LA-level model does not identify them. The model's job is to veto
        a weight the data contradicts, not to supply it.""")]

    weights_path, sens_path = cfg.path("weights"), cfg.path("sensitivity")

    if not weights_path.exists():
        notes.append(_entry("Calibration check", """
            NOT RUN — no LA-level outcome data was available for this build, so the
            declared weights are unchecked. They still produced this ranking."""))
    else:
        veto = json.loads(weights_path.read_text()).get("veto", {})
        status, findings = veto.get("status", "unknown"), veto.get("findings", [])
        headline = {
            "pass": "PASSED — the LA-level fit contradicts none of the declared weights.",
            "collinearity": ("PASSED, with collinearity notes — no declared weight is "
                             "contradicted, but the proxies overlap."),
            "unsupported": ("FLAGGED — a weighted proxy is not individually evidenced at "
                            "LA level."),
            "contradicted": ("FLAGGED — the LA-level fit points the opposite way to a "
                             "declared weight."),
        }.get(status, "status unknown.")
        body = "Veto check " + headline
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
            UNSTABLE with respect to {detail}. Areas we would act on drop out of
            contention under an alternative configuration, so read this as a starting
            point for local judgement rather than as a ranking."""))
    else:
        worst = max((c for c in checks.values() if c.get("worst_rank")),
                    key=lambda c: c["worst_rank"], default=None)
        tail = (f" Across every alternative tested the furthest any of them fell was to "
                f"rank {worst['worst_rank']}." if worst else "")
        notes.append(_entry("Stability check", f"""
            STABLE. Of the top {D} areas — the ones you would actually act on — 100% stay
            inside the top {band} under every alternative weighting, every draw from the
            range the LA fit supports, and every travel-time and catchment setting
            tested.{tail} The ORDER within the leading group is much less certain than
            its membership, which is why the output is banded into tiers rather than
            read as a strict ranking."""))

    tiers = sens.get("tiers") or {}
    if tiers:
        c = tiers.get("counts", {})
        notes.append(_entry("How to read the tiers", f"""
            {c.get('shortlist', 0)} areas are in the SHORTLIST tier — inside the top
            {sens.get('shortlist_n')} under every one of the
            {tiers.get('n_configurations')} configurations tested. A further
            {c.get('contention', 0)} are IN CONTENTION, reaching that under some
            configurations but not all. Within a tier, treat the areas as jointly
            prioritised: the evidence does not separate them, and local judgement —
            venue, volunteers, partner appetite — should decide between them."""))
    return notes
