"""The public-transport disclosure must survive edits to the caveat copy.

The tool has a MEASURED, directional bias (docs/adr/0002-*): it likely overstates
unmet need in dense city neighbourhoods, because driving is slow there precisely
where frequent transit exists. That leans the ranking whether or not we say so,
so the disclosure is load-bearing and is pinned here rather than left to prose.
"""

from src.caveats import data_caveats, public_transport_note, travel_note
from src.config import load_config


def _cav():
    return {e["label"]: e["body"] for e in data_caveats(load_config())}


def test_public_transport_is_a_named_caveat():
    assert "Public transport" in _cav()


def test_it_states_the_direction_of_the_bias():
    body = _cav()["Public transport"].lower()
    assert "overstates" in body
    assert "dense city" in body


def test_it_never_claims_to_change_a_score():
    assert "changes a score" in _cav()["Public transport"]


def test_the_rural_half_is_not_asserted_per_area():
    """"No way there" is absence of evidence while the feed carries no trains,
    so the copy must keep it general and say why."""
    body = _cav()["Public transport"]
    assert "not claimed of any particular area" in body
    assert "trains" in body


def test_the_measurement_carries_a_vintage():
    cfg = load_config()
    assert "public_transport" in cfg["vintages"]
    assert cfg["vintages"]["public_transport"] in public_transport_note(cfg)


def test_travel_note_does_not_duplicate_the_claim():
    """One source for the copy: the travel note points at it, never restates it."""
    for provider in ("osrm", "ors", "haversine"):
        cfg = load_config()
        cfg._data["accessibility"]["provider"] = provider
        note = travel_note(cfg)
        assert "public transport note" in note.lower()
        # Not a bare "overstates" check: the haversine note legitimately says the
        # stub overstates the worst journeys. Pin the public-transport claim itself.
        assert "dense city" not in note.lower()
