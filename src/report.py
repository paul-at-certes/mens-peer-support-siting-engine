"""Shortlist PDF report — the top-N areas for a new peer-support group.

Run with:  python -m src.report

A *rendering* of what the pipeline already produced — it reads
``fact_score.parquet`` (+ ``weights.json`` and ``sensitivity.json``) and writes a
clear, shareable PDF to ``paths.report``. It runs NO new analysis: every number
is the exact score the app shows, so the printed shortlist and the interactive
map can never disagree.

The report features the REACH view by default (``report.view``): areas ranked by
``priority_score × male working-age population`` — "the most men reached per new
group" — which is the lens for a national where-to-open-next decision.

reportlab is pure-Python and optional; install it with::

    pip install -e ".[report]"
"""

from __future__ import annotations

import json
import sys

import pandas as pd

from .config import Config, load_config

# Human-readable factor names (match the design note / app language).
FACTOR_LABEL = {
    "deprivation": "income & employment deprivation",
    "occupation": "high-risk male occupation share",
    "isolation": "male isolation",
}
NATION_NAME = {"E": "England", "W": "Wales", "S": "Scotland", "N": "Northern Ireland"}


def _require_reportlab():
    """Import reportlab lazily, failing loudly with an install hint (mirrors the
    'place file X here' pattern used for missing raw files in src/ingest/*)."""
    try:
        import reportlab  # noqa: F401
    except ModuleNotFoundError as e:  # pragma: no cover - trivial guard
        raise SystemExit(
            "The PDF report needs reportlab, which is an optional dependency.\n"
            "Install it with:  pip install -e \".[report]\"   (or: pip install reportlab)"
        ) from e


# --- Plain-English helpers --------------------------------------------------

def _band(pct: float) -> str:
    """Percentile -> qualitative wording."""
    if pct >= 0.95:
        return "very high"
    if pct >= 0.80:
        return "high"
    if pct >= 0.60:
        return "elevated"
    if pct >= 0.40:
        return "moderate"
    return "lower"


def _ord_pct(pct: float) -> str:
    """0.97 -> '97th percentile'."""
    n = int(round(pct * 100))
    n = min(max(n, 1), 100)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix} percentile"


def _join(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _a(n: int) -> str:
    """'a' / 'an' for a spoken number (8, 11, 18, 80s start with a vowel sound)."""
    return "an" if n in (8, 11, 18) or 80 <= n <= 89 else "a"


def build_reasoning(row: pd.Series, fb: dict, catchment_minutes: int) -> str:
    """One plain-English sentence explaining why this area is on the shortlist,
    naming its dominant factors straight from the factor_breakdown JSON."""
    nation = NATION_NAME.get(row["nation"], row["nation"])
    comps = fb["components"]

    # Feature the factors that are most ELEVATED (by percentile — that's what
    # stands out to a reader), not by weighted contribution, which reflects the
    # weighting choice and can bury a genuinely high but lightly-weighted proxy.
    items = [(k, comps[k]["percentile"]) for k in ("deprivation", "occupation", "isolation")]
    items.sort(key=lambda x: x[1], reverse=True)
    chosen = [it for it in items if it[1] >= 0.60][:3]
    if len(chosen) < 2:
        chosen = items[:2]
    phrases = [f"{_band(p)} {FACTOR_LABEL[k]} ({_ord_pct(p)})" for k, p in chosen]
    driver = f"Driven by {_join(phrases)} within {nation}."

    # Optional: the wider LA suicide signal, if it is itself high.
    sig = fb.get("suicide_signal", {})
    sig_pct = sig.get("percentile")
    sig_clause = ""
    if sig_pct is not None and sig_pct >= 0.80 and sig.get("la_rate_per_100k") is not None:
        sig_clause = (f" The wider Local-Authority male suicide signal is also "
                      f"elevated ({_ord_pct(sig_pct)}).")

    # Supply / accessibility.
    trav = int(round(float(row["travel_minutes"])))
    within = int(row["groups_within_catchment"])
    if within == 0:
        supply = (f" No existing group lies within the {catchment_minutes}-minute "
                  f"catchment; the nearest is {_a(trav)} {trav}-minute drive.")
    else:
        grp, verb = ("group", "lies") if within == 1 else ("groups", "lie")
        supply = (f" {within} {grp} already {verb} within the {catchment_minutes}-minute "
                  f"catchment; the nearest is {_a(trav)} {trav}-minute drive.")

    # Reach.
    pop = int(round(float(row["male_working_age_pop"])))
    reach = f" A new group here would be within reach of ~{pop:,} working-age men."

    return driver + sig_clause + supply + reach


def robustness_cell(area_code: str, robustness: dict):
    """(label, is_present) for the robustness column, using the app's thresholds."""
    if area_code in robustness:
        r = float(robustness[area_code])
        if r >= 0.80:
            return f"Robust ({r:.0%})", True
        if r >= 0.50:
            return f"Moderate ({r:.0%})", True
        return f"Low ({r:.0%})", True
    return "—", False   # em dash


# --- PDF assembly -----------------------------------------------------------

def run(cfg: Config) -> "Path":  # type: ignore[name-defined]
    _require_reportlab()
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    rep = cfg.get("report", {}) or {}
    top_n = int(rep.get("top_n", 20))
    view = rep.get("view", "reach")
    rank_col = "rank_reach" if view == "reach" else "rank"
    catchment = int(cfg["accessibility"].get("catchment_minutes", 30))

    df = pd.read_parquet(cfg.path("fact_score"))
    weights_meta = json.loads(cfg.path("weights").read_text()) if cfg.path("weights").exists() else {}
    sens = json.loads(cfg.path("sensitivity").read_text()) if cfg.path("sensitivity").exists() else {}
    robustness = sens.get("area_robustness", {})

    top = df.sort_values(rank_col).head(top_n).reset_index(drop=True)

    # --- styles -------------------------------------------------------------
    styles = getSampleStyleSheet()
    NAVY = colors.HexColor("#1b2a4a")
    ACCENT = colors.HexColor("#b3122b")
    GREY = colors.HexColor("#555555")
    h1 = ParagraphStyle("h1", parent=styles["Title"], textColor=NAVY, fontSize=20,
                        spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], textColor=GREY, fontSize=9,
                         spaceAfter=10)
    box = ParagraphStyle("box", parent=styles["Normal"], fontSize=9.5, leading=13,
                         backColor=colors.HexColor("#eef1f6"), borderColor=NAVY,
                         borderWidth=0.6, borderPadding=8, spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=NAVY, fontSize=13,
                        spaceBefore=8, spaceAfter=6)
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7.5, leading=9)
    cell_b = ParagraphStyle("cellb", parent=cell, fontName="Helvetica-Bold")
    reason_num = ParagraphStyle("rnum", parent=styles["Normal"], fontSize=9.5,
                                fontName="Helvetica-Bold", textColor=NAVY)
    reason = ParagraphStyle("reason", parent=styles["Normal"], fontSize=9, leading=12.5)
    foot = ParagraphStyle("foot", parent=styles["Normal"], fontSize=7.5, leading=10,
                          textColor=GREY)

    story = []

    # --- title + framing box ------------------------------------------------
    story.append(Paragraph(rep.get("title", "Shortlist for a New Group"), h1))
    scheme = weights_meta.get("active_scheme", "?")
    n_las = weights_meta.get("n_las", "?")
    view_label = "reach — most men reached per new group" if view == "reach" \
        else "per-capita — most acute unmet need"
    story.append(Paragraph(
        f"England &amp; Wales · ranked by <b>{view_label}</b> · "
        f"weighting scheme <b>{scheme}</b> (calibrated on {n_las} local authorities).",
        sub))
    story.append(Paragraph(
        "<b>What this is.</b> A prioritised shortlist of small areas (LSOAs) where the "
        "combination of latent need — deprivation, high-risk male occupations and "
        "isolation, informed by a Local-Authority suicide signal — and thin existing "
        "provision is greatest. <b>What this is not.</b> It does not predict deaths or "
        "score any individual; it allocates attention across areas. Treat it as a "
        "<b>starting shortlist for local judgement</b> — final siting depends on venue, "
        "volunteers and partner appetite, which no dataset captures.", box))

    # --- how to read --------------------------------------------------------
    story.append(Paragraph("How to read this", h2))
    story.append(Paragraph(
        "<b>Reach</b> ranks each area by its priority score multiplied by the local "
        "male 16–64 population, so larger areas with genuine need rise to the top — the "
        "goal being to reach the most men per new group. <b>Need index</b> (0–1) is the "
        "weighted blend of the risk proxies; <b>priority score</b> is that need after "
        "subtracting how well-served the area already is by road (nearer existing groups "
        "lower it). All proxies are ranked as <b>within-nation percentiles</b> — English "
        "and Welsh deprivation indices are not comparable across the border, so each "
        "nation is scored against itself. Scotland and Northern Ireland are out of scope "
        "for this version.", reason))
    story.append(Spacer(1, 4))

    # --- table --------------------------------------------------------------
    story.append(Paragraph(f"Top {len(top)} areas by reach", h2))
    header = ["#", "Area (LSOA)", "Local authority", "Region", "Male<br/>16–64",
              "Priority<br/>score", "Nearest<br/>group", "Robustness"]
    data = [[Paragraph(f"<b>{h}</b>", cell) for h in header]]
    any_missing_rob = False
    for i, r in top.iterrows():
        rob_label, present = robustness_cell(r["area_code"], robustness)
        any_missing_rob = any_missing_rob or not present
        trav = int(round(float(r["travel_minutes"])))
        data.append([
            Paragraph(str(i + 1), cell_b),
            Paragraph(f"{r['area_name']}<br/><font size=6 color='#777777'>{r['area_code']}</font>", cell),
            Paragraph(str(r["la_name"]), cell),
            Paragraph(str(r["region"]), cell),
            Paragraph(f"{int(r['male_working_age_pop']):,}", cell),
            Paragraph(f"{r['priority_score']:.3f}", cell),
            Paragraph(f"{trav} min", cell),
            Paragraph(rob_label, cell),
        ])
    col_w = [8*mm, 45*mm, 34*mm, 42*mm, 16*mm, 17*mm, 16*mm, 24*mm]
    tbl = Table(data, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f4f8")]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(tbl)

    # Robustness footnote (reach areas mostly fall outside the per-capita test).
    p = sens.get("perturbation", {})
    if any_missing_rob:
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "“Robustness” tests whether an area stays on the shortlist when the "
            "learned weights are perturbed across their confidence intervals. It is "
            f"defined on the <b>per-capita</b> top-{sens.get('shortlist_n', 100)} shortlist, "
            "so most reach-ranked areas here show “—” rather than a fabricated "
            "score. Across the ranking as a whole the shortlist is stable: mean retention "
            f"{p.get('mean_retention', 0):.0%}, with {p.get('n_low_confidence', 0)} "
            "low-confidence area(s) overall.", foot))

    # --- per-area reasoning -------------------------------------------------
    story.append(Spacer(1, 8))
    story.append(Paragraph("Why each area — factor breakdown", h2))
    for i, r in top.iterrows():
        fb = json.loads(r["factor_breakdown"])
        text = build_reasoning(r, fb, catchment)
        block = [
            Paragraph(f"{i + 1}. {r['area_name']} — {r['la_name']} "
                      f"<font size=7 color='#777777'>({r['area_code']})</font>", reason_num),
            Paragraph(text, reason),
            Spacer(1, 5),
        ]
        story.append(KeepTogether(block))

    # --- caveats & vintages (reuse the app's language) ----------------------
    v = cfg["vintages"]
    provider = cfg["accessibility"].get("provider", "haversine")
    if provider == "osrm":
        travel_note = ("real road driving times via a self-hosted OSRM routing engine "
                       "(car; public-transport access is not yet modelled).")
    else:
        travel_note = (f"the {provider} provider — straight-line distance, which "
                       "over-states accessibility in rural/estuarine areas.")
    story.append(Spacer(1, 6))
    story.append(Paragraph("Data vintages &amp; caveats", h2))
    caveats = [
        f"<b>Suicide signal:</b> {v['suicide']}. Local-Authority grain only, with a "
        "~200–270 day registration lag and small-number suppression, so it informs the "
        "<b>weighting</b> of the proxies, not fine-grained ranking. No small-area suicide "
        "rate is fabricated; the source is age 10+ and England-only, so weights are "
        "England-learned and Welsh areas carry a neutral suicide term.",
        f"<b>Deprivation:</b> {v['deprivation']}. Within-nation percentiles only "
        "(England scores, Wales ranks — not comparable across the border). It is collinear "
        "with the occupation proxy; the active univariate scheme weights each proxy by its "
        "own association with suicide so all three still contribute.",
        f"<b>Occupation:</b> {v['census']}. Residence-based (where high-risk workers live, "
        "not where they work) and at SOC major-group resolution — a broad proxy.",
        "<b>Isolation:</b> male single/separated/divorced (sex-specific) plus the "
        "one-person-household share (a household measure — Census 2021 has no sex-broken "
        "living-alone figure at this grain).",
        f"<b>Population:</b> {v['population']}. <b>Provision:</b> {v['provision']}.",
        f"<b>Travel time:</b> {travel_note}",
        "<b>Latent need, not prediction.</b> Area-level only — never read as a statement "
        "about any individual. Final siting needs local judgement.",
    ]
    for c in caveats:
        story.append(Paragraph("• " + c, foot))
        story.append(Spacer(1, 2))

    # --- build --------------------------------------------------------------
    out_path = cfg.path("report")
    doc = SimpleDocTemplate(
        str(out_path), pagesize=landscape(A4),
        leftMargin=14*mm, rightMargin=14*mm, topMargin=12*mm, bottomMargin=12*mm,
        title=rep.get("title", "Shortlist for a New Group"),
        author="Men's Peer-Support Siting Engine",
    )
    doc.build(story)
    return out_path


def main() -> None:
    cfg = load_config()
    out = run(cfg)
    df = pd.read_parquet(cfg.path("fact_score"))
    rep = cfg.get("report", {}) or {}
    rank_col = "rank_reach" if rep.get("view", "reach") == "reach" else "rank"
    top = df.sort_values(rank_col).head(int(rep.get("top_n", 20)))
    print(f"Wrote {out}")
    print(f"Top {len(top)} areas ({rep.get('view', 'reach')} view):")
    for i, (_, r) in enumerate(top.iterrows(), 1):
        print(f"  {i:>2}. {r['area_name']} — {r['la_name']} ({r['region']})")


if __name__ == "__main__":
    sys.exit(main())
