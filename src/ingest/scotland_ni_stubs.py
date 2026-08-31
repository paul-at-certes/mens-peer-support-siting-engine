"""Scotland & Northern Ireland adapters — DOCUMENTED STUBS (not implemented).

v1 ships England & Wales. Scotland and NI sit behind the SAME ingest interface
(`run(cfg) -> DataFrame keyed to area_code`) so they can be wired in without
touching anything downstream. They are deliberately separate because the data is
non-comparable across borders:

  * Geography:    Data Zone (Scotland) / SOA (Northern Ireland), not LSOA.
  * Deprivation:  SIMD (Scotland) / NIMDM (Northern Ireland) — NOT comparable
                  with England IMD or Welsh WIMD; normalise WITHIN nation only.
  * Census:       Scotland ran Census 2022 (different year to E&W 2021); NI 2021
                  via NISRA.
  * Suicide:      National Records of Scotland (NRS) / NISRA publish separately
                  from ONS, with their own geographies and disclosure rules.

When implemented, each function below mirrors its England & Wales counterpart in
src/ingest/ but points at the nation's sources and emits the same columns, so
score.py's within-nation percentile logic absorbs them unchanged.
"""

from __future__ import annotations

from ..config import Config


class NotImplementedStub(NotImplementedError):
    pass


def _stub(nation: str, source: str):
    raise NotImplementedStub(
        f"{nation} adapter for '{source}' is a v1 stub. England & Wales ship "
        f"first; implement against the nation's source emitting the same "
        f"area_code-keyed columns, then add its code to config.yaml `nations`."
    )


# --- Scotland --------------------------------------------------------------
def scotland_deprivation(cfg: Config):
    _stub("Scotland", "SIMD deprivation (Data Zone)")


def scotland_occupation(cfg: Config):
    _stub("Scotland", "Census 2022 occupation (Data Zone)")


def scotland_suicide(cfg: Config):
    _stub("Scotland", "NRS suicide by council area")


# --- Northern Ireland ------------------------------------------------------
def ni_deprivation(cfg: Config):
    _stub("Northern Ireland", "NIMDM deprivation (SOA)")


def ni_occupation(cfg: Config):
    _stub("Northern Ireland", "Census 2021 occupation (SOA)")


def ni_suicide(cfg: Config):
    _stub("Northern Ireland", "NISRA suicide statistics")
