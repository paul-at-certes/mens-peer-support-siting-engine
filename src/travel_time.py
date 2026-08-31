"""TravelTimeProvider — abstract interface + haversine stub + real-routing stubs.

The pipeline runs with ZERO external dependencies by default via the haversine
(straight-line) stub. A real routing engine (OSRM / OpenRouteService) or hosted
API is swapped in later behind the same interface without touching any
downstream code. Provision changes rarely, so the real implementations are
expected to precompute and cache the matrix.

Straight-line distance OVER-states accessibility in rural/estuarine geographies
— this caveat is surfaced on the map face until real routing lands.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

_EARTH_RADIUS_KM = 6371.0088


def haversine_km(lon1, lat1, lon2, lat2):
    """Vectorised great-circle distance in km. Inputs may be scalars or arrays."""
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


class TravelTimeProvider(ABC):
    """Computes a travel-time matrix (minutes) from origins to destinations."""

    @abstractmethod
    def matrix_minutes(self, origins, destinations) -> np.ndarray:
        """Return an (n_origins x n_destinations) array of minutes.

        ``origins`` and ``destinations`` are iterables of (lon, lat) pairs.
        """
        raise NotImplementedError


class HaversineTravelTimeProvider(TravelTimeProvider):
    """Straight-line distance converted to minutes at an assumed speed.

    The default, dependency-free stub. Build against this first.
    """

    def __init__(self, assumed_speed_kmh: float = 40.0):
        self.assumed_speed_kmh = float(assumed_speed_kmh)

    def matrix_minutes(self, origins, destinations) -> np.ndarray:
        o = np.asarray(list(origins), dtype=float)   # (n, 2) lon, lat
        d = np.asarray(list(destinations), dtype=float)
        if len(o) == 0 or len(d) == 0:
            return np.full((len(o), len(d)), np.inf)
        # Broadcast: origins on axis 0, destinations on axis 1.
        olon = o[:, 0][:, None]
        olat = o[:, 1][:, None]
        dlon = d[:, 0][None, :]
        dlat = d[:, 1][None, :]
        dist_km = haversine_km(olon, olat, dlon, dlat)
        return dist_km / self.assumed_speed_kmh * 60.0


class OSRMTravelTimeProvider(TravelTimeProvider):
    """Road-network travel time via a self-hosted OSRM ``/table`` service.

    Computes the full origin x destination duration matrix and caches it, keyed
    by a content hash of the inputs — provision changes rarely, so the expensive
    matrix is built once and reused across runs. OSRM caps the number of
    coordinates per /table request (``--max-table-size``), so origins are sent in
    chunks while all destinations ride along in every request.

    Stand up an OSRM server (one-off; you can tear it down after the matrix is
    cached), e.g. with a GB extract from Geofabrik::

        docker run -t -v "$PWD:/data" osrm/osrm-backend osrm-extract -p \
            /opt/car.lua /data/great-britain-latest.osm.pbf
        docker run -t -v "$PWD:/data" osrm/osrm-backend osrm-partition /data/great-britain-latest.osrm
        docker run -t -v "$PWD:/data" osrm/osrm-backend osrm-customize  /data/great-britain-latest.osrm
        docker run -p 5000:5000 -v "$PWD:/data" osrm/osrm-backend osrm-routed \
            --algorithm mld --max-table-size 5000 /data/great-britain-latest.osrm

    then set ``accessibility.provider: osrm`` and ``accessibility.osrm.base_url``
    in config.yaml. Haversine stays the default, so the pipeline needs no server.
    Failures raise (after retries) — we never silently fall back to straight-line.
    """

    def __init__(self, base_url: str | None, profile: str = "driving",
                 max_table_size: int = 2000, cache_dir=None, timeout: int = 120):
        if not base_url:
            raise ValueError(
                "OSRMTravelTimeProvider needs accessibility.osrm.base_url "
                "(e.g. http://localhost:5000).")
        self.base_url = base_url.rstrip("/")
        self.profile = profile
        self.max_table_size = int(max_table_size)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.timeout = timeout

    def _cache_path(self, origins: np.ndarray, destinations: np.ndarray):
        if not self.cache_dir:
            return None
        h = hashlib.sha1()
        for part in (self.base_url, self.profile):
            h.update(part.encode())
        h.update(origins.tobytes())
        h.update(destinations.tobytes())
        return self.cache_dir / f"osrm_matrix_{h.hexdigest()[:16]}.npz"

    def matrix_minutes(self, origins, destinations) -> np.ndarray:
        o = np.asarray(list(origins), dtype=float)
        d = np.asarray(list(destinations), dtype=float)
        if len(o) == 0 or len(d) == 0:
            return np.full((len(o), len(d)), np.inf)

        cache = self._cache_path(o, d)
        if cache and cache.exists():
            return np.load(cache)["minutes"]

        n_dest = len(d)
        if n_dest >= self.max_table_size:
            raise ValueError(
                f"{n_dest} destinations >= max_table_size={self.max_table_size}. "
                f"Increase the server's --max-table-size and "
                f"accessibility.osrm.max_table_size above {n_dest}.")
        chunk = max(1, self.max_table_size - n_dest)
        out = np.empty((len(o), n_dest), dtype=float)
        for start in range(0, len(o), chunk):
            block = o[start:start + chunk]
            out[start:start + len(block)] = self._table(block, d)

        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(cache, minutes=out)
        return out

    def _table(self, origins_block: np.ndarray, destinations: np.ndarray) -> np.ndarray:
        from .fetch import get as http_get   # reuse retry/backoff + UA
        coords = np.vstack([origins_block, destinations])
        coord_str = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in coords)
        n_o, n_d = len(origins_block), len(destinations)
        params = {
            "sources": ";".join(str(i) for i in range(n_o)),
            "destinations": ";".join(str(i) for i in range(n_o, n_o + n_d)),
            "annotations": "duration",
        }
        url = f"{self.base_url}/table/v1/{self.profile}/{coord_str}"
        data = http_get(url, params=params, timeout=self.timeout).json()
        if data.get("code") != "Ok":
            raise RuntimeError(
                f"OSRM /table error: {data.get('code')} {data.get('message', '')}")
        # durations[i][j] in seconds; None = unroutable -> inf minutes.
        durations = data["durations"]
        return np.array([[np.inf if v is None else v / 60.0 for v in row]
                         for row in durations], dtype=float)


class ORSTravelTimeProvider(TravelTimeProvider):
    """STUB — OpenRouteService hosted/self-hosted matrix API. See OSRM stub."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = base_url

    def matrix_minutes(self, origins, destinations) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError(
            "ORSTravelTimeProvider is a stub. Use provider: haversine in "
            "config.yaml, or implement the ORS matrix call + cache."
        )


def get_provider(cfg) -> TravelTimeProvider:
    """Factory: build the provider named in config.yaml under accessibility."""
    acc = cfg["accessibility"]
    name = acc.get("provider", "haversine")
    if name == "haversine":
        return HaversineTravelTimeProvider(acc.get("assumed_speed_kmh", 40.0))

    # Real providers cache their matrix under accessibility.matrix_cache.
    cache_rel = acc.get("matrix_cache")
    cache_dir = (cfg.root / cache_rel) if cache_rel else None

    if name == "osrm":
        o = acc.get("osrm", {}) or {}
        return OSRMTravelTimeProvider(
            o.get("base_url"), o.get("profile", "driving"),
            o.get("max_table_size", 2000), cache_dir)
    if name == "ors":
        o = acc.get("ors", {}) or {}
        return ORSTravelTimeProvider(o.get("api_key"), o.get("base_url"))
    raise ValueError(f"Unknown travel-time provider: {name!r}")
