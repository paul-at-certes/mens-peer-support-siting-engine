"""The OSRM matrix cache must survive an interrupted write.

The matrix takes ~2 minutes to build, so the window for a Ctrl-C, timeout or
crash mid-write is wide. A truncated .npz left under the real filename poisons
every later run with an opaque BadZipFile, which is exactly what happened once.
Two guarantees: writes are atomic, and an unreadable cache is a miss, not a fault.
"""

import numpy as np
import pytest

from src.travel_time import OSRMTravelTimeProvider

ORIGINS = [(-1.5, 53.8), (-1.6, 53.9), (-2.0, 53.4)]
DESTS = [(-1.55, 53.85), (-1.9, 53.5)]


class _StubOSRM(OSRMTravelTimeProvider):
    """Counts round-trips so cache hits are observable without a live server."""

    def __init__(self, cache_dir):
        super().__init__("http://stub:5001", cache_dir=cache_dir)
        self.calls = 0

    def _table(self, origins_block, destinations):
        self.calls += 1
        return np.full((len(origins_block), len(destinations)), 7.5)


def test_matrix_is_cached_and_reused(tmp_path):
    p = _StubOSRM(tmp_path)
    first = p.matrix_minutes(ORIGINS, DESTS)
    assert p.calls == 1 and first.shape == (3, 2)

    second = _StubOSRM(tmp_path)
    got = second.matrix_minutes(ORIGINS, DESTS)
    assert second.calls == 0, "second run should hit the cache, not the server"
    np.testing.assert_array_equal(got, first)


def test_write_leaves_no_temp_file_behind(tmp_path):
    _StubOSRM(tmp_path).matrix_minutes(ORIGINS, DESTS)
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob("*.tmp.npz")) == []
    # Exactly one cache file, and it is readable.
    caches = list(tmp_path.glob("osrm_matrix_*.npz"))
    assert len(caches) == 1
    np.load(caches[0])["minutes"]


def test_truncated_cache_is_discarded_not_fatal(tmp_path, capsys):
    """An interrupted write must not poison every later run."""
    p = _StubOSRM(tmp_path)
    expected = p.matrix_minutes(ORIGINS, DESTS)
    cache = next(tmp_path.glob("osrm_matrix_*.npz"))
    cache.write_bytes(b"\x50\x4b\x03\x04truncated-mid-write")   # partial zip

    recovered = _StubOSRM(tmp_path)
    got = recovered.matrix_minutes(ORIGINS, DESTS)
    assert recovered.calls == 1, "corrupt cache should be treated as a miss"
    np.testing.assert_array_equal(got, expected)
    assert "discarding unreadable matrix cache" in capsys.readouterr().out
    # And the rebuilt cache is usable again.
    again = _StubOSRM(tmp_path)
    again.matrix_minutes(ORIGINS, DESTS)
    assert again.calls == 0


def test_destination_count_must_fit_the_table_limit(tmp_path):
    p = _StubOSRM(tmp_path)
    p.max_table_size = 2
    with pytest.raises(ValueError, match="max_table_size"):
        p.matrix_minutes(ORIGINS, DESTS)
