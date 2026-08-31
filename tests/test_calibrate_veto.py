"""The veto rule: when does the LA fit contradict a declared weight?

Unit-tested against constructed fits rather than the synthetic pipeline, so the
verdicts are deterministic and each branch is exercised on purpose.
"""

from src.calibrate import _veto
from src.config import Config


def _cfg(floor: float = 0.15) -> Config:
    return Config({"calibration": {"unsupported_weight_floor": floor}})


def _fits(dep, occ, iso):
    """Univariate CIs per component, as (lo, hi)."""
    return {"deprivation": {"ci": list(dep)},
            "occupation": {"ci": list(occ)},
            "isolation": {"ci": list(iso)}}


def _components(**flips):
    return {name: {"collinearity_signflip": flips.get(name, False)}
            for name in ("deprivation", "occupation", "isolation")}


DECLARED = {"deprivation": 0.40, "occupation": 0.35, "isolation": 0.25}


def test_all_positive_and_significant_passes():
    v = _veto(_cfg(), DECLARED,
              _fits((0.05, 0.13), (0.11, 0.18), (0.02, 0.09)), _components())
    assert v["status"] == "pass"
    assert v["findings"] == []


def test_ci_below_zero_is_contradicted():
    """The data says the proxy is protective, yet we weight it upward."""
    v = _veto(_cfg(), DECLARED,
              _fits((-0.15, -0.01), (0.11, 0.18), (0.02, 0.09)), _components())
    assert v["status"] == "contradicted"
    finding = next(f for f in v["findings"] if f["component"] == "deprivation")
    assert finding["severity"] == "contradicted"
    # The message reaches the map face and the PDF, so it must name the component
    # and quote the interval rather than leave the reader to guess.
    assert finding["message"].startswith("deprivation:")
    assert "-0.150" in finding["message"] and "-0.010" in finding["message"]


def test_ci_spanning_zero_above_the_floor_is_unsupported():
    v = _veto(_cfg(floor=0.15), DECLARED,
              _fits((0.05, 0.13), (0.11, 0.18), (-0.012, 0.066)), _components())
    assert v["status"] == "unsupported"
    finding = next(f for f in v["findings"] if f["component"] == "isolation")
    assert finding["severity"] == "unsupported"
    assert finding["declared_weight"] == 0.25


def test_ci_spanning_zero_below_the_floor_is_tolerated():
    """A proxy we barely lean on needn't be individually evidenced."""
    declared = {"deprivation": 0.50, "occupation": 0.40, "isolation": 0.10}
    v = _veto(_cfg(floor=0.15), declared,
              _fits((0.05, 0.13), (0.11, 0.18), (-0.012, 0.066)), _components())
    assert v["status"] == "pass"


def test_collinearity_flip_is_informational_not_a_veto():
    v = _veto(_cfg(), DECLARED,
              _fits((0.05, 0.13), (0.11, 0.18), (0.02, 0.09)),
              _components(deprivation=True))
    assert v["status"] == "collinearity"
    assert all(f["severity"] == "collinearity" for f in v["findings"])


def test_contradiction_outranks_the_softer_severities():
    v = _veto(_cfg(), DECLARED,
              _fits((-0.15, -0.01), (0.11, 0.18), (-0.012, 0.066)),
              _components(occupation=True))
    assert v["status"] == "contradicted"
    assert {f["severity"] for f in v["findings"]} == {
        "contradicted", "unsupported", "collinearity"}
