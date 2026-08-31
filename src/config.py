"""Configuration + path helpers.

A single ``config.yaml`` at the repo root drives every step (paths, year
vintages, weights, catchment thresholds). Nothing else holds hidden state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# Repo root = parent of the src/ package directory.
REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"


class Config:
    """Thin wrapper over the parsed config.yaml with path resolution."""

    def __init__(self, data: dict[str, Any], root: Path = REPO_ROOT):
        self._data = data
        self.root = root

    # -- dict-style access -------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    @property
    def mode(self) -> str:
        return self._data.get("mode", "synthetic")

    @property
    def nations(self) -> list[str]:
        return list(self._data.get("nations", ["E", "W"]))

    # -- path resolution ---------------------------------------------------
    def path(self, key: str) -> Path:
        """Resolve a key under ``paths:`` to an absolute Path, making parent
        directories as needed."""
        rel = self._data["paths"][key]
        p = self.root / rel
        # Treat keys that look like files (have a suffix) vs directories.
        parent = p.parent if p.suffix else p
        parent.mkdir(parents=True, exist_ok=True)
        return p


def load_config(path: Path | str = CONFIG_PATH) -> Config:
    with open(path, "r") as fh:
        data = yaml.safe_load(fh)
    return Config(data)
