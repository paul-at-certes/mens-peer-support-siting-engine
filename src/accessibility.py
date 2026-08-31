"""fact_accessibility — supply surface from provision + travel time.

For each small-area centroid: minutes to the nearest existing group and the
number of groups reachable within the catchment threshold. The travel-time
matrix is computed via the configured TravelTimeProvider (haversine stub by
default) and is the only heavy compute in the pipeline, so real implementations
precompute and cache it.

Split into ``travel_matrix`` (the expensive part) and ``summarise`` (cheap,
catchment-dependent) so sensitivity.py can re-derive the supply surface at
several catchment thresholds off one matrix.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config
from .travel_time import get_provider


def travel_matrix(cfg: Config, dim_geography: pd.DataFrame,
                  dim_provision: pd.DataFrame) -> np.ndarray:
    """(n_areas x n_groups) minutes, via the configured provider."""
    provider = get_provider(cfg)
    origins = dim_geography[["centroid_lon", "centroid_lat"]].to_numpy()
    dests = dim_provision[["lon", "lat"]].to_numpy()
    return provider.matrix_minutes(origins, dests)


def summarise(minutes: np.ndarray, dim_geography: pd.DataFrame,
              dim_provision: pd.DataFrame, catchment: float) -> pd.DataFrame:
    """Nearest-group minutes + groups within `catchment`, from a computed matrix."""
    if minutes.shape[1] == 0:
        nearest_min = np.full(len(dim_geography), np.inf)
        nearest_idx = np.full(len(dim_geography), -1)
        within = np.zeros(len(dim_geography), dtype=int)
    else:
        nearest_idx = minutes.argmin(axis=1)
        nearest_min = minutes.min(axis=1)
        within = (minutes <= catchment).sum(axis=1)

    return pd.DataFrame({
        "area_code": dim_geography["area_code"].to_numpy(),
        "nearest_group_id": [
            dim_provision["group_id"].iloc[i] if i >= 0 else None for i in nearest_idx
        ],
        "travel_minutes": np.round(nearest_min, 2),
        "groups_within_catchment": within,
        "catchment_minutes": int(catchment),
    })


def run(cfg: Config, dim_geography: pd.DataFrame, dim_provision: pd.DataFrame) -> pd.DataFrame:
    catchment = float(cfg["accessibility"]["catchment_minutes"])
    minutes = travel_matrix(cfg, dim_geography, dim_provision)
    out = summarise(minutes, dim_geography, dim_provision, catchment)
    nearest_min = out["travel_minutes"].to_numpy()
    fp = cfg.path("interim") / "fact_accessibility.parquet"
    out.to_parquet(fp, index=False)
    print(f"[accessibility] {len(out)} areas, provider={cfg['accessibility']['provider']}, "
          f"median nearest={np.nanmedian(nearest_min):.1f} min -> {fp.name}")
    return out
