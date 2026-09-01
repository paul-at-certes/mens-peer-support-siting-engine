"""The two places a network failure could change a number without saying so.

Both contradicted the repo's own "fail loudly" rule, and both failed quietly in
the direction that moves the shortlist: an empty cache that later runs prefer to
the network, and a partial harvest that makes served areas look underserved.
"""

import json

import pandas as pd
import pytest

from src import fetch
from src.config import Config
from src.io_utils import MissingSourceError
from src.ingest import provision


class _Resp:
    def __init__(self, text):
        self.text = text


# --- 1. a transient miss must not become a permanent one -------------------

def test_an_empty_nomis_response_is_not_cached(tmp_path, monkeypatch, capsys):
    """One blip would otherwise write a zero-row CSV that every later run reads
    in preference to the network, and the caller's own empty check fires too
    late to help — the poisoned cache is already on disk."""
    monkeypatch.setattr(fetch, "get", lambda url, **kw: _Resp("GEOGRAPHY_CODE,OBS_VALUE\n"))
    cache = tmp_path / "nomis.csv"

    df = fetch.nomis_csv_all("https://example.test/x.data.csv", {}, cache_path=cache)

    assert df.empty
    assert not cache.exists()
    assert "not caching" in capsys.readouterr().out


def test_a_populated_nomis_response_is_still_cached(tmp_path, monkeypatch):
    """The guard must not cost us the caching it guards."""
    monkeypatch.setattr(fetch, "get", lambda url, **kw: _Resp(
        "GEOGRAPHY_CODE,OBS_VALUE\nE06000001,7\n"))
    cache = tmp_path / "nomis.csv"

    df = fetch.nomis_csv_all("https://example.test/x.data.csv", {}, cache_path=cache)

    assert len(df) == 1
    assert cache.exists()
    assert pd.read_csv(cache)["OBS_VALUE"].tolist() == [7]


# --- 2. a partial harvest must not pass for a complete one -----------------

def _grid(monkeypatch, fail_every=None):
    """Answer the WP Store Locator grid, failing on every nth request."""
    calls = {"n": 0}

    def fake_get(url, **kw):
        calls["n"] += 1
        if fail_every and calls["n"] % fail_every == 0:
            raise ConnectionError("boom")
        return _Grid()

    monkeypatch.setattr(provision, "get", fake_get)
    return calls


class _Grid:
    def json(self):
        return [{"id": "1", "lat": 53.5, "lng": -1.5, "name": "A club"}]


def test_a_wholly_failed_harvest_is_refused_rather_than_cached(tmp_path, monkeypatch):
    _grid(monkeypatch, fail_every=1)
    cache = tmp_path / "amc_groups.json"

    with pytest.raises(MissingSourceError, match="Harvest incomplete"):
        provision._harvest(cache, delay=0)

    assert not cache.exists()


def test_a_few_scattered_failures_still_harvest(tmp_path, monkeypatch, capsys):
    """The grid overlaps heavily, so an isolated miss is covered by its
    neighbours. The point is that the count is always printed, not that any
    failure is fatal."""
    _grid(monkeypatch, fail_every=100)          # 1%, under the 5% limit
    cache = tmp_path / "amc_groups.json"

    provision._harvest(cache, delay=0)

    assert json.loads(cache.read_text())
    out = capsys.readouterr().out
    assert "grid requests" in out and "failed" in out


def test_a_200_that_is_not_a_list_of_stores_counts_as_a_failure(tmp_path, monkeypatch):
    """The site can decline every request in valid JSON. Without counting those,
    the harvest reports 0.0% failed and caches a fraction of the groups -- the
    silent understatement of provision the guard exists to stop."""
    class _Declined:
        def json(self):
            return {"success": False, "message": "rate limited"}

    monkeypatch.setattr(provision, "get", lambda url, **kw: _Declined())
    cache = tmp_path / "amc_groups.json"

    with pytest.raises(MissingSourceError, match="Harvest incomplete"):
        provision._harvest(cache, delay=0)

    assert not cache.exists()


def test_the_failure_count_is_reported_even_when_nothing_failed(tmp_path, monkeypatch, capsys):
    _grid(monkeypatch)
    provision._harvest(tmp_path / "amc_groups.json", delay=0)
    assert "0 failed (0.0%)" in capsys.readouterr().out


# --- 3. a vanished column must not take the run down ----------------------

def test_a_harvest_without_open_status_defaults_to_active(tmp_path):
    """df.get returns its default verbatim, so a bare "OPEN" string would hit
    .map and raise AttributeError on the one run where the column is missing."""
    cfg = Config({"mode": "real",
                  "paths": {"raw": "raw", "real_raw": "raw/real"}}, root=tmp_path)
    (cfg.path("raw") / provision.HARVEST_NAME).write_text(json.dumps([
        {"id": "1", "lat": 53.5, "lng": -1.5, "name": "A club", "postcode": "HD1 1AA"},
    ]))

    dest = tmp_path / "provision.csv"
    out = pd.read_csv(provision._build_real(cfg, dest))

    assert out["status"].tolist() == ["active"]
