"""Static HTML findings report built from serving/ops tables (plus the curated HMA fact for two
scope notes: unclassifiable hazards and records outside the state-year table).

Charts are hand-built inline SVG (no plotting dependency): one measure per chart, one colour
for a single series, values labelled directly, native hover tooltips, a data table under every
chart, and light/dark colours from CSS variables.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

BLS_STATEMENT = (
    "BLS.gov cannot vouch for the data or analyses derived from these data after the data have "
    "been retrieved from BLS.gov."
)
ATTRIBUTION = (
    "This product uses the Federal Emergency Management Agency's OpenFEMA API, but is not "
    "endorsed by FEMA. The Federal Government or FEMA cannot vouch for the data or analyses "
    "derived from these data after the data have been retrieved from the Agency's website(s)."
)
# The central limitation of Q3, shown near the top of the report and again in the Q3 section.
CENTRAL_LIMITATION = (
    "HMA records are subapplications that reached FEMA. A county with no observed county-mapped "
    "subapplication may still have tried to apply: proposals screened out or deprioritized by "
    "the state, limited by local capacity (staff, cost share, benefit-cost analysis), covered "
    "only by a statewide or unparsed benefiting area, or simply absent from the data are all "
    "invisible. A zero recorded obligation can also reflect a pending, withdrawn or unsuccessful "
    "subapplication, or a true zero. The data cannot separate these cases, so this report shows "
    "the stage at which activity is or is not observed and never infers that a county did not "
    "apply, why its obligation is low, or that it was neglected. Recorded obligation is FEMA's "
    "obligated amount, split equally across a subapplication's benefiting areas; it is not money "
    "received by a county government and not all disaster aid."
)
STAGE_LABELS = {
    "geography_not_comparable": "Geography not comparable (CT planning region)",
    "not_observed": "No observed county-mapped subapplication",
    "observed_no_positive_obligation": "Observed, no recorded positive obligation",
    "obligated": "Recorded positive obligation",
}
# The 2022 Connecticut geography break, shown with the headline and wherever it matters.
GEOGRAPHY_BREAK = (
    "Connecticut replaced its 8 counties with 9 planning regions in 2022. Census Vintage 2025 and "
    "CDC/ATSDR SVI 2022 use the planning regions, so the regions keep their SVI and population "
    "and stay visible as current social geography. The 2000–2025 sources use the old counties: "
    "NOAA Storm Events, OpenFEMA disaster declarations and OpenFEMA HMA subapplications all "
    "record Connecticut on the 8 legacy counties. No defensible crosswalk from legacy counties "
    "to planning regions exists, so nothing is crosswalked or allocated across the break: the "
    "planning regions are excluded from the 2000–2025 storm, declaration, declaration-coverage, "
    "damage-rank and HMA comparisons. Their storm and declaration measures are shown as not "
    "comparable (blank), never as zero; they are outside every damage rank and quartile, the "
    "SVI-quartile damage and coverage rates and the follow-up screen; and their HMA stage is "
    "“geography not comparable”. The legacy counties' storm and declaration records are real "
    "pre-change history and stay in national historical event totals; they also keep their HMA "
    "records as a separate legacy-geography universe. Only the current-geography HMA headline "
    "shares leave the legacy counties out."
)
UNIVERSE_LABELS = {
    "states_dc": "States + DC",
    "puerto_rico": "Puerto Rico",
    "island_areas": "Island areas",
    "legacy_geography": "Legacy geography (pre-2022 CT counties)",
}

CSS = """
.viz-root{color-scheme:light;--surface-1:#fcfcfb;--surface-2:#f3f2ef;--text-primary:#0b0b0b;
--text-secondary:#52514e;--grid:#e2e1dc;--series-1:#2a78d6;--good:#0ca30c;--warn:#fab219;
--critical:#d03b3b}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{
color-scheme:dark;--surface-1:#1a1a19;--surface-2:#242422;--text-primary:#f0efec;
--text-secondary:#c3c2b7;--grid:#383835;--series-1:#3987e5}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface-1:#1a1a19;--surface-2:#242422;
--text-primary:#f0efec;--text-secondary:#c3c2b7;--grid:#383835;--series-1:#3987e5}
body.viz-root{background:var(--surface-1);color:var(--text-primary);margin:0;
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:24px}
h1{font-size:26px;margin:0 0 4px} h2{font-size:20px;margin:36px 0 8px;
border-top:1px solid var(--grid);padding-top:20px} h3{font-size:16px;margin:20px 0 6px}
.muted{color:var(--text-secondary)} .tiles{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}
.tile{background:var(--surface-2);border-radius:8px;padding:12px 16px;min-width:150px}
.tile b{display:block;font-size:22px} .tile span{color:var(--text-secondary);font-size:13px}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--grid)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
details{margin:6px 0 18px} summary{cursor:pointer;color:var(--text-secondary);font-size:13px}
svg text{fill:var(--text-primary);font-size:12px} svg .axis{fill:var(--text-secondary)}
svg .bar{fill:var(--series-1)} svg .bar:hover{opacity:.8} svg .hit{fill:transparent}
.status{font-weight:600} .pass{color:var(--good)} .fail,.error{color:var(--critical)}
.warn{color:#9a6a00} .note{background:var(--surface-2);padding:10px 14px;border-radius:8px}
"""


def _rows(conn: psycopg.Connection, sql: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql)  # type: ignore[arg-type]
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _num(v: Any) -> float | None:
    if v is None:
        return None
    return float(v) if isinstance(v, (Decimal, int, float)) else None


def money(v: Any, digits: int = 0) -> str:
    x = _num(v)
    if x is None:
        return "–"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= div:
            return f"${x / div:,.1f}{unit}"
    return f"${x:,.{digits}f}"


def money3(v: Any) -> str:
    """Like money(), with three decimals for billions (headline reconciliation figures)."""
    x = _num(v)
    if x is not None and abs(x) >= 1e9:
        return f"${x / 1e9:,.3f}B"
    return money(v)


def pct(v: Any) -> str:
    x = _num(v)
    return "–" if x is None else f"{x * 100:.1f}%"


def intf(v: Any) -> str:
    x = _num(v)
    return "–" if x is None else f"{x:,.0f}"


def esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def hbar(items: list[tuple[str, float | None, str]], fmt, title: str) -> str:
    """Horizontal bar chart. items = (label, value, tooltip). Baseline at zero, single series."""
    items = [(lab, v if v is not None else 0.0, tip) for lab, v, tip in items]
    width, label_w, value_w, bar_h, gap = 720, 190, 90, 22, 10
    plot_w = width - label_w - value_w
    vmax = max([v for _, v, _ in items] + [1e-9])
    height = len(items) * (bar_h + gap) + gap
    out = [
        f'<svg role="img" aria-label="{esc(title)}" viewBox="0 0 {width} {height}" '
        f'width="100%" style="max-width:{width}px">',
        f"<title>{esc(title)}</title>",
        f'<line x1="{label_w}" x2="{label_w}" y1="0" y2="{height}" stroke="var(--grid)"/>',
    ]
    for i, (label, v, tip) in enumerate(items):
        y = gap + i * (bar_h + gap)
        w = max(plot_w * v / vmax, 0)
        r = min(4, w)
        x0 = label_w
        # square at the baseline, 4px rounded at the data end
        path = (
            (
                f"M{x0},{y} h{max(w - r, 0):.1f} a{r},{r} 0 0 1 {r},{r} v{bar_h - 2 * r} "
                f"a{r},{r} 0 0 1 -{r},{r} h-{max(w - r, 0):.1f} z"
            )
            if w > 0
            else ""
        )
        out.append("<g>")
        out.append(f"<title>{esc(tip)}</title>")
        out.append(
            f'<rect class="hit" x="0" y="{y - gap / 2}" width="{width}" height="{bar_h + gap}"/>'
        )
        if path:
            out.append(f'<path class="bar" d="{path}"/>')
        out.append(
            f'<text x="{label_w - 8}" y="{y + bar_h / 2 + 4}" text-anchor="end">{esc(label)}</text>'
        )
        out.append(
            f'<text class="axis" x="{label_w + w + 6:.1f}" y="{y + bar_h / 2 + 4}">'
            f"{esc(fmt(v))}</text>"
        )
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)


def table(rows: list[dict[str, Any]], cols: list[tuple[str, str, Any]]) -> str:
    """cols = (key, header, formatter or None)."""
    head = "".join(f'<th class="{"n" if f else ""}">{esc(h)}</th>' for _, h, f in cols)
    body = []
    for r in rows:
        tds = "".join(
            f'<td class="n">{esc(f(r[k]))}</td>' if f else f"<td>{esc(r[k])}</td>"
            for k, _, f in cols
        )
        body.append(f"<tr>{tds}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def chart_with_table(title: str, subtitle: str, svg: str, tbl: str) -> str:
    return (
        f"<h3>{esc(title)}</h3><p class='muted'>{esc(subtitle)}</p>{svg}"
        f"<details><summary>Show data table</summary>{tbl}</details>"
    )


def build_report(conn: psycopg.Connection, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    prof = _rows(conn, "select * from ops.active_profile")[0]
    states = ", ".join(prof["states"]) if prof["states"] else "all states and territories"
    q = _rows(conn, "select * from serving.svi_quartile_summary")
    # national quartile charts: states + DC only (Puerto Rico, island areas and legacy geography
    # have no national SVI rank and are reported separately below)
    q_svi = [
        r for r in q if r["svi_quartile"] is not None and r["analysis_universe"] == "states_dc"
    ]
    other_universes = [r for r in q if r["analysis_universe"] != "states_dc"]
    base = prof.get("cpi_base_year")
    real = f"{base} $"
    zone_cov = _rows(conn, "select * from serving.zone_mapping_coverage order by event_year")
    infl = _rows(conn, "select * from serving.inflation_reference order by year")
    bls_retrieved = _rows(
        conn, "select max(retrieved_at) t from ops.source_objects where source = 'bls_cpi'"
    )[0]["t"]
    # Headline counts. The current-geography HMA shares use HMA-comparable units of states + DC
    # and Puerto Rico only: island areas, the CT planning regions (geography not comparable) and
    # the legacy CT counties (separate universe) are reported separately, never in that
    # denominator. The historical storm / declaration tiles sum every unit with comparable
    # history: they include the legacy CT counties' real pre-change records and leave out the CT
    # planning regions, whose history columns are NULL (not comparable), not zero.
    totals = _rows(
        conn,
        """
        select count(*) counties,
               count(*) filter (where analysis_universe <> 'legacy_geography') current_counties,
               count(*) filter (where analysis_universe = 'legacy_geography') legacy_counties,
               count(*) filter (where hma_activity_stage = 'geography_not_comparable')
                 not_comparable,
               count(*) filter (where not storm_declaration_geography_comparable)
                 history_not_comparable,
               sum(storm_events) event_county_records, sum(damage_total_usd) damage,
               sum(damage_total_real_usd) damage_real, sum(hma_obligated_real_usd) hma_real,
               coalesce(sum(hma_obligated_real_usd)
                        filter (where analysis_universe = 'legacy_geography'), 0) legacy_hma_real,
               sum(significant_events) sig, sum(significant_events_covered) covered,
               sum(hma_obligated_usd) hma,
               count(*) filter (where is_high_need_low_obligation) follow_up,
               count(*) filter (where analysis_universe = 'states_dc' and hma_geography_comparable
                                and storm_declaration_geography_comparable
                                and population_base is not null) follow_up_universe,
               count(*) filter (where hma_activity_stage in ('observed_no_positive_obligation',
                                                             'obligated')
                                and analysis_universe in ('states_dc', 'puerto_rico')) observed,
               count(*) filter (where hma_activity_stage = 'obligated'
                                and analysis_universe in ('states_dc', 'puerto_rico')) obligated,
               count(*) filter (where hma_activity_stage = 'observed_no_positive_obligation'
                                and analysis_universe in ('states_dc', 'puerto_rico'))
                 observed_no_positive,
               count(*) filter (where hma_geography_comparable
                                and analysis_universe in ('states_dc', 'puerto_rico'))
                 hma_counties
        from serving.county_resilience_profile""",
    )[0]
    fiscal_years = _rows(
        conn, "select coalesce(sum(fiscal_years_in_period), 0) n from curated.dim_hma_period"
    )[0]["n"]
    # Positive recorded obligation (whole subapplications) and the county-attributed part; the
    # net sum (negative de-obligation records included) is shown separately and labelled.
    hma_money = _rows(
        conn,
        """
        select (select coalesce(sum(obligated_real_usd), 0) from serving.hma_status_summary)
                 positive_real,
               (select coalesce(sum(county_attributed_obligated_real_usd), 0)
                from serving.hma_status_summary) county_real,
               (select coalesce(sum(obligated_real_usd), 0) from curated.fact_hma_allocation)
                 net_real,
               (select coalesce(sum(subapplications), 0) from serving.hma_status_summary) subs""",
    )[0]
    positive_real = _num(hma_money["positive_real"]) or 0.0
    county_real = _num(hma_money["county_real"]) or 0.0
    noncounty_real = positive_real - county_real
    # weather-related totals are a lower bound: no primaryHazard -> cannot be classified
    unclassified = _rows(
        conn,
        """
        select count(distinct hma_id) filter (where hazard_family = 'Not specified') subs,
               count(distinct hma_id) all_subs,
               count(distinct hma_id) filter (where hazard_family = 'Not specified'
                                              and county_fips is not null) county_subs,
               coalesce(sum(obligated_real_usd) filter (where hazard_family = 'Not specified'
                                                        and county_fips is not null
                                                        and has_positive_obligation), 0)
                 county_real
        from curated.fact_hma_allocation""",
    )[0]
    # subapplications outside serving.state_year_summary (no applicant state, or e.g. FM)
    state_year_out = _rows(
        conn,
        """
        select count(distinct hma_id) subs,
               count(distinct hma_id) filter (where state_abbr is null) no_state,
               coalesce(sum(obligated_real_usd) filter (where has_positive_obligation), 0)
                 positive_real,
               string_agg(distinct state_abbr, ', ') other_states
        from curated.fact_hma_allocation
        where state_abbr is null
           or state_abbr not in (select state_abbr from serving.state_year_summary)""",
    )[0]
    stage_detail = _rows(
        conn,
        """
        select * from serving.hma_stage_summary
        order by case analysis_universe when 'states_dc' then 1 when 'puerto_rico' then 2
                 when 'island_areas' then 3 else 4 end,
                 period_scope, period_start_fy nulls first, svi_quartile nulls last""",
    )
    for r in stage_detail:
        r["universe_label"] = UNIVERSE_LABELS.get(r["analysis_universe"], r["analysis_universe"])
    stage_svi_period = [
        r
        for r in stage_detail
        if r["analysis_universe"] == "states_dc" and r["svi_quartile"] is not None
    ]
    stage_other_period = [r for r in stage_detail if r["analysis_universe"] != "states_dc"]
    burden = _rows(
        conn,
        """
        select * from serving.hma_damage_burden_summary
        order by case analysis_universe when 'states_dc' then 1 when 'puerto_rico' then 2
                 else 3 end, damage_burden_quartile""",
    )
    for r in burden:
        r["universe_label"] = UNIVERSE_LABELS.get(r["analysis_universe"], r["analysis_universe"])
    burden_nat = [r for r in burden if r["analysis_universe"] == "states_dc"]
    by_state = _rows(
        conn,
        """
        with cov as (
          select state_abbr, count(*) pairs,
                 count(*) filter (where covered_by_major_disaster) covered,
                 sum(damage_total_real_usd) damage_real
          from serving.event_declaration_coverage group by 1
        ),
        req as (
          select state_abbr, sum(declared) declared, sum(turned_down) turned_down
          from serving.declaration_request_outcomes where declaration_type = 'DR' group by 1
        )
        select state_abbr, coalesce(cov.pairs, 0) pairs, coalesce(cov.covered, 0) covered,
               cov.covered::numeric / nullif(cov.pairs, 0) coverage, cov.damage_real,
               coalesce(req.declared, 0) declared, coalesce(req.turned_down, 0) turned_down,
               req.turned_down::numeric / nullif(req.declared + req.turned_down, 0)
                 turndown_share
        from cov full join req using (state_abbr)
        order by state_abbr""",
    )
    by_hazard = _rows(
        conn,
        """
        select hazard_family, count(*) events, avg(covered_by_major_disaster::int) coverage,
               sum(damage_total_real_usd) damage_real
        from serving.event_declaration_coverage group by 1 order by events desc""",
    )
    by_region = _rows(
        conn,
        """
        with cov as (
          select fema_region, count(*) events, avg(covered_by_major_disaster::int) coverage
          from serving.event_declaration_coverage group by 1
        ),
        req as (
          select fema_region, sum(declared) declared, sum(turned_down) turned_down
          from serving.declaration_request_outcomes where declaration_type = 'DR' group by 1
        )
        select 'Region ' || coalesce(fema_region::text, '?') region, fema_region,
               coalesce(cov.events, 0) events, cov.coverage,
               coalesce(req.declared, 0) declared, coalesce(req.turned_down, 0) turned_down,
               req.turned_down::numeric / nullif(req.declared + req.turned_down, 0) turndown_share
        from cov full join req using (fema_region)
        order by fema_region nulls last""",
    )
    tribal = _rows(
        conn,
        """
        select count(*) areas, count(distinct disaster_number) declarations
        from curated.fact_declaration_area where area_level = 'tribal' and is_weather""",
    )[0]
    tsum = _rows(
        conn,
        """
        select * from serving.declaration_timeliness_summary
        order by breakdown, sort_key, disasters desc, group_label""",
    )
    t_all = next((r for r in tsum if r["breakdown"] == "all"), None)
    t_svi = [r for r in tsum if r["breakdown"] == "svi_group"]
    t_haz = sorted(
        (r for r in tsum if r["breakdown"] == "hazard_family"), key=lambda r: -r["disasters"]
    )
    t_reg = [r for r in tsum if r["breakdown"] == "fema_region"]
    follow_up = _rows(
        conn,
        """
        select county_name, state_abbr, population_base, damage_per_capita_real, svi_overall,
               hma_activity_stage, hma_observed_subapplications,
               hma_positive_obligation_subapplications, hma_per_capita_real,
               hma_per_capita_real_median, significant_events, significant_events_covered
        from serving.county_resilience_profile where is_high_need_low_obligation
        order by damage_per_capita_real desc, county_fips limit 20""",
    )
    for r in follow_up:
        r["stage_label"] = STAGE_LABELS.get(r["hma_activity_stage"], r["hma_activity_stage"])
    # Q3 funnel: stages of the HMA record by period (states + DC, all national quartiles summed;
    # shares over HMA-comparable counties). The per-quartile detail is stage_svi_period.
    stage_periods = _rows(
        conn,
        """
        select period_label, max(fiscal_years_in_period) fiscal_years,
               bool_or(period_scope = 'all_periods') is_all, min(period_start_fy) period_start_fy,
               sum(counties) counties, sum(counties_hma_comparable) comparable,
               sum(counties_geography_not_comparable) not_comparable,
               sum(counties_not_observed) not_observed,
               sum(counties_observed_no_positive_obligation) observed_no_positive,
               sum(counties_obligated) obligated,
               sum(counties_obligated)::numeric / nullif(sum(counties_hma_comparable), 0)
                 share_obligated,
               (sum(counties_observed_no_positive_obligation) + sum(counties_obligated))::numeric
                 / nullif(sum(counties_hma_comparable), 0) share_observed,
               sum(obligated_real_usd) obligated_real,
               sum(weather_obligated_real_usd) weather_obligated_real
        from serving.hma_stage_summary
        where analysis_universe = 'states_dc' and svi_quartile is not null
        group by period_label
        order by bool_or(period_scope = 'all_periods') desc, min(period_start_fy)""",
    )
    by_program = _rows(
        conn,
        """
        with s as (
          select coalesce(program, '(not recorded)') program, sum(subapplications) subs,
                 sum(positive_obligation_subapplications) positive,
                 coalesce(sum(subapplications) filter (where county_mapping = 'county_mapped'), 0)
                   county_mapped,
                 sum(obligated_real_usd) obligated_real,
                 sum(county_attributed_obligated_real_usd) county_obligated_real
          from serving.hma_status_summary group by 1
        ),
        c as (
          select coalesce(program, '(not recorded)') program,
                 count(distinct county_fips) counties_observed,
                 count(distinct county_fips) filter (where hma_activity_stage = 'obligated')
                   counties_obligated
          from serving.hma_county_program_hazard_activity group by 1
        )
        select s.*, coalesce(c.counties_observed, 0) counties_observed,
               coalesce(c.counties_obligated, 0) counties_obligated
        from s left join c using (program) order by s.subs desc, s.program""",
    )
    by_hma_hazard = _rows(
        conn,
        """
        select hazard_family, bool_and(is_weather_hazard) is_weather,
               count(distinct county_fips) counties_observed,
               count(distinct county_fips) filter (where hma_activity_stage = 'obligated')
                 counties_obligated,
               sum(observed_subapplications) links,
               sum(obligated_real_usd) obligated_real
        from serving.hma_county_program_hazard_activity
        group by 1 order by counties_observed desc, hazard_family""",
    )
    status_mix = _rows(
        conn,
        """
        select coalesce(status, '(status not recorded)') status, sum(subapplications) subs,
               sum(positive_obligation_subapplications) positive,
               sum(obligation_not_reported_subapplications) not_reported,
               sum(zero_obligation_subapplications) + sum(negative_obligation_subapplications)
                 zero_or_negative,
               coalesce(sum(subapplications) filter (where county_mapping = 'county_mapped'), 0)
                 county_mapped,
               sum(obligated_real_usd) obligated_real
        from serving.hma_status_summary group by 1 order by subs desc, status""",
    )
    top_loss = _rows(
        conn,
        """
        select county_name, state_abbr, damage_total_usd, damage_total_real_usd,
               damage_per_capita_real, svi_quartile_label, major_disasters, hma_obligated_real_usd
        from serving.county_resilience_profile
        where storm_declaration_geography_comparable
        order by damage_total_real_usd desc nulls last, county_fips limit 10""",
    )
    dq = _rows(
        conn,
        """
        select check_name, layer, severity, status, failing_rows, total_rows, fail_ratio, handling
        from ops.dq_results
        where dq_run_id = (select dq_run_id from ops.dq_results order by id desc limit 1)
           or id in (select max(id) from ops.dq_results where layer = 'raw' group by check_name)
        order by case layer when 'raw' then 1 when 'staging' then 2 when 'curated' then 3
                 else 4 end, id""",
    )
    dq = list({r["check_name"]: r for r in dq}.values())
    marks = {"pass": "✓ pass", "warn": "! warn", "fail": "✕ fail", "error": "✕ error"}
    for r in dq:
        r["status_label"] = marks.get(r["status"], r["status"])
    sources = _rows(
        conn,
        """
        select source, count(*) objects, sum(size_bytes) bytes, max(retrieved_at) last_retrieved,
               min(source_url) example_url
        from ops.source_objects group by source order by source""",
    )
    layer_counts = _rows(
        conn,
        """
        select table_schema as layer, count(*) tables from information_schema.tables
        where table_schema in ('raw','staging','curated','serving') group by 1""",
    )

    hma_n = _num(totals["hma_counties"]) or 0
    share_observed = (_num(totals["observed"]) or 0) / hma_n if hma_n else None
    share_obligated = (_num(totals["obligated"]) or 0) / hma_n if hma_n else None
    legacy_n = int(_num(totals["legacy_counties"]) or 0)
    not_comparable_n = int(_num(totals["not_comparable"]) or 0)
    history_nc_n = int(_num(totals["history_not_comparable"]) or 0)
    history_scope = (
        f" (incl. {legacy_n} legacy CT counties' pre-change history"
        + (f"; excl. {history_nc_n} CT planning regions, not comparable" if history_nc_n else "")
        + ")"
        if legacy_n or history_nc_n
        else ""
    )
    tiles = [
        (
            intf(totals["current_counties"]),
            "current counties and county-equivalents in scope"
            + (f" (+ {legacy_n} legacy CT counties, reported separately)" if legacy_n else ""),
        ),
        (
            intf(totals["event_county_records"]),
            "storm event-county records (one storm event counts once per county it touched; "
            "not distinct storm events)" + history_scope,
        ),
        (
            money(totals["damage_real"]),
            f"NOAA-recorded damage ({real}; nominal {money(totals['damage'])})" + history_scope,
        ),
        (
            pct((_num(totals["covered"]) or 0) / (_num(totals["sig"]) or 1)),
            "significant event-county pairs with major disaster declaration coverage"
            + history_scope,
        ),
        (
            f"{pct(share_observed)} · {pct(share_obligated)}",
            f"of {intf(hma_n)} HMA-comparable counties and municipios (states, DC, Puerto Rico), "
            f"all {fiscal_years} fiscal years: observed county-mapped subapplication · recorded "
            "positive obligation (see note)",
        ),
        (
            money(totals["hma_real"]),
            f"recorded positive HMA obligation attributed to counties ({real}"
            + (f"; {money(totals['legacy_hma_real'])} on legacy CT counties" if legacy_n else "")
            + ")",
        ),
        (
            intf(totals["follow_up"]),
            "high-need, low-obligation counties (follow-up screen, of "
            f"{intf(totals['follow_up_universe'])} states + DC counties with comparable storm "
            "history and HMA geography)",
        ),
    ]
    headline_note = (
        "<p class='note'><b>Reading the HMA headline.</b> "
        f"The two HMA shares use the same denominator: {intf(hma_n)} HMA-comparable counties and "
        "municipios of the states, DC and Puerto Rico. Island areas are reported separately"
        + (
            f"; the {not_comparable_n} Connecticut planning regions (HMA geography not comparable)"
            f" and the {legacy_n} legacy Connecticut counties (separate universe) are also outside "
            "this denominator"
            if not_comparable_n or legacy_n
            else ""
        )
        + ". Only these HMA shares leave the legacy counties out: the event-county record, "
        "damage and coverage tiles include the legacy counties' storm and declaration records "
        "(real pre-change history) and leave out the planning regions, whose 2000–2025 storm and "
        "declaration history is not comparable."
        + f" The two shares are close because they cover all {fiscal_years} profile fiscal "
        "years: of the "
        f"{intf(totals['observed'])} counties with an observed county-mapped subapplication, "
        f"{intf(totals['obligated'])} are also named in at least one with a recorded positive "
        f"obligation and only {intf(totals['observed_no_positive'])} are not. Over a long window "
        "almost every county named in any subapplication is also named in one with a positive "
        "obligated amount, so the shares diverge mainly within five-year periods (see the period "
        "tables in Q3). Neither share says anything about counties that tried to apply but have "
        "no record. "
        f"Recorded positive HMA obligation in scope totals {money3(positive_real)} ({real}) over "
        f"{intf(hma_money['subs'])} subapplications; {money3(county_real)} is attributed to "
        f"counties and {money3(noncounty_real)} "
        f"({pct(noncounty_real / positive_real if positive_real else None)}) stays at state "
        "level (statewide, out-of-state, unresolved or unspecified benefiting areas). The net sum "
        f"including negative de-obligation records is {money3(hma_money['net_real'])}; positive "
        "amounts are used throughout.</p>"
    )
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Resilience Gap - findings report</title>",
        f"<style>{CSS}</style></head>",
        "<body class='viz-root'><main>",
        "<h1>Resilience Gap: how presidential disaster declarations and recorded HMA "
        "obligations align with county storm losses and social vulnerability</h1>",
        f"<p class='muted'>Profile <b>{esc(prof['profile'])}</b>: {prof['start_year']}–"
        f"{prof['end_year']}, {esc(states)}. Generated "
        f"{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} from serving tables.</p>",
        "<div class='tiles'>"
        + "".join(f"<div class='tile'><b>{esc(v)}</b><span>{esc(k)}</span></div>" for v, k in tiles)
        + "</div>",
        headline_note,
        "<p class='note'>Q2 counts <b>event-county pairs</b>: a storm event that touches three "
        "counties counts three times. A pair is significant when its county-allocated damage is "
        f"≥ {money(prof['significant_damage_real_usd'])} in constant {base} dollars or the event "
        f"caused ≥ {prof['significant_casualties']} casualty. Covered = a weather-related Major "
        "Disaster (DR) declaration designates the county, or is a literal \u201cStatewide\u201d "
        "designation, with a compatible hazard family, and the event falls within the declared "
        "incident window. Tribal-area designations (reservations, tribal statistical areas, "
        "Alaska Native villages) are kept as their own areas and never counted as county or "
        "statewide coverage. "
        "SVI quartiles use fixed national cut points of the CDC/ATSDR SVI 2022 percentile and "
        "cover the 50 states + DC only. Dollar figures marked "
        f"{esc(real)} are restated in constant {base} dollars with the BLS CPI-U "
        f"({esc(prof.get('cpi_series_id'))}); nominal values are kept alongside.</p>",
        "<p class='note'><b>Four measures, four stages, kept separate.</b> "
        "(1) <i>Declaration coverage</i> is designation of a county for a declared incident "
        "(eligibility), not money. (2) <i>Observed HMA subapplication activity</i>: the county "
        "is named in a subapplication that reached FEMA. (3) <i>Status</i>: OpenFEMA's current "
        "recorded status, shown verbatim, a snapshot rather than a final outcome. (4) "
        "<i>Recorded obligation</i>: FEMA's obligated amount. Declaration turndowns "
        "(DeclarationDenials) are government-level outcomes of presidential declaration "
        "requests; they carry no county and are not HMA decisions. The analysis is descriptive, "
        "not causal.</p>",
        f"<p class='note'><b>Central limitation.</b> {esc(CENTRAL_LIMITATION)}</p>",
    ]
    # always shown: it is part of the method, even when Connecticut is outside the profile
    parts.append(
        f"<p class='note'><b>Connecticut geography break.</b> {esc(GEOGRAPHY_BREAK)} In this "
        f"profile: {history_nc_n} current units without comparable storm and declaration "
        f"history, {not_comparable_n} current units with HMA geography not comparable, "
        f"{legacy_n} legacy counties.</p>"
    )

    parts.append("<h2>Q1. Who bears the storm losses?</h2>")
    parts.append(
        chart_with_table(
            f"NOAA-recorded storm damage per resident ({real}), by national SVI quartile",
            "Property + crop damage over the profile years in constant dollars, divided by the "
            "2022 population of the units with comparable storm history (numerator and "
            "denominator use the same units; the CT planning regions are excluded from both). "
            "States + DC.",
            hbar(
                [
                    (
                        r["svi_quartile_label"],
                        _num(r["damage_per_capita_real"]),
                        f"{r['svi_quartile_label']}: "
                        f"{money(r['damage_per_capita_real'], 2)} per resident ({real}); "
                        f"{intf(r['counties_history_comparable'])} counties with comparable "
                        f"storm history; {money(r['damage_total_real_usd'])} total",
                    )
                    for r in q_svi
                ],
                lambda v: money(v, 2),
                "Damage per capita by SVI quartile",
            ),
            table(
                q,
                [
                    ("svi_quartile_label", "SVI quartile", None),
                    ("counties", "Counties", intf),
                    ("population", "Population", intf),
                    (
                        "counties_history_not_comparable",
                        "No comparable storm history (excluded)",
                        intf,
                    ),
                    ("counties_history_comparable", "Counties with comparable history", intf),
                    (
                        "history_comparable_population",
                        "Population of those counties (denominator)",
                        intf,
                    ),
                    ("damage_total_usd", "Damage (nominal)", money),
                    ("damage_total_real_usd", f"Damage ({real})", money),
                    ("damage_per_capita_real", f"Damage / capita ({real})", lambda v: money(v, 2)),
                ],
            ),
        )
    )
    parts.append(
        "<h3>Counties with the largest recorded losses</h3><p class='muted'>Units with "
        "comparable storm history only (the CT planning regions have none to rank).</p>"
    )
    parts.append(
        table(
            top_loss,
            [
                ("county_name", "County", None),
                ("state_abbr", "State", None),
                ("damage_total_usd", "Damage (nominal)", money),
                ("damage_total_real_usd", f"Damage ({real})", money),
                ("damage_per_capita_real", f"Per capita ({real})", lambda v: money(v, 0)),
                ("svi_quartile_label", "SVI group", None),
                ("major_disasters", "Major disasters", intf),
                ("hma_obligated_real_usd", f"Recorded HMA obligation ({real})", money),
            ],
        )
    )

    parts.append("<h2>Q2. Do declarations follow significant events?</h2>")
    parts.append(
        chart_with_table(
            "Share of significant event-county pairs covered by a Major Disaster declaration, by "
            "SVI quartile",
            "Coverage is designation of the county for a declared incident (eligibility), not "
            "money received. A low share can reflect state requests and FEMA damage thresholds; "
            "it is a divergence to explain, not evidence of neglect. Units with comparable storm "
            "and declaration history only (CT planning regions excluded).",
            hbar(
                [
                    (
                        r["svi_quartile_label"],
                        _num(r["coverage_rate"]),
                        f"{r['svi_quartile_label']}: {pct(r['coverage_rate'])} of "
                        f"{intf(r['significant_event_counties'])} significant event-county pairs "
                        "covered",
                    )
                    for r in q_svi
                ],
                pct,
                "Coverage rate by SVI quartile",
            ),
            table(
                q,
                [
                    ("svi_quartile_label", "SVI quartile", None),
                    ("significant_event_counties", "Significant event-county pairs", intf),
                    ("covered_event_counties", "Covered pairs", intf),
                    ("coverage_rate", "Covered", pct),
                ],
            ),
        )
    )
    parts.append(
        chart_with_table(
            "Coverage by hazard family",
            "Same measure, split by the harmonised hazard family of the NOAA event. A "
            "declaration covers an event only if its hazard family is compatible "
            "(seeds/hazard_match_rules.csv); Heat and Other Weather events have no compatible "
            "declaration type.",
            hbar(
                [
                    (
                        r["hazard_family"],
                        _num(r["coverage"]),
                        f"{r['hazard_family']}: {pct(r['coverage'])} of {intf(r['events'])} "
                        "event-county pairs",
                    )
                    for r in by_hazard
                ],
                pct,
                "Coverage rate by hazard family",
            ),
            table(
                by_hazard,
                [
                    ("hazard_family", "Hazard family", None),
                    ("events", "Significant event-county pairs", intf),
                    ("coverage", "Covered", pct),
                    ("damage_real", f"Damage ({real})", money),
                ],
            ),
        )
    )
    parts.append(
        chart_with_table(
            "Coverage by FEMA region, with declaration requests turned down",
            "Coverage of significant event-county pairs by the FEMA region of the county. Request "
            "columns count weather-related Major Disaster requests by incident year: declared "
            "(disaster numbers) and turned down (OpenFEMA DeclarationDenials, current status "
            "\u201cTurndown\u201d). A turndown is FEMA's recorded decision on a state, territory "
            "or tribal request; it says nothing about the damage, and events for which no "
            "declaration was requested appear in neither column.",
            hbar(
                [
                    (
                        r["region"],
                        _num(r["coverage"]),
                        f"{r['region']}: {pct(r['coverage'])} of {intf(r['events'])} pairs "
                        f"covered; {intf(r['declared'])} declared, {intf(r['turned_down'])} "
                        "turned down",
                    )
                    for r in by_region
                    if r["events"]
                ],
                pct,
                "Coverage rate by FEMA region",
            ),
            table(
                by_region,
                [
                    ("region", "FEMA region", None),
                    ("events", "Significant event-county pairs", intf),
                    ("coverage", "Covered", pct),
                    ("declared", "DR declared", intf),
                    ("turned_down", "DR requests turned down", intf),
                    ("turndown_share", "Turned down / decided", pct),
                ],
            ),
        )
    )
    parts.append(
        f"<p class='muted'>Tribal-area designations in scope: {intf(tribal['areas'])} areas in "
        f"{intf(tribal['declarations'])} weather-related declarations. They are kept with their "
        "FEMA designation text and do not count as coverage of any county.</p>"
    )
    parts.append(
        "<h3>Coverage by state</h3><p class='muted'>Significant event-county pairs by the state "
        "of the county, the share covered by a weather-related Major Disaster declaration, and "
        "the state's weather-related Major Disaster requests declared and turned down (by "
        "incident year). Same definitions as above; coverage is designation, not money, and a "
        "state's share depends on its hazard mix and on what was requested. A state row counts "
        "every event-county pair recorded in the state, so Connecticut's row is its pre-change "
        "history on the legacy counties.</p>"
    )
    parts.append(
        table(
            by_state,
            [
                ("state_abbr", "State", None),
                ("pairs", "Significant event-county pairs", intf),
                ("covered", "Covered pairs", intf),
                ("coverage", "Covered", pct),
                ("damage_real", f"Damage of significant pairs ({real})", money),
                ("declared", "DR declared", intf),
                ("turned_down", "DR requests turned down", intf),
                ("turndown_share", "Turned down / decided", pct),
            ],
        )
        if by_state
        else "<p>No significant event-county pairs or requests in this profile.</p>"
    )

    parts.append(
        "<h2>Q3. Mitigation alignment, stage by stage: observed subapplications, status and "
        "recorded obligation</h2>"
    )
    parts.append(f"<p class='note'><b>Central limitation.</b> {esc(CENTRAL_LIMITATION)}</p>")
    parts.append(
        "<p class='muted'>Each county is in exactly one stage: <b>no observed county-mapped "
        "subapplication</b>; <b>observed, no recorded positive obligation</b> (at least one "
        "county-mapped subapplication, none with a recorded obligated amount above zero); or "
        "<b>recorded positive obligation</b>; or, for the current Connecticut planning regions "
        "only, <b>geography not comparable</b> (HMA names the legacy counties; those regions are "
        "left out of every Q3 share and rate). Stages use county-mapped benefiting areas only; "
        "statewide, out-of-state, unresolved and unspecified shares stay at state level. "
        "Declaration turndowns are not used in Q3.</p>"
    )
    parts.append(
        chart_with_table(
            "Share of counties with an observed county-mapped HMA subapplication, by national SVI "
            "quartile (all profile fiscal years)",
            "Observed activity: the county is named in at least one subapplication that reached "
            "FEMA. The remaining counties have no observed county-mapped subapplication, which "
            "is not the same as not applying. Denominator: HMA-comparable counties (CT planning "
            "regions excluded).",
            hbar(
                [
                    (
                        r["svi_quartile_label"],
                        _num(r["share_counties_hma_observed"]),
                        f"{r['svi_quartile_label']}: {pct(r['share_counties_hma_observed'])} of "
                        f"{intf(r['counties_hma_comparable'])} HMA-comparable counties observed; "
                        f"{intf(r['counties_hma_not_observed'])} with no observed county-mapped "
                        "subapplication",
                    )
                    for r in q_svi
                ],
                pct,
                "Share of counties with observed HMA activity by SVI quartile",
            ),
            table(
                q,
                [
                    ("analysis_universe", "Universe", None),
                    ("svi_quartile_label", "SVI group", None),
                    ("counties", "Counties", intf),
                    (
                        "counties_hma_geography_not_comparable",
                        "Geography not comparable (excluded)",
                        intf,
                    ),
                    ("counties_hma_comparable", "HMA-comparable counties", intf),
                    ("counties_hma_not_observed", "No observed county-mapped subapplication", intf),
                    (
                        "counties_hma_observed_no_positive_obligation",
                        "Observed, no recorded positive obligation",
                        intf,
                    ),
                    ("counties_hma_obligated", "Recorded positive obligation", intf),
                    ("share_counties_hma_observed", "Observed", pct),
                    ("share_counties_hma_obligated", "Obligated", pct),
                    ("hma_observed_subapplications", "Distinct subapplications", intf),
                    (
                        "hma_positive_obligation_subapplications",
                        "… with positive obligation",
                        intf,
                    ),
                ],
            ),
        )
    )
    parts.append(
        chart_with_table(
            "Share of counties with a recorded positive HMA obligation, by national SVI quartile "
            "(all profile fiscal years)",
            "Positive obligation: at least one county-mapped subapplication with a recorded "
            "obligated amount above zero, whatever its status. Compare with the chart above to "
            "see where activity is observed but no positive obligation is recorded.",
            hbar(
                [
                    (
                        r["svi_quartile_label"],
                        _num(r["share_counties_hma_obligated"]),
                        f"{r['svi_quartile_label']}: {pct(r['share_counties_hma_obligated'])} of "
                        f"{intf(r['counties_hma_comparable'])} HMA-comparable counties with a "
                        "recorded positive obligation; "
                        f"{intf(r['counties_hma_observed_no_positive_obligation'])} observed "
                        "without one",
                    )
                    for r in q_svi
                ],
                pct,
                "Share of counties with recorded positive obligation by SVI quartile",
            ),
            table(
                q,
                [
                    ("analysis_universe", "Universe", None),
                    ("svi_quartile_label", "SVI group", None),
                    ("counties_hma_comparable", "HMA-comparable counties", intf),
                    ("counties_hma_obligated", "Recorded positive obligation", intf),
                    (
                        "counties_hma_observed_no_positive_obligation",
                        "Observed, no recorded positive obligation",
                        intf,
                    ),
                    ("share_counties_hma_obligated", "Obligated", pct),
                    ("hma_obligated_real_usd", f"Recorded obligation ({real})", money),
                ],
            ),
        )
    )
    period_cols = [
        ("period_label", "Fiscal years", None),
        ("fiscal_years_in_period", "FYs", intf),
        ("svi_quartile_label", "SVI group", None),
        ("counties_hma_comparable", "HMA-comparable counties", intf),
        ("counties_not_observed", "No observed county-mapped subapplication", intf),
        (
            "counties_observed_no_positive_obligation",
            "Observed, no recorded positive obligation",
            intf,
        ),
        ("counties_obligated", "Recorded positive obligation", intf),
        ("share_counties_observed", "Observed", pct),
        ("share_counties_obligated", "Obligated", pct),
        ("obligated_real_per_capita", f"Obligation / resident ({real})", lambda v: money(v, 2)),
    ]
    parts.append(
        "<h3>Stage by five-year fiscal-year period and national SVI quartile (states + DC)</h3>"
        "<p class='muted'>Fixed periods FY2001–2005 … FY2021–2025 (FY2000 forms a one-year "
        "period), clipped to the profile; compare counts only between periods with the same "
        "number of fiscal years. Recent periods include subapplications still under review. "
        "Shares use HMA-comparable counties of each quartile. Within a period the observed and "
        "obligated shares can differ widely even where the all-period shares are close.</p>"
    )
    parts.append(
        table(stage_svi_period, period_cols)
        if stage_svi_period
        else "<p>No states + DC counties in this profile.</p>"
    )
    parts.append(
        "<h3>Stage by period, national quartiles combined (states + DC)</h3><p class='muted'>"
        "The rows above summed over Q1–Q4, for orientation only; the comparison across SVI "
        "groups is the table above.</p>"
    )
    parts.append(
        table(
            stage_periods,
            [
                ("period_label", "Fiscal years", None),
                ("fiscal_years", "FYs", intf),
                ("not_comparable", "Geography not comparable (excluded)", intf),
                ("not_observed", "No observed county-mapped subapplication", intf),
                ("observed_no_positive", "Observed, no recorded positive obligation", intf),
                ("obligated", "Recorded positive obligation", intf),
                ("share_observed", "Observed", pct),
                ("share_obligated", "Obligated", pct),
                ("obligated_real", f"Recorded obligation ({real})", money),
                ("weather_obligated_real", f"… weather-related ({real})", money),
            ],
        )
    )
    parts.append(
        "<h3>Stage by period in the other analysis universes</h3><p class='muted'>Puerto Rico "
        "(SVI ranked only within Puerto Rico), island areas (no SVI) and the legacy Connecticut "
        "counties (HMA records of the pre-2022 geography) are separate universes, never pooled "
        "into a national quartile or into each other.</p>"
    )
    parts.append(
        table(stage_other_period, [("universe_label", "Universe", None)] + period_cols)
        if stage_other_period
        else "<p>No such areas in this profile.</p>"
    )
    parts.append(
        chart_with_table(
            "Share of counties with a recorded positive HMA obligation, by quartile of storm "
            f"damage per resident ({real}; states + DC, all profile fiscal years)",
            "Damage-burden quartiles rank constant-dollar NOAA damage per resident within states "
            "+ DC (D4 = top quartile, the high-loss group of the follow-up screen), over units "
            "with comparable storm history only: the CT planning regions have no comparable "
            "history, hence no damage rank and no quartile. HMA-comparable counties only. Many "
            "counties have no recorded damage and tie in D1. Descriptive alignment, not a cause.",
            hbar(
                [
                    (
                        r["damage_burden_label"],
                        _num(r["share_counties_obligated"]),
                        f"{r['damage_burden_label']}: {pct(r['share_counties_obligated'])} of "
                        f"{intf(r['counties'])} counties with a recorded positive obligation; "
                        f"{intf(r['counties_not_observed'])} with no observed county-mapped "
                        f"subapplication; {money(r['obligated_real_per_capita'], 2)} per resident",
                    )
                    for r in burden_nat
                ],
                pct,
                "Share of counties with recorded positive obligation by damage-burden quartile",
            ),
            table(
                burden,
                [
                    ("universe_label", "Universe", None),
                    ("damage_burden_label", "Damage per resident quartile", None),
                    ("counties", "HMA-comparable counties", intf),
                    (
                        "counties_excluded_geography_not_comparable",
                        "Geography not comparable (excluded)",
                        intf,
                    ),
                    ("counties_not_observed", "No observed county-mapped subapplication", intf),
                    (
                        "counties_observed_no_positive_obligation",
                        "Observed, no recorded positive obligation",
                        intf,
                    ),
                    ("counties_obligated", "Recorded positive obligation", intf),
                    ("share_counties_obligated", "Obligated", pct),
                    (
                        "obligated_real_per_capita",
                        f"Obligation / resident ({real})",
                        lambda v: money(v, 2),
                    ),
                    (
                        "median_obligated_real_per_capita",
                        f"County median / resident ({real})",
                        lambda v: money(v, 2),
                    ),
                    ("obligated_per_damage_dollar", "Per $ of damage", lambda v: money(v, 2)),
                    ("counties_svi_q4", "… in SVI Q4", intf),
                    ("counties_high_need_low_obligation", "Follow-up screen", intf),
                ],
            ),
        )
        if burden
        else "<p>No ranked counties in this profile.</p>"
    )
    parts.append(
        chart_with_table(
            f"Recorded HMA obligation per resident ({real}), by national SVI quartile",
            "County-attributed shares of positive recorded obligations (fiscal years in profile); "
            "statewide, out-of-state and unresolved shares are excluded from county totals. "
            "Per-resident rates use the population of HMA-comparable counties (CT planning "
            "regions excluded). A recorded obligation is not money received by a county "
            "government.",
            hbar(
                [
                    (
                        r["svi_quartile_label"],
                        _num(r["hma_per_capita_real"]),
                        f"{r['svi_quartile_label']}: {money(r['hma_per_capita_real'], 2)} per "
                        f"resident ({real}); {money(r['hma_obligated_real_usd'])} total",
                    )
                    for r in q_svi
                ],
                lambda v: money(v, 2),
                "Recorded HMA obligation per capita by SVI quartile",
            ),
            table(
                q,
                [
                    ("svi_quartile_label", "SVI group", None),
                    ("hma_obligated_usd", "Recorded obligation (nominal)", money),
                    ("hma_obligated_real_usd", f"Recorded obligation ({real})", money),
                    ("hma_weather_obligated_real_usd", f"… weather-related ({real})", money),
                    ("hma_per_capita_real", f"Per resident ({real})", lambda v: money(v, 2)),
                    (
                        "hma_weather_per_capita_real",
                        f"Weather-related per resident ({real})",
                        lambda v: money(v, 2),
                    ),
                    (
                        "hma_per_damage_dollar",
                        "Per $ of recorded damage (real)",
                        lambda v: money(v, 2),
                    ),
                    ("high_need_low_obligation_counties", "Follow-up screen counties", intf),
                ],
            ),
        )
    )
    parts.append(
        "<p class='note'><b>Weather-related obligation is a lower bound.</b> "
        f"{intf(unclassified['subs'])} of the {intf(unclassified['all_subs'])} in-scope "
        "subapplications have no primaryHazard in OpenFEMA, so their hazard cannot be classified "
        "as weather or non-weather; they count in recorded-obligation totals but never in "
        f"weather-related totals. {intf(unclassified['county_subs'])} of them are attributed to "
        f"counties, with {money3(unclassified['county_real'])} ({real}) of recorded positive "
        "obligation in county shares. "
        "<b>State-year scope.</b> serving.state_year_summary has rows only for states, DC and "
        f"territories; {intf(state_year_out['subs'])} in-scope subapplications "
        f"({intf(state_year_out['no_state'])} with no applicant state"
        + (
            f"; other applicants: {esc(state_year_out['other_states'])}"
            if state_year_out["other_states"]
            else ""
        )
        + f"), with {money3(state_year_out['positive_real'])} ({real}) of recorded positive "
        "obligation, "
        "are therefore not in it. They remain in every county, status and program table; DQ "
        "checks state_year_hma_out_of_scope and state_year_hma_reconciles account for them.</p>"
    )
    parts.append(
        "<h3>By program and by hazard family (observed activity only)</h3><p class='muted'>"
        "Programs and hazard families are shown only where activity is observed. A county "
        "missing from a program or hazard row is not a county that did not apply: programs "
        "existed in different years, and a subapplication records one primary hazard. "
        "Subapplication counts include state-level subapplications; county counts use "
        "county-mapped areas.</p>"
    )
    parts.append(
        table(
            by_program,
            [
                ("program", "Program (OpenFEMA, verbatim)", None),
                ("subs", "Subapplications", intf),
                ("positive", "… with positive obligation", intf),
                ("county_mapped", "… county-mapped", intf),
                ("counties_observed", "Counties observed", intf),
                ("counties_obligated", "Counties with positive obligation", intf),
                ("obligated_real", f"Recorded obligation ({real})", money),
                ("county_obligated_real", f"… attributed to counties ({real})", money),
            ],
        )
        if by_program
        else "<p>No HMA subapplications in this profile.</p>"
    )
    parts.append(
        table(
            by_hma_hazard,
            [
                ("hazard_family", "Hazard family (primary hazard)", None),
                ("is_weather", "Weather-related", None),
                ("counties_observed", "Counties observed", intf),
                ("counties_obligated", "Counties with positive obligation", intf),
                ("links", "County-subapplication links", intf),
                ("obligated_real", f"Recorded obligation to counties ({real})", money),
            ],
        )
        if by_hma_hazard
        else ""
    )
    parts.append(
        "<h3>Recorded status mix</h3><p class='muted'>OpenFEMA's current status of each "
        "subapplication, shown verbatim (a snapshot, not a final outcome). Status and recorded "
        "obligation are separate source fields: a positive obligation can appear under several "
        "statuses, and the stages above use the recorded amount, never the status.</p>"
    )
    parts.append(
        table(
            status_mix,
            [
                ("status", "Status (verbatim)", None),
                ("subs", "Subapplications", intf),
                ("positive", "… with positive obligation", intf),
                ("zero_or_negative", "… zero or negative amount", intf),
                ("not_reported", "… no amount recorded", intf),
                ("county_mapped", "… county-mapped", intf),
                ("obligated_real", f"Recorded obligation ({real})", money),
            ],
        )
        if status_mix
        else ""
    )
    follow_up_total = int(_num(totals["follow_up"]) or 0)
    parts.append(
        "<h3>High-need, low-obligation follow-up screen"
        + (
            f": top {len(follow_up)} of {follow_up_total} counties"
            if follow_up_total > len(follow_up)
            else f": all {follow_up_total} counties"
        )
        + "</h3><p class='muted'>States + DC only, counties with comparable storm history and "
        "HMA geography only (the CT planning regions have neither, so they cannot be screened): "
        f"top-quartile constant-dollar ({real}) storm damage per resident, national SVI quartile "
        "4, and constant-dollar recorded HMA obligation per resident at or below the median of "
        "HMA-comparable states + DC counties. "
        + (
            f"The table lists the {len(follow_up)} with the highest damage per resident; the "
            f"full list of {follow_up_total} is in serving.county_resilience_profile "
            "(is_high_need_low_obligation) and the dashboard. "
            if follow_up_total > len(follow_up)
            else ""
        )
        + "Puerto Rico's is_low_obligation flag is computed against Puerto Rico's own median "
        "(each analysis universe uses its own), but Puerto Rico is not part of this screen "
        "because it has no national SVI rank. A screen for follow-up within the state's own HMA "
        "process, not evidence that a county was overlooked, did not apply, or was "
        "neglected.</p>"
    )
    parts.append(
        table(
            follow_up,
            [
                ("county_name", "County", None),
                ("state_abbr", "State", None),
                ("population_base", "Population", intf),
                ("damage_per_capita_real", f"Damage / capita ({real})", lambda v: money(v, 0)),
                ("svi_overall", "SVI", lambda v: "–" if v is None else f"{float(v):.2f}"),
                ("stage_label", "HMA stage (all FYs)", None),
                ("hma_observed_subapplications", "Observed subapplications", intf),
                ("hma_positive_obligation_subapplications", "… with positive obligation", intf),
                (
                    "hma_per_capita_real",
                    f"Recorded obligation / capita ({real})",
                    lambda v: money(v, 2),
                ),
                (
                    "hma_per_capita_real_median",
                    f"States + DC median, comparable counties ({real})",
                    lambda v: money(v, 2),
                ),
                ("significant_events", "Significant events", intf),
                ("significant_events_covered", "With declaration coverage", intf),
            ],
        )
        if follow_up
        else "<p>No county meets all three screen criteria in this profile.</p>"
    )

    parts.append("<h2>Q4. How long does a declaration take?</h2>")
    # The two promised lags: incident start -> request, and incident start -> declaration.
    # Request -> declaration is shown as an extra column, never instead of either.
    t_cols = [
        ("group_label", "Group", None),
        ("disasters", "Disasters", intf),
        ("median_days_incident_to_request", "Median days incident start→request", intf),
        ("p25_days_incident_to_declaration", "P25 days incident→declaration", intf),
        ("median_days_incident_to_declaration", "Median days incident start→declaration", intf),
        ("p75_days_incident_to_declaration", "P75 days incident→declaration", intf),
        ("median_days_request_to_declaration", "Median days request→declaration (extra)", intf),
        ("disasters_with_request_date", "With a request date", intf),
        ("top_hazard_family", "Most common hazard", None),
        ("top_hazard_share", "Its share", pct),
        ("sample_note", "Sample size", None),
    ]
    parts.append(
        "<p class='note'>Descriptive, one-way breakdowns of FEMA's own dates for weather-related "
        "Major Disaster declarations. Two lags are reported for every group: <b>incident start "
        "to declaration request</b> and <b>incident start to declaration</b>; request to "
        "declaration is shown as an extra column"
        + (
            f" ({intf(t_all['disasters'])} disasters, {intf(t_all['disasters_with_request_date'])}"
            " with a request date; overall medians "
            f"{intf(t_all['median_days_incident_to_request'])} days from incident start to "
            f"request and {intf(t_all['median_days_incident_to_declaration'])} days from "
            "incident start to declaration)"
            if t_all
            else ""
        )
        + ". No adjustment is made: SVI groups, hazard families and regions differ in hazard "
        "mix, year and state, so a difference between groups is not an effect of vulnerability. "
        "The most common hazard and its share are shown to make that confounding visible. "
        "Groups with fewer than 10 disasters are not comparable.</p>"
    )
    parts.append(
        chart_with_table(
            "Median days from incident start to Major Disaster declaration, by SVI group",
            "SVI group = population-weighted national SVI of the designated counties (states + "
            "DC). Puerto Rico, island areas and declarations with no county designation "
            "(statewide or tribal areas only) are separate groups.",
            hbar(
                [
                    (
                        r["group_label"],
                        _num(r["median_days_incident_to_declaration"]),
                        f"{r['group_label']}: median "
                        f"{intf(r['median_days_incident_to_declaration'])} days over "
                        f"{intf(r['disasters'])} disasters ({r['sample_note']})",
                    )
                    for r in t_svi
                ],
                lambda v: f"{v:,.0f} days",
                "Median days to declaration by SVI group",
            ),
            table(t_svi, t_cols),
        )
    )
    parts.append(
        chart_with_table(
            "Median days from incident start to the declaration request, by SVI group",
            "Days from the incident begin date to the date the state, territory or tribe "
            "requested the declaration (FEMA's declarationRequestDate), for declared weather "
            "Major Disasters with a request date. Same SVI groups as above.",
            hbar(
                [
                    (
                        r["group_label"],
                        _num(r["median_days_incident_to_request"]),
                        f"{r['group_label']}: median "
                        f"{intf(r['median_days_incident_to_request'])} days from incident start "
                        f"to request over {intf(r['disasters_with_request_date'])} disasters "
                        f"with a request date ({r['sample_note']})",
                    )
                    for r in t_svi
                ],
                lambda v: f"{v:,.0f} days",
                "Median days from incident start to request by SVI group",
            ),
            table(t_svi, t_cols),
        )
    )
    parts.append(
        chart_with_table(
            "Median days from incident start to declaration, by hazard family",
            "Hazard family of the declaration's incident type. Turned-down requests are weather "
            "Major Disaster requests of the same hazard family recorded as \u201cTurndown\u201d.",
            hbar(
                [
                    (
                        r["group_label"],
                        _num(r["median_days_incident_to_declaration"]),
                        f"{r['group_label']}: median "
                        f"{intf(r['median_days_incident_to_declaration'])} days over "
                        f"{intf(r['disasters'])} disasters",
                    )
                    for r in t_haz
                ],
                lambda v: f"{v:,.0f} days",
                "Median days to declaration by hazard family",
            ),
            table(
                t_haz,
                t_cols[:7]
                + [
                    ("turned_down", "Requests turned down", intf),
                    ("turndown_share", "Turned down / decided", pct),
                    ("sample_note", "Sample size", None),
                ],
            ),
        )
    )
    parts.append(
        chart_with_table(
            "Median days from incident start to declaration, by FEMA region",
            "FEMA region of the declaring state, territory or tribe.",
            hbar(
                [
                    (
                        r["group_label"],
                        _num(r["median_days_incident_to_declaration"]),
                        f"{r['group_label']}: median "
                        f"{intf(r['median_days_incident_to_declaration'])} days over "
                        f"{intf(r['disasters'])} disasters",
                    )
                    for r in t_reg
                ],
                lambda v: f"{v:,.0f} days",
                "Median days to declaration by FEMA region",
            ),
            table(
                t_reg,
                t_cols
                + [
                    ("turned_down", "Requests turned down", intf),
                    ("turndown_share", "Turned down / decided", pct),
                ],
            ),
        )
    )

    parts.append("<h2>Areas outside the national SVI ranking</h2>")
    parts.append(
        "<p class='muted'>CDC/ATSDR ranks Puerto Rico only against its own municipios (“Puerto "
        "Rico is not included in the U.S.-wide ranking”), and publishes no SVI for the island "
        "areas. These areas are therefore reported separately and never pooled into national "
        "quartiles. Puerto Rico's damage and HMA ranks, and its is_low_obligation flag, are "
        "computed against Puerto Rico's own distribution and median. The legacy Connecticut "
        "counties hold the storm, declaration and HMA records of the pre-2022 geography; they "
        "have no population or SVI, are left out of the current-geography HMA headline shares, "
        "and their storm and declaration records stay in national historical event totals.</p>"
    )
    parts.append(
        table(
            other_universes,
            [
                ("analysis_universe", "Universe", None),
                ("svi_quartile_label", "Group", None),
                ("counties", "Areas", intf),
                ("population", "Population", intf),
                ("damage_total_real_usd", f"Damage ({real})", money),
                ("damage_per_capita_real", f"Damage / capita ({real})", lambda v: money(v, 2)),
                ("coverage_rate", "Declaration coverage", pct),
                ("counties_hma_not_observed", "No observed county-mapped subapplication", intf),
                ("counties_hma_obligated", "Recorded positive obligation", intf),
                ("hma_obligated_real_usd", f"Recorded HMA obligation ({real})", money),
            ],
        )
        if other_universes
        else "<p>No such areas in this profile.</p>"
    )

    parts.append("<h2>How storm events were placed in counties</h2>")
    parts.append(
        "<p class='muted'>Zone-based events are mapped with the NWS zone–county crosswalk "
        "version in effect on the event date (name-verified inside documented gaps or when the "
        "nearest version is used). Unmapped events are kept with a reason and never guessed.</p>"
    )
    parts.append(
        table(
            zone_cov,
            [
                ("event_year", "Year", None),
                ("county_events", "County events", intf),
                ("zone_events", "Zone events", intf),
                ("zone_mapped_effective", "In-effect version", intf),
                ("zone_mapped_gap_name_verified", "Gap, name-verified", intf),
                ("zone_mapped_nearest_name_verified", "Nearest, name-verified", intf),
                ("zone_mapped_via_archive_version", "Via archived version", intf),
                ("zone_unmapped", "Unmapped", intf),
                ("zone_mapped_rate", "Mapped", pct),
            ],
        )
    )

    parts.append(f"<h2>Constant dollars (base year {base})</h2>")
    parts.append(
        "<p class='muted'>Factor = CPI-U annual average of the base year ÷ that of the year of "
        "the amount (storm damage by event year; HMA by federal fiscal year, October–September)."
        "</p>"
    )
    parts.append(
        table(
            infl,
            [
                ("year", "Year", None),
                ("cpi", "CPI-U", lambda v: f"{float(v):.3f}"),
                ("cpi_basis", "Basis", None),
                ("factor_to_base", f"Factor to {base} $", lambda v: f"{float(v):.4f}"),
            ],
        )
    )

    parts.append("<h2>Data quality (latest run)</h2>")
    parts.append(
        table(
            dq,
            [
                ("layer", "Layer", None),
                ("check_name", "Check", None),
                ("severity", "Severity", None),
                ("status_label", "Status", None),
                ("failing_rows", "Failing", intf),
                ("total_rows", "Total", intf),
                ("fail_ratio", "Ratio", pct),
                ("handling", "Handling", None),
            ],
        )
    )

    parts.append("<h2>Provenance</h2>")
    parts.append(
        table(
            sources,
            [
                ("source", "Source", None),
                ("objects", "Stored objects", intf),
                ("bytes", "Bytes", intf),
                ("last_retrieved", "Last retrieved", None),
                ("example_url", "Example URL", None),
            ],
        )
    )
    parts.append(
        "<p class='muted'>Tables per layer: "
        + ", ".join(f"{esc(r['layer'])} {r['tables']}" for r in layer_counts)
        + ".</p>"
    )
    parts.append(
        "<h2>Sources and attribution</h2><ul>"
        "<li>NOAA NCEI Storm Events Database (bulk CSV).</li>"
        "<li>NOAA NWS County–Public Forecast Zones Correlation files.</li>"
        "<li>FEMA OpenFEMA: Disaster Declarations Summaries v2 (declaration coverage, Q2 and "
        "Q4), HMA Subapplications v2 (subapplications that reached FEMA, their status and "
        "recorded obligation, Q3), Declaration Denials v1 (turned-down presidential declaration "
        "requests; government-level context for Q2 and Q4, not HMA decisions).</li>"
        "<li>CDC/ATSDR Social Vulnerability Index 2022: U.S. county file and the stand-alone "
        "Puerto Rico file.</li>"
        "<li>U.S. Census Bureau Vintage 2025 county and Puerto Rico municipio population "
        "estimates; 2020 county reference file.</li>"
        "<li>U.S. Bureau of Labor Statistics, CPI-U (CUUR0000SA0), Public Data API, retrieved "
        f"{esc(bls_retrieved.strftime('%Y-%m-%d') if bls_retrieved else 'n/a')}.</li></ul>"
        f"<p class='muted'>{esc(ATTRIBUTION)} {esc(BLS_STATEMENT)} No NOAA, CDC or Census "
        "endorsement is implied.</p></main></body></html>"
    )
    out = out_dir / "resilience_gap_report.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    return out
