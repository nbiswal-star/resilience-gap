"""Interactive dashboard over the serving tables.

Run:  make dashboard   (installs the optional `dashboard` extra and starts Streamlit)
"""

import os

import pandas as pd
import psycopg
import streamlit as st

from resgap.config import load_settings

st.set_page_config(page_title="Resilience Gap", layout="wide")
settings = load_settings()


@st.cache_data(ttl=300)
def query(sql: str) -> pd.DataFrame:
    with psycopg.connect(os.environ.get("RESGAP_DATABASE_URL", settings.database_url)) as conn:
        cur = conn.execute(sql)
        return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


profile = query("select * from ops.active_profile")
counties = query("select * from serving.county_resilience_profile")
# national quartiles: 50 states + DC only (other universes are never pooled into them)
quartiles = query(
    "select * from serving.svi_quartile_summary "
    "where analysis_universe = 'states_dc' and svi_quartile is not null"
)
coverage_by_region = query(
    """
    with cov as (
      select fema_region, count(*) as event_county_pairs,
             avg(covered_by_major_disaster::int) as coverage_rate
      from serving.event_declaration_coverage group by 1
    ),
    req as (
      select fema_region, sum(declared) as dr_declared, sum(turned_down) as dr_turned_down
      from serving.declaration_request_outcomes where declaration_type = 'DR' group by 1
    )
    select fema_region, coalesce(event_county_pairs, 0) as event_county_pairs, coverage_rate,
           coalesce(dr_declared, 0) as dr_declared, coalesce(dr_turned_down, 0) as dr_turned_down
    from cov full join req using (fema_region) order by fema_region
    """
)
coverage_by_state = query(
    """
    with cov as (
      select state_abbr, count(*) as event_county_pairs,
             count(*) filter (where covered_by_major_disaster) as covered_pairs,
             sum(damage_total_real_usd) as damage_real_usd
      from serving.event_declaration_coverage group by 1
    ),
    req as (
      select state_abbr, sum(declared) as dr_declared, sum(turned_down) as dr_turned_down
      from serving.declaration_request_outcomes where declaration_type = 'DR' group by 1
    )
    select state_abbr, coalesce(event_county_pairs, 0) as event_county_pairs,
           coalesce(covered_pairs, 0) as covered_pairs,
           covered_pairs::numeric / nullif(event_county_pairs, 0) as coverage_rate,
           damage_real_usd, coalesce(dr_declared, 0) as dr_declared,
           coalesce(dr_turned_down, 0) as dr_turned_down
    from cov full join req using (state_abbr) order by state_abbr
    """
)
timeliness = query(
    "select * from serving.declaration_timeliness_summary order by breakdown, sort_key, "
    "disasters desc, group_label"
)
# Q3 stage by period and SVI group, every analysis universe (kept separate, never pooled)
stage_detail = query(
    """
    select analysis_universe, svi_quartile_label, svi_quartile, period_scope, period_start_fy,
           period_label, fiscal_years_in_period, counties, counties_geography_not_comparable,
           counties_hma_comparable, counties_not_observed,
           counties_observed_no_positive_obligation, counties_obligated,
           share_counties_observed, share_counties_obligated, obligated_real_usd,
           obligated_real_per_capita
    from serving.hma_stage_summary
    order by analysis_universe, period_scope, period_start_fy nulls first,
             svi_quartile nulls last
    """
)
burden = query(
    "select * from serving.hma_damage_burden_summary "
    "order by analysis_universe, damage_burden_quartile"
)
status_mix = query(
    """
    select coalesce(status, '(status not recorded)') as status_verbatim,
           sum(subapplications) as subapplications,
           sum(positive_obligation_subapplications) as with_positive_obligation,
           sum(obligated_real_usd) as recorded_obligation_real_usd
    from serving.hma_status_summary group by 1 order by 2 desc, 1
    """
)
hma_money = query(
    """
    select (select coalesce(sum(obligated_real_usd), 0) from serving.hma_status_summary)
             as positive_real,
           (select coalesce(sum(county_attributed_obligated_real_usd), 0)
            from serving.hma_status_summary) as county_real,
           (select count(distinct hma_id) filter (where hazard_family = 'Not specified')
            from curated.fact_hma_allocation) as no_hazard_subs,
           (select count(distinct hma_id) filter (where hazard_family = 'Not specified'
                                                  and county_fips is not null)
            from curated.fact_hma_allocation) as no_hazard_county_subs,
           (select coalesce(sum(obligated_real_usd), 0) from curated.fact_hma_allocation
            where hazard_family = 'Not specified' and county_fips is not null
              and has_positive_obligation) as no_hazard_county_real
    """
).iloc[0]
program_hazard = query("select * from serving.hma_county_program_hazard_activity")

STAGES = {
    "geography_not_comparable": "Geography not comparable (CT planning region)",
    "not_observed": "No observed county-mapped subapplication",
    "observed_no_positive_obligation": "Observed, no recorded positive obligation",
    "obligated": "Recorded positive obligation",
}
UNIVERSES = {
    "states_dc": "States + DC (national SVI quartiles)",
    "puerto_rico": "Puerto Rico (SVI ranked within PR)",
    "island_areas": "Island areas (no SVI)",
    "legacy_geography": "Legacy geography (pre-2022 CT counties)",
}
CENTRAL_LIMITATION = (
    "HMA records are subapplications that reached FEMA. A county with no observed county-mapped "
    "subapplication may still have tried to apply (state screening, local capacity, statewide or "
    "unparsed entries), and a zero recorded obligation can reflect a pending, withdrawn or "
    "unsuccessful subapplication or a true zero. Stages show where activity is or is not "
    "observed; they never show that a county did not apply, why its obligation is low, or that "
    "it was neglected. Recorded obligation is not money received by a county government."
)
GEOGRAPHY_BREAK = (
    "Connecticut geography break: Census Vintage 2025 and SVI 2022 use the 9 planning regions "
    "created in 2022, so the regions keep their SVI and population as current social geography. "
    "NOAA Storm Events, OpenFEMA disaster declarations and OpenFEMA HMA record 2000-2025 "
    "Connecticut history on the 8 legacy counties, and nothing is crosswalked or allocated across "
    "the break (no defensible crosswalk). The planning regions are therefore excluded from the "
    "2000-2025 storm, declaration, declaration-coverage, damage-rank and HMA comparisons: their "
    "storm and declaration columns are blank (not comparable), never zero; they are in no damage "
    "rank or quartile, no SVI-quartile damage or coverage rate and not in the follow-up screen; "
    "their HMA stage is 'geography not comparable'. The legacy counties' storm and declaration "
    "records are real pre-change history and stay in national historical event totals. Only the "
    "current-geography HMA headline shares leave the legacy counties out."
)


def money_b(v: float) -> str:
    return f"${float(v) / 1e9:,.3f}B"


st.title("Resilience Gap")
p = profile.iloc[0]
base = int(p["cpi_base_year"])
st.caption(
    f"Profile {p['profile']}: {p['start_year']}-{p['end_year']}, "
    f"{', '.join(p['states']) if p['states'] else 'all states'}. Dollar figures in constant "
    f"{base} dollars (BLS CPI-U); nominal values in the table. Sources: NOAA NCEI and NWS, FEMA "
    "OpenFEMA, CDC/ATSDR SVI 2022, U.S. Census Bureau, U.S. Bureau of Labor Statistics."
)

states = sorted(counties["state_abbr"].dropna().unique())
chosen = st.multiselect("States", states, default=states)
view = counties[counties["state_abbr"].isin(chosen)]
current = view[view["analysis_universe"] != "legacy_geography"]
legacy = view[view["analysis_universe"] == "legacy_geography"]
# HMA headline denominator: HMA-comparable counties of states + DC and Puerto Rico
hma_base = view[
    view["analysis_universe"].isin(["states_dc", "puerto_rico"])
    & view["hma_geography_comparable"].astype(bool)
]
n_hma = max(len(hma_base), 1)
observed = hma_base["hma_activity_stage"].isin(["observed_no_positive_obligation", "obligated"])
obligated = hma_base["hma_activity_stage"] == "obligated"

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(
    "Current counties / municipios",
    f"{len(current):,}",
    help=f"{len(legacy):,} legacy Connecticut counties are reported separately.",
)
history_nc = view[~view["storm_declaration_geography_comparable"].astype(bool)]
history_help = (
    f"Includes the storm and declaration records of {len(legacy):,} legacy Connecticut counties "
    "(real pre-change history)"
    + (
        f"; leaves out {len(history_nc):,} Connecticut planning regions, whose 2000-2025 history "
        "is recorded on the legacy counties and is not comparable (blank, not zero)"
        if len(history_nc)
        else ""
    )
    + "."
)


def num(col: str) -> pd.Series:
    """Numeric view of a column; NULL (not comparable) stays NaN and is skipped by sums."""
    return pd.to_numeric(view[col], errors="coerce")


c2.metric(
    f"Recorded damage ({base} $)",
    f"${num('damage_total_real_usd').sum() / 1e6:,.0f}M",
    help=history_help,
)
sig = num("significant_events").sum()
covered = num("significant_events_covered").sum() / max(sig, 1)
c3.metric(
    "Significant event-county pairs with declaration coverage",
    f"{covered:.1%}",
    help=history_help,
)
c4.metric(
    "HMA-comparable counties: observed · recorded positive obligation",
    f"{observed.sum() / n_hma:.1%} · {obligated.sum() / n_hma:.1%}",
    help=(
        f"Same denominator for both: {len(hma_base):,} HMA-comparable counties and municipios of "
        "the states, DC and Puerto Rico, all profile fiscal years. Island areas, the Connecticut "
        "planning regions (HMA geography not comparable) and the legacy Connecticut counties are "
        "outside this denominator; only these HMA shares leave the legacy counties out. They "
        "are close because over many years almost every county "
        "named in a subapplication is also named in one with a positive recorded obligation: "
        f"{int(observed.sum()):,} observed, {int(obligated.sum()):,} with a positive obligation, "
        f"{int(observed.sum() - obligated.sum()):,} observed without one. The period table below "
        "shows where they diverge."
    ),
)
c5.metric(
    "High-need, low-obligation counties (follow-up screen)",
    int(view["is_high_need_low_obligation"].sum()),
)
st.info(f"Central limitation: {CENTRAL_LIMITATION}")
st.caption(GEOGRAPHY_BREAK)

left, right = st.columns(2)
with left:
    st.subheader(f"Damage per resident ({base} $) by national SVI quartile")
    st.bar_chart(
        quartiles.set_index("svi_quartile_label")["damage_per_capita_real"].astype(float),
        horizontal=True,
    )
with right:
    st.subheader(f"Recorded HMA obligation per resident ({base} $) by national SVI quartile")
    st.bar_chart(
        quartiles.set_index("svi_quartile_label")["hma_per_capita_real"].astype(float),
        horizontal=True,
    )
positive_real = float(hma_money["positive_real"])
county_real = float(hma_money["county_real"])
st.caption(
    "National SVI quartiles cover the 50 states + DC. CDC/ATSDR ranks Puerto Rico only within "
    "Puerto Rico and publishes no SVI for the island areas; those rows keep their own "
    "analysis_universe in the tables below. Damage per resident = damage of the counties with "
    "comparable storm history / their population (history_comparable_population); the CT "
    "planning regions are in neither. Recorded obligation = county shares of positive "
    "obligated amounts of county-mapped subapplications, per resident of HMA-comparable "
    "counties; not money received by a county government and not all disaster aid. "
    f"Positive recorded obligation in scope: {money_b(positive_real)} ({base} $), of which "
    f"{money_b(county_real)} attributed to counties and {money_b(positive_real - county_real)} "
    f"({(positive_real - county_real) / positive_real if positive_real else 0:.2%}) at state "
    "level. Weather-related obligation is a lower bound: "
    f"{int(hma_money['no_hazard_subs']):,} subapplications have no primaryHazard and cannot be "
    f"classified ({int(hma_money['no_hazard_county_subs']):,} of them county-attributed, "
    f"{money_b(hma_money['no_hazard_county_real'])} of positive obligation in county shares). "
    "National view."
)

st.subheader("Q3. HMA stage by national SVI quartile (all profile fiscal years)")
q_left, q_right = st.columns(2)
with q_left:
    st.caption("Share of HMA-comparable counties with an observed county-mapped subapplication")
    st.bar_chart(
        quartiles.set_index("svi_quartile_label")["share_counties_hma_observed"].astype(float),
        horizontal=True,
    )
with q_right:
    st.caption("Share of HMA-comparable counties with a recorded positive obligation")
    st.bar_chart(
        quartiles.set_index("svi_quartile_label")["share_counties_hma_obligated"].astype(float),
        horizontal=True,
    )
st.dataframe(
    quartiles[
        [
            "svi_quartile_label",
            "counties",
            "counties_hma_geography_not_comparable",
            "counties_hma_comparable",
            "counties_hma_not_observed",
            "counties_hma_observed_no_positive_obligation",
            "counties_hma_obligated",
            "hma_observed_subapplications",
            "hma_positive_obligation_subapplications",
            "hma_obligated_real_usd",
            "hma_weather_obligated_real_usd",
        ]
    ],
    hide_index=True,
)


def stage_text(frame: pd.DataFrame) -> str:
    counts = (
        frame["hma_activity_stage"]
        .map(STAGES)
        .value_counts()
        .reindex(list(STAGES.values()), fill_value=0)
    )
    return "; ".join(f"{label}: {n:,}" for label, n in counts.items() if n)


stage_caption = f"Selected states, current geography: {stage_text(current) or 'none'}."
if len(legacy):
    stage_caption += (
        f" Legacy CT counties (separate universe, not in the shares): {stage_text(legacy)}."
    )
st.caption(
    stage_caption + " Stages describe county-mapped subapplications that reached FEMA; 'no "
    "observed county-mapped subapplication' is not 'did not apply'."
)

st.subheader("Q3. HMA stage by five-year fiscal-year period and SVI group")
universe = st.selectbox(
    "Analysis universe",
    [u for u in UNIVERSES if u in set(stage_detail["analysis_universe"])],
    format_func=lambda u: UNIVERSES[u],
)
sv = stage_detail[stage_detail["analysis_universe"] == universe]
measure = st.radio(
    "Measure",
    ["share_counties_obligated", "share_counties_observed", "obligated_real_per_capita"],
    format_func=lambda m: {
        "share_counties_obligated": "Share with a recorded positive obligation",
        "share_counties_observed": "Share with an observed county-mapped subapplication",
        "obligated_real_per_capita": f"Recorded obligation per resident ({base} $)",
    }[m],
    horizontal=True,
)
st.dataframe(
    sv.pivot_table(
        index=["period_label"],
        columns="svi_quartile_label",
        values=measure,
        aggfunc="first",
        sort=False,
    ).astype(float),
)
st.dataframe(sv.drop(columns=["analysis_universe", "svi_quartile"]), hide_index=True)
st.caption(
    "Rows = SVI group within the chosen analysis universe; each universe is shown on its own and "
    "never pooled. Shares use HMA-comparable counties (CT planning regions excluded). Fixed "
    "periods FY2001-2005 ... FY2021-2025 (FY2000 is a one-year period), clipped to the profile; "
    "compare only periods with the same number of fiscal years. National view; the state filter "
    "does not apply here."
)

st.subheader("Q3. Recorded HMA obligation by storm-damage burden")
bu = burden[burden["analysis_universe"] == "states_dc"]
if len(bu):
    st.bar_chart(
        bu.set_index("damage_burden_label")["share_counties_obligated"].astype(float),
        horizontal=True,
    )
st.dataframe(burden, hide_index=True)
st.caption(
    "Quartiles of constant-dollar storm damage per resident, ranked within each analysis "
    "universe (D4 = top quartile, the high-loss group of the follow-up screen); chart = states + "
    "DC. Only units with comparable storm history are ranked: the CT planning regions have no "
    "damage rank and no row here. HMA-comparable counties only "
    "(counties_excluded_geography_not_comparable counts ranked units whose HMA geography is not "
    "comparable). Counties with no recorded damage tie in D1. Descriptive alignment, not a "
    "cause. National view."
)

pv = program_hazard[program_hazard["state_abbr"].isin(chosen)]
p_left, p_right = st.columns(2)
with p_left:
    st.subheader("By program (observed activity only)")
    st.dataframe(
        pv.groupby("program", dropna=False)
        .agg(
            counties_observed=("county_fips", "nunique"),
            county_subapplication_links=("observed_subapplications", "sum"),
            recorded_obligation_real_usd=("obligated_real_usd", "sum"),
        )
        .reset_index()
        .sort_values("counties_observed", ascending=False),
        hide_index=True,
    )
with p_right:
    st.subheader("By hazard family (observed activity only)")
    st.dataframe(
        pv.groupby("hazard_family", dropna=False)
        .agg(
            counties_observed=("county_fips", "nunique"),
            county_subapplication_links=("observed_subapplications", "sum"),
            recorded_obligation_real_usd=("obligated_real_usd", "sum"),
        )
        .reset_index()
        .sort_values("counties_observed", ascending=False),
        hide_index=True,
    )
st.caption(
    "Rows exist only where activity is observed. A county absent from a program or hazard row "
    "is not a county that did not apply: programs existed in different years and each "
    "subapplication records one primary hazard ('Not specified' = no primaryHazard, which is why "
    "weather-related totals are a lower bound)."
)

st.subheader("Q3. Recorded status mix (OpenFEMA status, verbatim)")
st.dataframe(status_mix, hide_index=True)
st.caption(
    "All subapplications in profile scope, county-mapped or not. Status is a snapshot, not a "
    "final outcome; status and recorded obligation are separate source fields. National view."
)

st.subheader("Q3. High-need, low-obligation follow-up screen")
follow = view[view["is_high_need_low_obligation"].astype(bool)].sort_values(
    "damage_per_capita_real", ascending=False
)
st.caption(
    f"All {len(follow):,} flagged counties in the selected states (of "
    f"{int(counties['is_high_need_low_obligation'].sum()):,} nationally in this profile). States "
    "+ DC, counties with comparable storm history and HMA geography only (not the CT planning "
    "regions): top-quartile constant-dollar damage per resident, SVI "
    "quartile 4, recorded HMA obligation per resident at or below the median of HMA-comparable "
    "states + DC counties. Puerto Rico's is_low_obligation uses Puerto Rico's own median, but "
    "Puerto Rico has no national SVI rank and is not screened. Not evidence of neglect or "
    "non-application."
)
st.dataframe(
    follow[
        [
            "county_name",
            "state_abbr",
            "population_base",
            "damage_per_capita_real",
            "svi_overall",
            "hma_activity_stage",
            "hma_observed_subapplications",
            "hma_positive_obligation_subapplications",
            "hma_per_capita_real",
            "hma_per_capita_real_median",
        ]
    ],
    hide_index=True,
)

st.subheader("Q2. Coverage by state, and Major Disaster requests turned down")
st.dataframe(
    coverage_by_state[coverage_by_state["state_abbr"].isin(chosen)],
    hide_index=True,
)
st.subheader("Q2. Coverage by FEMA region, and Major Disaster requests turned down")
st.dataframe(coverage_by_region, hide_index=True)
st.caption(
    "Coverage counts significant event-county pairs (an event touching three counties counts "
    "three times); significance uses constant-dollar damage. A pair is covered only by a DR that "
    "designates its county or is a literal statewide designation, with a compatible hazard "
    "family; tribal-area designations never count as county coverage. Coverage is designation "
    "(eligibility), not money. Turned-down requests come from OpenFEMA DeclarationDenials "
    "(current status 'Turndown'): government-level outcomes of presidential declaration "
    "requests, with no county, not HMA decisions and not a measure of damage. The state table "
    "follows the state filter; the region table is a national view."
)

st.subheader("Q4. Days from incident start to the declaration request and to the declaration")
breakdown = st.radio(
    "Group by",
    ["svi_group", "hazard_family", "fema_region"],
    format_func=lambda b: {
        "svi_group": "SVI group",
        "hazard_family": "Hazard family",
        "fema_region": "FEMA region",
    }[b],
    horizontal=True,
)
tview = timeliness[timeliness["breakdown"] == breakdown]
lag_left, lag_right = st.columns(2)
with lag_left:
    st.caption("Median days, incident start to declaration request")
    st.bar_chart(
        tview.set_index("group_label")["median_days_incident_to_request"].astype(float),
        horizontal=True,
    )
with lag_right:
    st.caption("Median days, incident start to declaration")
    st.bar_chart(
        tview.set_index("group_label")["median_days_incident_to_declaration"].astype(float),
        horizontal=True,
    )
st.dataframe(
    tview[
        [
            "group_label",
            "disasters",
            "disasters_with_request_date",
            "median_days_incident_to_request",
            "p25_days_incident_to_declaration",
            "median_days_incident_to_declaration",
            "p75_days_incident_to_declaration",
            "median_days_request_to_declaration",
            "top_hazard_family",
            "top_hazard_share",
            "turned_down",
            "sample_note",
        ]
    ],
    hide_index=True,
)
st.caption(
    "The two lags reported for every group are incident start to request and incident start to "
    "declaration; request to declaration is an extra column. Descriptive one-way breakdowns, "
    "not adjusted for hazard, region or year: groups differ in hazard mix (see the most common "
    "hazard and its share), so differences are not effects of vulnerability. Groups with fewer "
    "than 10 disasters are not comparable. National view."
)

st.subheader("Counties")
cols = [
    "county_name",
    "state_abbr",
    "analysis_universe",
    "population_base",
    "svi_overall",
    "svi_pr_within_rank",
    "damage_total_usd",
    "damage_total_real_usd",
    "damage_per_capita_real",
    "significant_events",
    "coverage_rate",
    "damage_pctile",
    "storm_declaration_geography_comparable",
    "hma_geography_comparable",
    "hma_activity_stage",
    "hma_observed_subapplications",
    "hma_positive_obligation_subapplications",
    "hma_obligated_real_usd",
    "hma_weather_obligated_real_usd",
    "hma_per_capita_real",
    "is_low_obligation",
    "is_high_need_low_obligation",
]
st.dataframe(
    view[cols].sort_values(
        "damage_total_real_usd",
        ascending=False,
        na_position="last",
        key=lambda s: pd.to_numeric(s, errors="coerce"),
    ),
    hide_index=True,
)
st.caption(
    "is_low_obligation compares each county with the median of its own analysis universe "
    "(Puerto Rico against Puerto Rico). is_high_need_low_obligation is a follow-up screen "
    "(states + DC, counties with comparable storm history and HMA geography: top-quartile "
    "constant-dollar damage per resident, SVI quartile 4, recorded HMA obligation per resident "
    "at or below the median), not evidence of neglect or non-application. "
    "storm_declaration_geography_comparable = false and hma_geography_comparable = false mark "
    "the CT planning regions: their storm, damage, coverage and declaration columns are blank "
    "(not comparable, not zero) and sorted last; SVI and population are current."
)
st.caption(
    "This product uses the Federal Emergency Management Agency's OpenFEMA API, but is not "
    "endorsed by FEMA. The Federal Government or FEMA cannot vouch for the data or analyses "
    "derived from these data after the data have been retrieved from the Agency's website(s). "
    "BLS.gov cannot vouch for the data or analyses derived from these data after the data have "
    "been retrieved from BLS.gov."
)
