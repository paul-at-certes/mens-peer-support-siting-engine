"""The Connection Scan behind ADR 0002, checked on a hand-computable timetable.

`spikes/` is throwaway research code and nothing in the pipeline imports it —
but ADR 0002 rests entirely on this scan being right, so the scan is tested.
Three stops in a line, far enough apart that no footpath shortcuts the answer.

    trip1:  A dep 17:00  ->  B arr 17:20
    trip2:  B dep 17:30  ->  C arr 17:50

so the only A->C journey leaves at 17:00, changes at B, and lands at 17:50.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spikes.pt_evening_access import Network  # noqa: E402

H = 3600
# ~686 m apart at this latitude: beyond the 400 m footpath radius, so every
# journey below has to be made by vehicle.
STOPS = pd.DataFrame({
    "stop_id": ["A", "B", "C"],
    "stop_name": ["A", "B", "C"],
    "stop_lat": [52.0, 52.0, 52.0],
    "stop_lon": [0.00, 0.01, 0.02],
})


def _net(trip2_dep=17 * H + 30 * 60):
    st = pd.DataFrame({
        "trip_id": ["t1", "t1", "t2", "t2"],
        "stop_id": ["A", "B", "B", "C"],
        "stop_sequence": [0, 1, 0, 1],
        "arr": [17 * H, 17 * H + 20 * 60, trip2_dep, 17 * H + 50 * 60],
        "dep": [17 * H, 17 * H + 20 * 60, trip2_dep, 17 * H + 50 * 60],
    })
    return Network(st, STOPS, transfer_m=400)


def test_no_footpaths_between_these_stops():
    assert len(_net().fp_b) == 0


def test_forward_finds_the_connecting_journey():
    net = _net()
    arr = net.forward([0], [17 * H], min_transfer=60)   # leave A at 17:00
    assert arr[1] == 17 * H + 20 * 60                   # B at 17:20
    assert arr[2] == 17 * H + 50 * 60                   # C at 17:50, via the change


def test_forward_respects_the_interchange_buffer():
    """trip2 leaving B 30s after trip1 lands is not a connection you can make."""
    net = _net(trip2_dep=17 * H + 20 * 60 + 30)
    arr = net.forward([0], [17 * H], min_transfer=60)
    assert arr[1] == 17 * H + 20 * 60                   # still reach B
    assert not np.isfinite(arr[2])                      # but never C


def test_forward_misses_a_departure_already_gone():
    net = _net()
    arr = net.forward([0], [17 * H + 1], min_transfer=60)  # one second too late
    assert not np.isfinite(arr[1])


def test_backward_finds_the_latest_departure():
    """The mirror of the forward case: to be at C by 17:50, leave A at 17:00."""
    net = _net()
    dep = net.backward([2], [17 * H + 50 * 60], min_transfer=60)
    assert dep[1] == 17 * H + 30 * 60                   # leave B at 17:30
    assert dep[0] == 17 * H                             # leave A at 17:00


def test_backward_is_unreachable_when_you_must_arrive_earlier():
    net = _net()
    dep = net.backward([2], [17 * H + 49 * 60], min_transfer=60)
    assert not np.isfinite(dep[0])


def test_footpath_links_stops_within_the_transfer_radius():
    """Widen the radius past 686 m and the walk A->B becomes available."""
    net = _net()
    wide = Network(pd.DataFrame({
        "trip_id": ["t1", "t1"], "stop_id": ["B", "C"], "stop_sequence": [0, 1],
        "arr": [17 * H, 17 * H + 10 * 60], "dep": [17 * H, 17 * H + 10 * 60],
    }), STOPS, transfer_m=800)
    assert len(wide.fp_b) > 0
    # Start at A at 16:00 with time to walk to B, then ride to C.
    arr = wide.forward([0], [16 * H], min_transfer=60)
    assert arr[2] == 17 * H + 10 * 60
