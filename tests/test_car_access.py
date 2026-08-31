"""Car or van availability: schema, ranges, and the guarantee that it is
DESCRIPTIVE ONLY.

The last test is the important one. `no_car_share` exists to say where the
car-only travel time overstates access; the moment it moves a score it stops
being context and becomes an unweighted, undeclared fourth factor.
"""

import pandas as pd
import pytest

from src import pipeline
from src.caveats import HIGH_NO_CAR_SHARE, car_access_note
from src.ingest import car_access
from tests.test_pipeline import _temp_cfg


def test_fact_car_access_schema_and_ranges(tmp_path):
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)

    car = pd.read_parquet(cfg.path("interim") / "fact_car_access.parquet")
    assert set(car.columns) == {"area_code", "households", "no_car_households",
                                "no_car_share"}
    assert car["area_code"].is_unique
    assert len(car) == 25 * 5
    # Counts are household counts, not person-level rows.
    assert (car["households"] > 0).all()
    assert (car["no_car_households"] >= 0).all()
    assert (car["no_car_households"] <= car["households"]).all()
    # The share is a share.
    assert car["no_car_share"].between(0, 1).all()
    assert car["no_car_share"].notna().all()


def test_no_car_share_reaches_fact_score(tmp_path):
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)

    score = pd.read_parquet(cfg.path("fact_score"))
    car = pd.read_parquet(cfg.path("interim") / "fact_car_access.parquet")
    assert "no_car_share" in score.columns
    assert score["no_car_share"].notna().all()
    assert score["no_car_share"].between(0, 1).all()
    # It is the ingested figure for that area, not a recomputed or reordered one.
    merged = score.merge(car, on="area_code", suffixes=("_score", "_car"))
    assert len(merged) == len(score)
    assert (merged["no_car_share_score"] - merged["no_car_share_car"]).abs().max() < 1e-12


def test_priority_score_unchanged_without_car_data(tmp_path, monkeypatch):
    """The load-bearing test: car data must not move a single number in the score.

    Runs the whole pipeline twice into separate temp roots — once with the car
    ingest, once with it failing the way a lost fetch would — and demands every
    scored column match exactly. Only `no_car_share` itself may differ.
    """
    with_car = _temp_cfg(tmp_path / "with_car")
    pipeline.run(with_car)
    a = pd.read_parquet(with_car.path("fact_score"))

    without = _temp_cfg(tmp_path / "without_car")

    def _boom(_cfg):
        raise RuntimeError("no car availability data")

    monkeypatch.setattr(car_access, "run", _boom)
    pipeline.run(without)
    b = pd.read_parquet(without.path("fact_score"))

    assert not (without.path("interim") / "fact_car_access.parquet").exists()
    # Absent data shows as absent, never as a substituted value.
    assert b["no_car_share"].isna().all()
    assert a["no_car_share"].notna().all()

    # Same areas, same order, same everything else — including the JSON factor
    # breakdown, so the weights and contributions are untouched too.
    assert list(a.columns) == list(b.columns)
    for col in a.columns:
        if col == "no_car_share":
            continue
        pd.testing.assert_series_equal(a[col], b[col], check_exact=(
            col not in ("need_index", "supply_index", "priority_score",
                        "reach_score", "percentile", "travel_minutes")))

    # And the derived outputs the shortlist is actually read from.
    ta = pd.read_parquet(with_car.path("fact_tier"))
    tb = pd.read_parquet(without.path("fact_tier"))
    pd.testing.assert_frame_equal(ta, tb)


def test_car_access_note_only_warns_when_the_share_is_high():
    """The plain-English copy is shared by the map and the PDF (src/caveats.py)."""
    low = car_access_note(HIGH_NO_CAR_SHARE - 0.01)
    high = car_access_note(HIGH_NO_CAR_SHARE + 0.01)
    assert "overstates" not in low
    assert "overstates" in high
    for note in (low, high):
        assert "no car or van" in note
    # A missing figure says so rather than implying good access.
    assert "no way to tell" in car_access_note(float("nan"))
    assert "no way to tell" in car_access_note(None)


def test_extract_check_rejects_a_truncated_or_empty_extract():
    """Nomis fails quietly two ways: a geography type that returns rows with no
    values, and the 25,000-row page cap. Both must fail loudly here."""
    full = pd.DataFrame({
        "area_code": [f"E0100{i:04d}" for i in range(car_access.EXPECTED_AREAS)],
        "households": 825,
        "no_car_households": 192,
    })
    car_access._check_extract(full)   # 23% no-car on 24.75m households: passes

    with pytest.raises(ValueError, match="truncated"):
        car_access._check_extract(full.head(25_000))
    with pytest.raises(ValueError, match="missing values"):
        car_access._check_extract(full.assign(no_car_households=pd.NA))
    with pytest.raises(ValueError, match="no-car share"):
        car_access._check_extract(full.assign(no_car_households=0))
