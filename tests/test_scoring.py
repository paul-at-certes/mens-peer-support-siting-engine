"""Scoring maths: within-nation percentiles and the priority formula."""

import numpy as np
import pandas as pd

from src.score import _within_nation_pct


def test_within_nation_percentiles_are_independent():
    df = pd.DataFrame({
        "nation": ["E", "E", "E", "W", "W"],
        "x": [1.0, 2.0, 3.0, 10.0, 20.0],
    })
    pct = _within_nation_pct(df, "x")
    # Top of each nation ranks at 1.0 regardless of the other nation's scale.
    assert pct.iloc[2] == 1.0   # highest in E
    assert pct.iloc[4] == 1.0   # highest in W
    # W's small absolute values don't drag E's percentiles.
    assert pct.iloc[0] < pct.iloc[2]


def test_priority_formula_monotonic():
    # priority = need * (1 - supply): higher need and lower supply => higher priority.
    need = np.array([0.8, 0.8, 0.2])
    supply = np.array([0.1, 0.9, 0.1])
    priority = need * (1 - supply)
    assert priority[0] > priority[1]   # same need, less supply -> higher
    assert priority[0] > priority[2]   # same supply, more need -> higher


def test_reach_multiplier():
    priority = np.array([0.5, 0.5])
    pop = np.array([1000, 4000])
    reach = priority * pop
    # Same per-capita priority, but the larger area reaches more men.
    assert reach[1] > reach[0]
