"""Travel-time provider behaviour."""

import numpy as np
import pytest

from src import fetch, travel_time
from src.travel_time import HaversineTravelTimeProvider, haversine_km


def test_haversine_known_distance():
    # London (-0.1276, 51.5072) to Paris (2.3522, 48.8566) ~ 343 km.
    d = haversine_km(-0.1276, 51.5072, 2.3522, 48.8566)
    assert 330 < float(d) < 355


def test_matrix_shape_and_minutes():
    prov = HaversineTravelTimeProvider(assumed_speed_kmh=60)
    origins = [(-0.13, 51.51), (-1.0, 52.0)]
    dests = [(-0.13, 51.51), (0.5, 51.0)]
    m = prov.matrix_minutes(origins, dests)
    assert m.shape == (2, 2)
    # Same point -> ~0 minutes.
    assert m[0, 0] < 0.01
    assert (m >= 0).all()


def test_empty_destinations():
    prov = HaversineTravelTimeProvider()
    m = prov.matrix_minutes([(-0.1, 51.5)], [])
    assert m.shape == (1, 0)


def test_osrm_requires_base_url():
    with pytest.raises(ValueError):
        travel_time.OSRMTravelTimeProvider(None)


def test_osrm_matrix_chunks_and_parses(monkeypatch):
    """OSRM provider chunks origins, converts seconds->minutes, None->inf — all
    without a live server (the HTTP layer is mocked)."""
    calls = []

    def fake_get(url, params=None, timeout=None, **kw):
        n_s = len(params["sources"].split(";"))
        n_d = len(params["destinations"].split(";"))
        calls.append((n_s, n_d))

        class R:
            def json(self):
                # duration source i -> dest j = 60*(j+1) seconds; one unroutable.
                dur = [[60.0 * (j + 1) for j in range(n_d)] for _ in range(n_s)]
                dur[0][0] = None
                return {"code": "Ok", "durations": dur}
        return R()

    monkeypatch.setattr(fetch, "get", fake_get)
    prov = travel_time.OSRMTravelTimeProvider("http://osrm.test", max_table_size=5)
    origins = [(0, 0), (1, 1), (2, 2), (3, 3)]   # 4 origins
    dests = [(10, 10), (11, 11)]                 # 2 dests; chunk = 5-2 = 3

    m = prov.matrix_minutes(origins, dests)
    assert m.shape == (4, 2)
    assert np.isinf(m[0, 0])          # None -> inf
    assert m[1, 1] == 2.0             # 120s -> 2 min
    assert m[2, 0] == 1.0             # 60s  -> 1 min
    assert calls == [(3, 2), (1, 2)]  # origins chunked 3 then 1
