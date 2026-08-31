"""Small shared helpers for ingestion: loud failures and schema validation.

Most UK statistical data is downloaded manually and file layouts drift, so every
ingest step must (a) fail loudly with a clear "place file X here" message when a
required source is missing, and (b) validate that the expected columns are
present before doing any work.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


class MissingSourceError(FileNotFoundError):
    """Raised when a required raw source file is absent."""


def require_file(path: Path, source_label: str, url_hint: str = "") -> Path:
    """Return ``path`` if it exists, else raise a clear, actionable error."""
    if not path.exists():
        hint = f"\n  Source: {url_hint}" if url_hint else ""
        raise MissingSourceError(
            f"Required source for '{source_label}' not found.\n"
            f"  Place the file here: {path}{hint}\n"
            f"  (Or set mode: synthetic in config.yaml to run on the fixture.)"
        )
    return path


def validate_columns(df: pd.DataFrame, required: list[str], source_label: str) -> pd.DataFrame:
    """Assert that ``df`` has at least the ``required`` columns."""
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"'{source_label}' is missing expected column(s): {missing}. "
            f"Found columns: {list(df.columns)}"
        )
    return df


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)
