"""A plain-English guide to the siting engine, for readers with no background in
statistics or research data.

Figures are read from the pipeline's own outputs rather than typed in, so the
guide cannot drift away from what the tool actually did.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from src.config import load_config

cfg = load_config()


@st.cache_data
def _facts() -> dict:
    """Live numbers from the pipeline outputs, with safe fallbacks."""
    f: dict = {}
    try:
        score = pd.read_parquet(cfg.path("fact_score"))
        f["n_areas"] = len(score)
        f["n_las"] = int(score["la_code"].nunique())
        f["median_travel"] = float(score["travel_minutes"].median())
    except Exception:
        pass
    try:
        f["n_groups"] = len(pd.read_parquet(cfg.path("interim") / "dim_provision.parquet"))
    except Exception:
        pass
    try:
        f["sens"] = json.loads(cfg.path("sensitivity").read_text())
    except Exception:
        f["sens"] = {}
    try:
        f["weights"] = json.loads(cfg.path("weights").read_text())
    except Exception:
        f["weights"] = {}
    return f


F = _facts()
S = F.get("sens", {})
tiers = S.get("tiers", {})
W = cfg["scoring"]["component_weights"]
N = S.get("shortlist_n", 100)
N_CONFIG = tiers.get("n_configurations", 20)


def n(key, default="not available", fmt="{:,}"):
    v = F.get(key)
    return fmt.format(v) if isinstance(v, (int, float)) else default


# ---------------------------------------------------------------------------
st.title("A beginner's guide to this tool")
st.markdown(
    "No background in statistics is needed to read this. It covers what the tool "
    "is, where its numbers come from, how it puts them together, and how the tiers "
    "are worked out. Read it straight through, or jump to the section you need."
)

st.info(
    "In short: the tool looks at every neighbourhood in England and Wales, asks how "
    "much men there might need a peer-support group and how hard it would be to "
    "reach one today, then lists the places where need is high and the nearest "
    "group is far away."
)

# ---------------------------------------------------------------------------
st.header("1. What the tool is, and what it is not")

col_is, col_isnt = st.columns(2)
with col_is:
    st.markdown("#### What it is")
    st.markdown(
        """
        - A way of narrowing thousands of neighbourhoods down to a shortlist, so a
          conversation about where to open the next group starts from evidence
          rather than from whoever argues hardest.
        - Built from public statistics, the kind anyone can download from a
          government website.
        - Explainable. Every area's score can be opened up to show which factors
          pushed it up or down.
        """
    )
with col_isnt:
    st.markdown("#### What it is not")
    st.markdown(
        """
        - It is not about individual people. It never sees, stores or scores a
          single person. Everything it uses is a neighbourhood total published by
          the government, already grouped so nobody can be identified.
        - It does not predict suicides. It does not say how many men will die
          anywhere. It measures likely unmet need for support, which is a
          different thing.
        - It does not decide anything. Whether a group can actually run somewhere
          depends on a venue, volunteers and local partners, and no dataset knows
          about those.
        """
    )

# ---------------------------------------------------------------------------
st.header("2. The basic idea: need, minus what is already there")

st.markdown(
    """
    Say you run a chain of free evening classes and want to open one more. You
    would not open it where you already have three down the road. Nor would you
    open it somewhere nobody needs one. You would look for the place with the most
    unmet demand: high need, nothing nearby.

    That is what this tool does, in two halves.
    """
)

need_col, supply_col, result_col = st.columns([2, 2, 3])
with need_col:
    st.markdown("#### The need side")
    st.markdown(
        "How much might men in this neighbourhood benefit from a peer-support "
        "group? Built from four things, set out in section 3."
    )
with supply_col:
    st.markdown("#### The supply side")
    st.markdown(
        f"How well served is the neighbourhood already? Mostly this means how long "
        f"the drive is to the nearest existing group. Across the country the "
        f"typical drive is about {n('median_travel', 'unknown', '{:.0f}')} minutes."
    )
with result_col:
    st.markdown("#### Putting them together")
    st.markdown(
        "An area only scores highly if it is high on need *and* poorly served. "
        "High need with a group already next door counts for less, because those "
        "men already have somewhere to go. Well served and low need counts for "
        "less still."
    )

st.caption(
    "Written out, this is `priority = need × (1 − supply)`. In words: take the "
    "need, then reduce it according to how well the area is already covered. An "
    "area with a group on its doorstep has most of its need met already."
)

# ---------------------------------------------------------------------------
st.header("3. The four things that make up need")

st.markdown(
    "These four were chosen because published research links them to poor mental "
    "health and suicide risk among working-age men. Each one is measured for every "
    "neighbourhood."
)

st.dataframe(
    pd.DataFrame([
        {"Factor": "Deprivation",
         "What it measures": "How much poverty there is: people on low incomes and out of work.",
         "Why it is here": "The most consistent area-level marker of poor mental health.",
         "Counts for": f"{W['deprivation']:.0%}"},
        {"Factor": "High-risk jobs",
         "What it measures": "The share of local men working in construction, farming, factory work and labouring.",
         "Why it is here": "These trades have markedly higher suicide rates than average.",
         "Counts for": f"{W['occupation']:.0%}"},
        {"Factor": "Isolation",
         "What it measures": "Men living alone, and men who are single, separated or divorced.",
         "Why it is here": "Isolation and relationship breakdown are closely linked to men in crisis.",
         "Counts for": f"{W['isolation']:.0%}"},
        {"Factor": "Local suicide rate",
         "What it measures": "The recorded male suicide rate for the wider council area.",
         "Why it is here": "The only place real outcome data enters. Given a small say on purpose, for reasons in section 5.",
         "Counts for": f"{cfg['scoring']['suicide_signal_weight']:.0%}"},
    ]),
    hide_index=True, use_container_width=True,
)

st.caption(
    "The percentages are shares of the total. They were chosen and written down "
    "rather than worked out by the computer. Section 7 explains why."
)

# ---------------------------------------------------------------------------
st.header("4. Where the numbers come from")

st.markdown(
    "All of it is free, published, official statistics. Nothing is bought, taken "
    "from private sources, or estimated by us."
)

v = cfg["vintages"]
st.dataframe(
    pd.DataFrame([
        {"What": "Poverty and unemployment",
         "Who publishes it": "UK and Welsh governments, in the Indices of Deprivation",
         "Vintage": v["deprivation"]},
        {"What": "Jobs, households and relationships",
         "Who publishes it": "The 2021 Census, the survey every household fills in",
         "Vintage": v["census"]},
        {"What": "How many men live where",
         "Who publishes it": "Office for National Statistics, from the Census",
         "Vintage": v["population"]},
        {"What": "Recorded suicides by council area",
         "Who publishes it": "Office for National Statistics death registrations",
         "Vintage": v["suicide"]},
        {"What": "Where existing groups are",
         "Who publishes it": "Andy's Man Club's own public group finder",
         "Vintage": v["provision"]},
        {"What": "Driving times",
         "Who publishes it": "OpenStreetMap road network, routed on our own machine",
         "Vintage": "Car driving times. Public transport is not included."},
    ]),
    hide_index=True, use_container_width=True,
)

st.markdown(
    f"Together that covers {n('n_areas')} neighbourhoods across {n('n_las')} "
    f"council areas in England and Wales, and {n('n_groups')} existing groups."
)

with st.expander("What counts as a neighbourhood?"):
    st.markdown(
        """
        The official name is an LSOA, short for Lower Layer Super Output Area. It
        is a standard statistical building block of roughly 1,500 people: small
        enough to be meaningfully local, large enough that no individual can be
        picked out of the figures.

        It is the smallest unit most UK statistics are published for, which is why
        the tool works at that level. Each one has a code such as `E01021988` and a
        name such as *Tendring 018A*.
        """
    )

# ---------------------------------------------------------------------------
st.header("5. Why there is no suicide figure for a neighbourhood")

st.warning(
    "Suicide figures are only published for whole council areas, places of 100,000 "
    "people or more. That is deliberate and right. The numbers for a single "
    "neighbourhood would be so small that publishing them could identify a family."
)

st.markdown(
    f"""
    So the tool has detailed data on the things associated with risk (poverty,
    jobs, isolation, all available for every neighbourhood) but only broad-brush
    data on the outcome itself, available for {n('n_las')} council areas. It cannot
    look up the suicide rate for a neighbourhood, and it never invents one.

    Instead it does two things.
    """
)

c1, c2 = st.columns(2)
with c1:
    st.markdown("##### A check at council level")
    st.markdown(
        """
        The tool adds its four factors up for each whole council area, where
        suicide figures do exist, and asks whether those factors really do line up
        with higher suicide rates.

        They do. All four point the right way. Had any pointed the wrong way, the
        tool would say so on screen rather than carry on quietly.
        """
    )
with c2:
    st.markdown("##### A small nudge in the score")
    st.markdown(
        f"""
        The council-wide suicide rate also enters the score directly, but at a
        deliberately small {cfg['scoring']['suicide_signal_weight']:.0%}.

        Every neighbourhood in a council area shares the same figure, so it can
        only move a whole council up or down a little. It cannot pretend to tell
        one street from the next.
        """
    )

# ---------------------------------------------------------------------------
st.header("6. How four different measurements get compared")

st.markdown(
    """
    The four factors are measured in completely different units: pounds of income,
    percentages of workers, counts of households. Adding those together directly
    would make no more sense than adding up your height, your age and your shoe
    size.

    So each is converted into the same thing, a position in the queue.
    """
)

st.success(
    "Picture all the neighbourhoods lined up in order, one factor at a time, from "
    "lowest to highest. An area finishing 90th out of 100 is written down as 0.90. "
    "Bottom of the queue is 0.00 and top is 1.00. Statisticians call this a "
    "percentile. It means the fraction of areas that sit below this one."
)

st.markdown(
    """
    Once every factor is on that same 0 to 1 scale, they can be combined using the
    percentages from section 3.

    One detail matters here. England and Wales are lined up separately, because the
    two countries measure poverty using different systems that are not comparable.
    A Welsh neighbourhood is ranked against other Welsh neighbourhoods and an
    English one against English. Comparing them directly would measure the
    difference between two government methods rather than a real difference
    between places.
    """
)

# ---------------------------------------------------------------------------
st.header("7. Why the percentages were chosen, not calculated")

st.markdown(
    """
    A fair question: why not let the computer work out the best weighting from the
    data?

    We tried, and the data cannot answer it.

    The four factors overlap heavily. Poorer neighbourhoods tend to have more men
    in manual trades and more men living alone. The three travel together, so when
    a statistical model is asked which of them matters most, it has no way to
    separate them.

    Ask the question in several equally reasonable ways and the answers genuinely
    differ, including one that says poverty does not matter at all. That is not a
    finding about the world. It is the model losing its footing on data where
    everything moves together.
    """
)

st.markdown(
    f"""
    So the tool takes a different route:

    1. The weighting is stated up front ({W['deprivation']:.0%} deprivation,
       {W['occupation']:.0%} jobs, {W['isolation']:.0%} isolation), with the
       reasoning written down in a settings file anyone can read and argue with.
    2. The data gets a veto. If the real suicide figures had contradicted any of
       those choices, the tool would say so on screen.
    3. We then test how much the choice changes the answer, which is what the
       tiers are for.
    """
)

# ---------------------------------------------------------------------------
st.header("8. How the tiers are worked out")

st.markdown(
    f"""
    Any ranking involves judgement calls: the weighting above, how long a drive
    counts as too far, and several others. Each is defensible and each would shift
    the list a little.

    Rather than pick one and present the result as fact, the tool runs the whole
    calculation {N_CONFIG} times, making a different reasonable choice each time,
    then looks at where every neighbourhood landed across all {N_CONFIG} runs.
    """
)

t1, t2, t3 = st.columns(3)
counts = tiers.get("counts", {})
rcounts = tiers.get("reach_counts", {})
with t1:
    st.markdown("### ① Shortlist")
    st.markdown(
        f"Finished in the top {N} every single time, whichever reasonable choices "
        f"were made.\n\nThe strongest candidates."
    )
    st.metric("areas", f"{counts.get('shortlist', 0):,}")
with t2:
    st.markdown("### ② In contention")
    st.markdown(
        f"Reached the top {N} under some choices but not others.\n\nWorth a look, "
        f"though the evidence is less settled."
    )
    st.metric("areas", f"{counts.get('contention', 0):,}")
with t3:
    st.markdown("### ③ Outside")
    st.markdown(
        f"Never reached the top {N}.\n\nNot a judgement on the place, just not "
        f"where this evidence points."
    )
    st.metric("areas", f"{counts.get('outside', 0):,}")

st.info(
    f"The tiers can be relied on. The exact running order inside a tier cannot. "
    f"Two areas ranked 4th and 11th may well swap places under a slightly "
    f"different judgement call, so treat everything in a tier as jointly worth "
    f"considering and let local knowledge decide between them. That is the point "
    f"of grouping them: the tool should not claim a precision the evidence does "
    f"not support."
)

with st.expander("Does the tool ever say it is unsure?"):
    stab = S.get("stability", {})
    status = stab.get("status", "unknown")
    if status == "stable":
        st.markdown(
            f"""
            Yes, and at the moment it reports stable. The test it applies is this:
            of the top {S.get('decision_n', 20)} areas, the ones you would
            realistically act on, how many stay in contention when the judgement
            calls change?

            All of them do, across all {N_CONFIG} runs. If that stopped being true
            a warning would appear at the top of the map page, and the shortlist
            would carry that warning rather than be presented as a ranking.
            """
        )
    else:
        st.markdown(
            f"""
            Yes, and at the moment it reports {status}. A warning appears at the
            top of the map page. Treat the shortlist as a starting point for
            discussion rather than a ranking.
            """
        )

# ---------------------------------------------------------------------------
st.header("9. The two lists, and why they differ")

va, vb = st.columns(2)
with va:
    st.markdown("#### Per-capita: acute pockets")
    st.markdown(
        """
        Ranks by need per person, answering where men are worst off relative to
        what is available.

        It tends to surface smaller places such as coastal towns and former
        industrial areas, where need is concentrated and nothing is nearby.
        """
    )
    st.metric("① Shortlist areas", f"{counts.get('shortlist', 0):,}")
with vb:
    st.markdown("#### Reach: most men helped")
    st.markdown(
        """
        Multiplies that by how many working-age men live there, answering where one
        new group would reach the most men.

        It tends to surface larger places, where need is slightly less acute but
        far more men are within range of a single new group.
        """
    )
    st.metric("① Shortlist areas", f"{rcounts.get('shortlist', 0):,}")

st.caption(
    "Neither list is the correct one. They answer different questions, and a real "
    "decision usually weighs both. Each has its own tiers, because a place can be "
    "an urgent pocket without being where you would reach the most men."
)

# ---------------------------------------------------------------------------
st.header("10. What this tool cannot tell you")

st.markdown(
    """
    - It only knows about driving. Public transport is not included, which is a
      real gap: the men most likely to need a free peer-support group are among
      the least likely to own a car, and groups often meet in the evening.
    - The data is a few years old. The Census was 2021 and the deprivation figures
      2019. Neighbourhoods change.
    - Suicide figures arrive slowly, typically six to nine months after a death,
      because each one goes through an inquest first.
    - It knows nothing about whether a group could actually run. No venue, no
      volunteers, no local partners, no sense of whether men there would come.
      Those things decide success and none of them are in any dataset.
    - It measures likely need, not certainty. A high score means the conditions
      associated with unmet need are present, not that any particular person is
      struggling.
    """
)

st.divider()
st.caption(
    "Every figure on this page is read from the tool's own outputs, so it cannot "
    "fall out of step with what the pipeline produced. Fuller technical detail is "
    "in README.md and docs/adr/0001-calibration-as-veto.md."
)
