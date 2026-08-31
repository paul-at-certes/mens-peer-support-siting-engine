"""Entry point. Run with:  streamlit run app/streamlit_app.py

Routing only — each page lives in app/views/. st.navigation is used rather than
a pages/ directory so the pages carry proper names in the sidebar instead of
labels derived from their filenames.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Make the repo root importable so `src` resolves for every page.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="Men's Peer-Support Siting Engine", layout="wide")

VIEWS = Path(__file__).resolve().parent / "views"
st.navigation([
    st.Page(str(VIEWS / "priority_map.py"), title="Priority map", icon="🗺️", default=True),
    st.Page(str(VIEWS / "guide.py"), title="Beginner's guide", icon="📖"),
]).run()
