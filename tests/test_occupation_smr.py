"""The SMR-weighted occupation layer: the weight rule and the LSOA/MSOA hybrid.

Pure-function tests — no network, no workbook, so they run on a clean clone.
"""

import json
import math

import pandas as pd
import pytest

from src.ingest.occupation import (
    MAJOR_GROUPS, NEUTRAL_SMR, SUBMAJOR_LABELS, apply_weight_rule, composite_smr,
)


# --- the weight rule -------------------------------------------------------
def test_significant_group_keeps_its_smr():
    # 91 Elementary trades: 782 deaths, SMR 292 (272-313) — wholly above 100.
    assert apply_weight_rule(782, 292, 272, 313) == (292, False)


def test_protective_group_keeps_its_smr():
    # 11 Corporate managers: CI wholly BELOW 100 is just as much a finding.
    assert apply_weight_rule(337, 28, 26, 32) == (28, False)


def test_ci_spanning_100_is_neutralised_not_pulled_to_its_parent():
    """The regression this rule exists to prevent.

    92 Elementary administration is a precise 103 (97-110) on 1,002 deaths.
    Major group 9 is 144, almost entirely because of its sibling 91 (292). A
    parent-group fallback would assign elevated risk to 1.2m men on the strength
    of a different occupation, so the group must go to neutral instead.
    """
    weight, neutralised = apply_weight_rule(1002, 103, 97, 110)
    assert (weight, neutralised) == (NEUTRAL_SMR, True)
    assert weight != 144


def test_inclusive_lower_bound_counts_as_spanning():
    # 81 Process, plant and machine operatives: 108 (100-117) sits on the line.
    assert apply_weight_rule(671, 108, 100, 117) == (NEUTRAL_SMR, True)


def test_too_few_deaths_is_neutralised_even_when_significant():
    # 42 Secretarial: CI excludes 100 but only 25 deaths — too unstable to use.
    assert apply_weight_rule(25, 59, 38, 87) == (NEUTRAL_SMR, True)


# --- SOC 2020 coverage -----------------------------------------------------
def test_every_soc_2020_submajor_group_has_a_label():
    """26 sub-major groups, and the SOC 2020-only group 63 is one of them."""
    assert len(SUBMAJOR_LABELS) == 26
    assert "63" in SUBMAJOR_LABELS
    assert all(g[0] in {str(i) for i in MAJOR_GROUPS} for g in SUBMAJOR_LABELS)


def test_group_63_falls_back_to_neutral_when_absent_from_the_smr_table():
    """SOC 2020 added 63; the SOC 2010 SMR table cannot have it. It must take
    the neutral weight rather than a null, which would poison the sum."""
    weights = pd.Series({"61": 118.0, "62": 100.0})       # no '63'
    w = pd.Series({s: float(weights.get(s, NEUTRAL_SMR)) for s in ["61", "62", "63"]})
    assert w["63"] == NEUTRAL_SMR
    assert not w.isna().any()


# --- the hybrid composition ------------------------------------------------
def _sub(**rows):
    return pd.DataFrame(rows, index=["91", "92"]).T


def test_composite_smr_is_the_within_major_weighted_mean():
    """Two MSOAs with the same number of elementary men but a different mix must
    get different composite SMRs — the whole point of going sub-major."""
    sub = pd.DataFrame({"91": [80.0, 20.0], "92": [20.0, 80.0]},
                       index=["M_trades", "M_admin"])
    w = pd.Series({"91": 292.0, "92": 100.0})
    comp, within = composite_smr(sub, w)
    assert comp.loc["M_trades", "9"] == pytest.approx(0.8 * 292 + 0.2 * 100)
    assert comp.loc["M_admin", "9"] == pytest.approx(0.2 * 292 + 0.8 * 100)
    assert comp.loc["M_trades", "9"] > comp.loc["M_admin", "9"]
    assert within["9"].loc["M_trades", "91"] == pytest.approx(0.8)


def test_major_group_with_no_men_falls_back_to_the_national_mix():
    """An empty major group has no local mix; it must borrow the national one
    rather than produce NaN and silently void the area's score."""
    sub = pd.DataFrame({"91": [80.0, 0.0], "92": [20.0, 0.0]},
                       index=["M_has", "M_empty"])
    comp, _ = composite_smr(sub, pd.Series({"91": 292.0, "92": 100.0}))
    assert not math.isnan(comp.loc["M_empty", "9"])
    # national mix is 80:20, so the empty area gets the same as the only area
    assert comp.loc["M_empty", "9"] == pytest.approx(comp.loc["M_has", "9"])


def test_composite_ignores_major_groups_absent_from_the_data():
    sub = pd.DataFrame({"91": [10.0], "92": [10.0]}, index=["M"])
    comp, _ = composite_smr(sub, pd.Series({"91": 292.0, "92": 100.0}))
    assert list(comp.columns) == ["9"]


# --- what reaches the app --------------------------------------------------
def test_top_groups_payload_is_shaped_for_the_map():
    """score.py copies this into factor_breakdown and the map reads label/smr."""
    payload = json.dumps([{"soc": "91", "label": SUBMAJOR_LABELS["91"],
                           "smr": 292.0, "contribution": 0.14}])
    top = json.loads(payload)
    assert {"soc", "label", "smr", "contribution"} <= set(top[0])
    assert top[0]["label"] == "Elementary trades and related occupations"
