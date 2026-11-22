"""End-to-end: run the real pipeline on real fixture excerpts (no network) and check results,
reconciliation and idempotency."""

import gzip

import psycopg
import pytest

from resgap.cli import main
from resgap.config import SQL_DIR
from resgap.db import run_sql_file
from resgap.dq.runner import load_checks
from tests.conftest import FIXTURES

from .conftest import reset_database

pytestmark = pytest.mark.integration


def q(url, sql):
    with psycopg.connect(url) as c, c.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def q1(url, sql):
    return q(url, sql)[0][0]


SERVING_FINGERPRINT = """
select md5(string_agg(t::text, '|' order by t.county_fips))
from serving.county_resilience_profile t
"""


@pytest.fixture(scope="module")
def pipeline(db_url, tmp_path_factory):
    reset_database(db_url)
    mp = pytest.MonkeyPatch()
    lake = tmp_path_factory.mktemp("lake")
    out = tmp_path_factory.mktemp("report")
    mp.setenv("RESGAP_DATABASE_URL", db_url)
    mp.setenv("RESGAP_LAKE_DIR", str(lake))
    mp.setenv("RESGAP_OBJECT_STORE", "local")
    rc = main(["--profile", "fixture", "run", "--offline-dir", str(FIXTURES), "--out", str(out)])
    yield {"rc": rc, "url": db_url, "lake": lake, "out": out, "mp": mp}
    mp.undo()


def test_run_succeeds_and_writes_report(pipeline):
    assert pipeline["rc"] == 0
    html = (pipeline["out"] / "resilience_gap_report.html").read_text()
    assert "Q1. Who bears the storm losses?" in html
    assert "not endorsed by FEMA" in html
    # Q3 wording: stages, recorded obligation, the central limitation, a follow-up screen
    assert "Central limitation." in html
    assert "no observed county-mapped subapplication" in html
    assert "Recorded positive obligation" in html
    assert "High-need, low-obligation follow-up screen" in html
    assert "Recorded status mix" in html
    # final-review additions: geography break, Q3 detail, Q2 by state, both Q4 lags, scope notes
    assert "Connecticut geography break." in html
    # storm / declaration break: the regions are excluded from history, not given zeros; only the
    # HMA headline shares leave the legacy counties out; the tile counts event-county records
    assert "Only the current-geography HMA headline shares leave the legacy counties out" in html
    assert "never as zero" in html
    assert "storm event-county records" in html
    assert "storm events (county-level)" not in html
    assert "left out of current-geography headline percentages" not in html
    assert "not part of any current-geography headline" not in html
    assert "Population of those counties (denominator)" in html
    assert "Stage by five-year fiscal-year period and national SVI quartile" in html
    assert "Stage by period in the other analysis universes" in html
    assert "by quartile of storm damage per resident" in html
    assert "Coverage by state" in html
    assert "Median days incident start→request" in html
    assert "Median days incident start→declaration" in html
    assert "Median days from incident start to the declaration request, by SVI group" in html
    assert "Weather-related obligation is a lower bound." in html
    assert "State-year scope." in html
    assert "Reading the HMA headline." in html
    assert "own median" in html
    lowered = html.lower()
    for misleading in ("priority-gap", "priority gap", "funded", "hma obligated"):
        assert misleading not in lowered, misleading


def test_raw_preserves_every_source_row(pipeline):
    url = pipeline["url"]
    storm = next((FIXTURES / "noaa_storm_events").glob("*.csv.gz"))
    n_lines = len(gzip.decompress(storm.read_bytes()).decode().splitlines()) - 1
    assert q1(url, "select count(*) from raw.noaa_storm_events_details") == n_lines
    assert q1(url, "select count(*) from ops.raw_rejects") == 0
    # raw keeps source text verbatim (damage strings are not parsed in raw)
    assert (
        q1(
            url,
            "select count(*) from raw.noaa_storm_events_details where damage_property like '%K'",
        )
        > 0
    )


def test_objects_have_checksums_and_lineage(pipeline):
    url = pipeline["url"]
    assert q1(url, "select count(*) from ops.source_objects where length(sha256) <> 64") == 0
    assert q1(url, "select count(distinct source) from ops.source_objects") == 9
    # offline copies are namespaced so a later live run can never reuse a fixture excerpt
    assert (
        q1(
            url,
            "select count(*) from ops.source_objects where object_key not like 'offline/%'"
            " or source_url not like 'file:%'",
        )
        == 0
    )
    assert (
        q1(
            url,
            "select count(*) from raw.openfema_records r left join ops.source_objects o "
            "on o.object_key = r._object_key where o.object_key is null",
        )
        == 0
    )


def test_all_pa_counties_in_serving(pipeline):
    assert (
        q1(
            pipeline["url"],
            "select count(*) from serving.county_resilience_profile "
            "where state_abbr = 'PA' and population_base is not null",
        )
        == 67
    )


def test_tropical_storm_debby_matched_to_dr_4815(pipeline):
    # NOAA records Debby's significant flooding on 2024-08-09 in Wayne (42127), Wyoming (42131)
    # and Susquehanna (42115) counties; all three are designated in DR-4815-PA.
    rows = q(
        pipeline["url"],
        """
        select county_fips, matched_declaration from serving.event_declaration_coverage
        where event_date = '2024-08-09' and covered_by_major_disaster order by 1""",
    )
    assert {("42115", "DR-4815-PA"), ("42127", "DR-4815-PA"), ("42131", "DR-4815-PA")} <= set(rows)


def test_debby_match_uses_an_explicit_hazard_rule(pipeline):
    # DR-4815-PA is a "Tropical Storm" declaration; Debby's NOAA events are floods, which the
    # hazard_match_rules seed lists as a companion hazard of tropical cyclones
    rows = q(
        pipeline["url"],
        """
        select distinct hazard_family, matched_hazard_family, matched_hazard_rule
        from serving.event_declaration_coverage where matched_declaration = 'DR-4815-PA'""",
    )
    assert rows == [("Flood", "Tropical Cyclone", "companion_hazard")]


def test_fema_regions_and_q4_breakdowns(pipeline):
    url = pipeline["url"]
    regions = dict(q(url, "select state_abbr, min(fema_region) from curated.dim_county group by 1"))
    assert (regions["PA"], regions["PR"], regions["VI"]) == (3, 2, 2)
    # real fixture: DR-4815-PA (Debby), DR-4805-PR, DR-4850-PR (Ernesto), DR-4841-VI
    timely = dict(
        q(url, "select fema_declaration_string, svi_group from serving.declaration_timeliness")
    )
    assert timely == {
        "DR-4815-PA": "Q2",
        "DR-4805-PR": "Puerto Rico (SVI ranked within PR)",
        "DR-4850-PR": "Puerto Rico (SVI ranked within PR)",
        "DR-4841-VI": "Island areas (no SVI published)",
    }
    sums = dict(
        q(
            url,
            "select breakdown, sum(disasters) from serving.declaration_timeliness_summary"
            " group by 1",
        )
    )
    assert sums == {"all": 4, "svi_group": 4, "hazard_family": 4, "fema_region": 4}
    notes = {r[0] for r in q(url, "select sample_note from serving.declaration_timeliness_summary")}
    assert notes == {"fewer than 10 disasters: do not compare"}


def test_declaration_denials_contribute(pipeline):
    url = pipeline["url"]
    # the one real denial in the fixture scope: a 2024 PA emergency request (winter storm),
    # current status "Turndown"
    assert q(
        url,
        "select state_abbr, declaration_type, hazard_family, request_status"
        " from curated.fact_declaration_denial",
    ) == [("PA", "EM", "Winter Weather", "Turndown")]
    assert q(
        url,
        "select declared, turned_down from serving.declaration_request_outcomes"
        " where state_abbr = 'PA' and declaration_type = 'EM'",
    ) == [(0, 1)]
    html = (pipeline["out"] / "resilience_gap_report.html").read_text()
    assert "DR requests turned down" in html
    assert "Median days from incident start to declaration, by FEMA region" in html


def test_money_reconciles_across_layers(pipeline):
    url = pipeline["url"]
    staged = q1(
        url,
        "select coalesce(sum(total_obligated_usd), 0) from staging.hma_subapplications"
        " where fiscal_year = 2024 and state_abbr in ('PA', 'PR', 'VI')",
    )
    curated = q1(url, "select coalesce(sum(obligated_usd), 0) from curated.fact_hma_allocation")
    assert abs(float(staged) - float(curated)) < 0.01
    events = q1(
        url,
        """select sum(coalesce(damage_property_usd,0)+coalesce(damage_crops_usd,0))
        from staging.storm_events e where exists (select 1 from staging.storm_event_county b
        where b.event_id = e.event_id)""",
    )
    facts = q1(url, "select sum(damage_total_usd) from curated.fact_storm_event_county")
    assert abs(float(events) - float(facts)) < 0.01


def test_analysis_universes_never_mix_svi_rankings(pipeline):
    url = pipeline["url"]
    rows = dict(
        (r[0], r[1:])
        for r in q(
            url,
            """
        select analysis_universe, count(*), count(population_base), count(svi_overall),
               count(svi_quartile), count(svi_pr_within_rank), min(svi_coverage)
        from curated.dim_county where in_profile_scope group by 1""",
        )
    )
    # PA: national SVI; 78 PR municipios: own ranking + Census PR population; VI: nothing
    assert rows["states_dc"] == (67, 67, 67, 67, 0, "national_svi")
    assert rows["puerto_rico"] == (78, 78, 0, 0, 78, "puerto_rico_svi_ranked_within_pr")
    assert rows["island_areas"] == (3, 0, 0, 0, 0, "no_svi_published")
    labels = {
        r[0]: r[1]
        for r in q(
            url, "select svi_quartile_label, analysis_universe from serving.svi_quartile_summary"
        )
    }
    assert labels["Puerto Rico (SVI ranked within PR)"] == "puerto_rico"
    assert labels["Island areas (no SVI published)"] == "island_areas"
    assert all(u == "states_dc" for k, u in labels.items() if k.startswith("Q"))
    assert (
        q1(
            url,
            "select count(*) from serving.county_resilience_profile "
            "where is_high_need_low_obligation and analysis_universe <> 'states_dc'",
        )
        == 0
    )


def test_hma_stages_on_real_fixture_records(pipeline):
    url = pipeline["url"]
    stage = dict(
        q(url, "select county_fips, hma_activity_stage from serving.county_resilience_profile")
    )
    # Aibonito: one real "Pending" subapplication with no obligated amount recorded
    assert stage["72009"] == "observed_no_positive_obligation"
    # Humacao: a "Pending" subapplication and an "Approved" one with $24,000 obligated
    assert stage["72069"] == "obligated"
    # Accented municipios: FEMA writes "Mayaguez", "Rio Grande", "Anasco", "Canovanas"; the
    # Census reference stores them double-encoded. Without accent folding these real records
    # would be lost and the municipios wrongly shown as "not_observed".
    assert stage["72097"] == "obligated"  # Mayagüez: "Approved", $24,000
    assert stage["72119"] == "obligated"  # Río Grande: "Approved", $24,000 (not CO's Rio Grande)
    assert stage["72011"] == "observed_no_positive_obligation"  # Añasco: "Pending"
    assert stage["72029"] == "observed_no_positive_obligation"  # Canóvanas: "Pending"
    # Adjuntas and the Virgin Islands districts: no county-mapped subapplication observed
    assert stage["72001"] == "not_observed"
    vi = {s for f, s in stage.items() if f.startswith("78")}
    assert vi == {"not_observed"}
    mix = dict(
        q(
            url,
            "select county_fips, hma_status_mix from serving.county_resilience_profile "
            "where county_fips in ('72069', '72001')",
        )
    )
    assert mix == {"72069": {"Approved": 1, "Pending": 1}, "72001": {}}
    # every stage value is one of the four; no county is labelled with a status. The fixture
    # profile (PA, PR, VI) has no Connecticut, so no unit is geography_not_comparable
    sql = "select distinct hma_activity_stage from serving.hma_county_period_activity"
    stages = {r[0] for r in q(url, sql)}
    assert stages <= {"not_observed", "observed_no_positive_obligation", "obligated"}
    assert (
        q1(
            url,
            "select count(*) from serving.county_resilience_profile "
            "where not hma_geography_comparable or hma_activity_stage = 'geography_not_comparable'",
        )
        == 0
    )
    # ... and no unit lacks comparable storm / declaration history, so 0 means "none recorded"
    assert (
        q1(
            url,
            "select count(*) from serving.county_resilience_profile "
            "where not storm_declaration_geography_comparable or storm_events is null "
            "or damage_total_real_usd is null or major_disasters is null",
        )
        == 0
    )


def test_hma_status_is_preserved_verbatim(pipeline):
    url = pipeline["url"]
    raw = dict(
        q(
            url,
            """
            select record->>'status', count(*) from raw.openfema_records
            where dataset = 'HmaSubapplications'
              and staging.try_int(record->>'fiscalYear') = 2024
              and upper(record->>'subapplicantStateAbbreviation') in ('PA', 'PR', 'VI')
            group by 1""",
        )
    )
    served = {
        k: int(v)
        for k, v in q(
            url, "select status, sum(subapplications) from serving.hma_status_summary group by 1"
        )
    }
    assert served == raw
    assert "Approved" in served  # real PR records say "Approved"; nothing else is relabelled
    # status and obligation are independent: positive obligations exist under statuses other
    # than "Obligated" in the real fixture
    other = q1(
        url,
        "select count(distinct hma_id) from curated.fact_hma_allocation "
        "where has_positive_obligation and status is distinct from 'Obligated'",
    )
    assert other > 0
    cols = {
        r[0]
        for r in q(
            url,
            "select column_name from information_schema.columns "
            "where table_schema in ('curated', 'serving')",
        )
    }
    for gone in (
        "is_funded",
        "hma_funded_subapplications",
        "is_priority_gap",
        "is_low_mitigation",
        "priority_gap_counties",
    ):
        assert gone not in cols, gone
    assert {"has_positive_obligation", "is_high_need_low_obligation", "is_low_obligation"} <= cols


def test_hma_periods_are_fixed_and_clipped(pipeline):
    url = pipeline["url"]
    # FY2024 lies in the fixed period FY2021-2025; the fixture profile holds only FY2024
    assert q(
        url,
        "select period_start_fy, period_end_fy, first_fy_in_profile, last_fy_in_profile, "
        "fiscal_years_in_period, is_partial_period, period_label from curated.dim_hma_period",
    ) == [(2021, 2025, 2024, 2024, 1, True, "FY2024")]
    assert (
        q1(url, "select count(*) from curated.fact_hma_allocation where period_start_fy <> 2021")
        == 0
    )
    counties = q1(url, "select count(*) from serving.county_resilience_profile")
    assert q1(url, "select count(*) from serving.hma_county_period_activity") == counties


def test_hma_funnel_reconciles(pipeline):
    url = pipeline["url"]
    # period rows add up to the all-period profile, county by county
    assert (
        q1(
            url,
            """
            select count(*) from serving.county_resilience_profile c
            join (select county_fips, sum(observed_subapplications) o,
                         sum(positive_obligation_subapplications) p, sum(obligated_usd) u,
                         sum(weather_obligated_real_usd) w
                  from serving.hma_county_period_activity group by 1) a using (county_fips)
            where a.o <> c.hma_observed_subapplications
               or a.p <> c.hma_positive_obligation_subapplications
               or abs(a.u - c.hma_obligated_usd) > 0.01
               or abs(a.w - c.hma_weather_obligated_real_usd) > 0.01""",
        )
        == 0
    )
    # program x hazard rows add up to the county-period and are never "not_observed"
    assert (
        q1(
            url,
            """
            select count(*) from serving.hma_county_period_activity a
            left join (select county_fips, period_start_fy, sum(observed_subapplications) o,
                              sum(obligated_usd) u
                       from serving.hma_county_program_hazard_activity group by 1, 2) p
              using (county_fips, period_start_fy)
            where coalesce(p.o, 0) <> a.observed_subapplications
               or abs(coalesce(p.u, 0) - a.obligated_usd) > 0.01""",
        )
        == 0
    )
    assert (
        q1(
            url,
            "select count(*) from serving.hma_county_program_hazard_activity "
            "where hma_activity_stage = 'not_observed'",
        )
        == 0
    )
    # county-mapped recorded obligation = positive county shares in the curated fact
    fact, profile = q(
        url,
        """
        select (select sum(obligated_usd) from curated.fact_hma_allocation
                where county_fips is not null and has_positive_obligation),
               (select sum(hma_obligated_usd) from serving.county_resilience_profile)""",
    )[0]
    assert abs(float(fact) - float(profile)) < 0.01
    # stages partition the counties in every summary row; all-period rows match the SVI summary
    assert (
        q1(
            url,
            "select count(*) from serving.hma_stage_summary where counties <> "
            "counties_not_observed + counties_observed_no_positive_obligation + counties_obligated",
        )
        == 0
    )
    assert sorted(
        q(
            url,
            "select analysis_universe, svi_quartile_label, counties_not_observed, "
            "counties_observed_no_positive_obligation, counties_obligated "
            "from serving.hma_stage_summary where period_scope = 'all_periods'",
        )
    ) == sorted(
        q(
            url,
            "select analysis_universe, svi_quartile_label, counties_hma_not_observed, "
            "counties_hma_observed_no_positive_obligation, counties_hma_obligated "
            "from serving.svi_quartile_summary",
        )
    )


def test_follow_up_screen_is_the_exact_rule(pipeline):
    url = pipeline["url"]
    rows = q(
        url,
        """
        select county_fips, damage_per_capita_real, svi_quartile, hma_per_capita_real,
               is_high_need_low_obligation
        from serving.county_resilience_profile where analysis_universe = 'states_dc'""",
    )
    damage = sorted(r[1] for r in rows if r[1] is not None)
    hma = sorted(r[3] for r in rows if r[3] is not None)
    median = (hma[(len(hma) - 1) // 2] + hma[len(hma) // 2]) / 2  # true median, exact Decimal

    def pct_rank(v):
        return sum(1 for x in damage if x < v) / (len(damage) - 1)

    for fips, dmg, quartile, per_capita, flag in rows:
        expected = (
            dmg is not None
            and pct_rank(dmg) >= 0.75
            and quartile == 4
            and per_capita is not None
            and per_capita <= median
        )
        assert flag is expected, fips
    # never outside states + DC
    assert (
        q1(
            url,
            "select count(*) from serving.county_resilience_profile "
            "where is_high_need_low_obligation and analysis_universe <> 'states_dc'",
        )
        == 0
    )


FUNNEL_CHECKS = (
    "hma_raw_status_preserved",
    "hma_positive_obligation_matches_source",
    "hma_period_boundaries",
    "hma_stage_exclusive_exhaustive",
    "hma_period_grid_complete",
    "hma_period_reconciles_profile",
    "hma_county_amounts_reconcile_fact",
    "hma_status_summary_reconciles",
    "hma_program_hazard_reconciles",
    "high_need_low_obligation_flag_exact",
    "hma_stage_summary_reconciles",
    "hma_geography_break_guard",
    "state_year_hma_reconciles",
    "hma_damage_burden_reconciles",
)
# storm / declaration history across the geography break (critical)
HISTORY_CHECKS = (
    "storm_declaration_geography_break_guard",
    "history_summaries_reconcile",
    "serving_damage_reconciles",
)

# a PA county turned into legacy geography: Pennsylvania then "has" a geography break with storm
# facts on the legacy unit, while the serving tables still treat every current PA unit as ordinary
PA_LEGACY_MUTATION = (
    "update curated.dim_county set analysis_universe = 'legacy_geography' "
    "where county_fips = (select min(county_fips) from curated.fact_storm_event_county "
    "where county_fips like '42%' and is_weather)"
)


def test_funnel_dq_checks_pass(pipeline):
    status = dict(
        q(
            pipeline["url"],
            "select distinct on (check_name) check_name, status from ops.dq_results "
            "order by check_name, id desc",
        )
    )
    for name in FUNNEL_CHECKS + HISTORY_CHECKS:
        assert status.get(name) == "pass", name
    # the fixture profile (PA, PR, VI) has no geography break: no unit is non-comparable
    assert status.get("storm_declaration_geography_not_comparable_units") == "pass"
    assert status.get("history_facts_on_noncomparable_geography") == "pass"


@pytest.mark.parametrize(
    "mutation,check",
    [
        (
            "update serving.hma_county_period_activity set hma_activity_stage = 'obligated' "
            "where county_fips = '72001'",
            "hma_stage_exclusive_exhaustive",
        ),
        (
            "update serving.county_resilience_profile set hma_activity_stage = 'not_observed' "
            "where county_fips = '72009'",
            "hma_stage_exclusive_exhaustive",
        ),
        (
            "delete from serving.hma_county_period_activity where county_fips = '72001'",
            "hma_period_grid_complete",
        ),
        (
            "update serving.county_resilience_profile set hma_obligated_usd = hma_obligated_usd + 1"
            " where county_fips = '72069'",
            "hma_period_reconciles_profile",
        ),
        (
            "update serving.county_resilience_profile set hma_obligated_usd = hma_obligated_usd + 1"
            " where county_fips = '72069'",
            "hma_county_amounts_reconcile_fact",
        ),
        (
            "update curated.fact_hma_allocation set status = 'Approved' where status = 'Pending'",
            "hma_raw_status_preserved",
        ),
        (
            "update staging.hma_subapplications set status = 'Approved' where status = 'Pending'",
            "hma_raw_status_preserved",
        ),
        (
            "update curated.dim_hma_period set last_fy_in_profile = 2023",
            "hma_period_boundaries",
        ),
        (
            "update serving.county_resilience_profile "
            "set is_high_need_low_obligation = not is_high_need_low_obligation "
            "where county_fips = (select min(county_fips) from serving.county_resilience_profile "
            "where analysis_universe = 'states_dc')",
            "high_need_low_obligation_flag_exact",
        ),
        (
            "update serving.hma_status_summary set subapplications = subapplications + 1 "
            "where status = 'Approved'",
            "hma_status_summary_reconciles",
        ),
        (
            "update serving.hma_county_program_hazard_activity "
            "set observed_subapplications = observed_subapplications + 1 "
            "where county_fips = '72069'",
            "hma_program_hazard_reconciles",
        ),
        # positive-obligation flag: curated flag flipped consistently (passes the table
        # constraint), and separately the raw OpenFEMA amount changed under unchanged staging
        (
            "update curated.fact_hma_allocation "
            "set has_positive_obligation = false, obligation_amount_class = 'zero' "
            "where hma_id = (select min(hma_id) from curated.fact_hma_allocation "
            "where has_positive_obligation)",
            "hma_positive_obligation_matches_source",
        ),
        (
            "update raw.openfema_records "
            "set record = jsonb_set(record, '{totalObligatedAmount}', '0'::jsonb) "
            "where dataset = 'HmaSubapplications' and record->>'id' = "
            "(select min(hma_id) from curated.fact_hma_allocation where has_positive_obligation)",
            "hma_positive_obligation_matches_source",
        ),
        # stage summary: a county moved between stages keeps counties = sum of stages, so only
        # the independent recount from the fact can detect it
        (
            "update serving.hma_stage_summary "
            "set counties_obligated = counties_obligated + 1, "
            "counties_not_observed = counties_not_observed - 1 "
            "where analysis_universe = 'puerto_rico' and period_scope = 'five_year_period'",
            "hma_stage_summary_reconciles",
        ),
        (
            "update serving.hma_stage_summary "
            "set counties_geography_not_comparable = counties_geography_not_comparable + 1, "
            "counties_not_observed = counties_not_observed - 1 "
            "where analysis_universe = 'puerto_rico' and period_scope = 'all_periods'",
            "hma_stage_summary_reconciles",
        ),
        # geography break: once a unit of PR is legacy geography holding HMA records, every
        # current PR unit with an ordinary stage must be caught
        (
            "update curated.dim_county set analysis_universe = 'legacy_geography' "
            "where county_fips = '72069'",
            "hma_geography_break_guard",
        ),
        (
            "update serving.state_year_summary set hma_obligated_usd = hma_obligated_usd + 1 "
            "where state_abbr = 'PR' and year = 2024",
            "state_year_hma_reconciles",
        ),
        (
            "update serving.hma_damage_burden_summary "
            "set counties_obligated = counties_obligated + 1, "
            "counties_not_observed = counties_not_observed - 1 "
            "where analysis_universe = 'puerto_rico' and damage_burden_quartile = 1",
            "hma_damage_burden_reconciles",
        ),
        # Puerto Rico's is_low_obligation is checked against Puerto Rico's own median
        (
            "update serving.county_resilience_profile "
            "set is_low_obligation = not is_low_obligation where county_fips = '72069'",
            "high_need_low_obligation_flag_exact",
        ),
        # storm / declaration geography break: once PA has a legacy unit holding storm facts,
        # every current PA unit with ordinary history (flags true, zeros, ranks) must be caught,
        # by the fact-triggered guard and by the recounts that re-derive comparability from
        # analysis_universe + state rather than reading the stored flag
        (PA_LEGACY_MUTATION, "storm_declaration_geography_break_guard"),
        (PA_LEGACY_MUTATION, "history_summaries_reconcile"),
        (PA_LEGACY_MUTATION, "hma_damage_burden_reconciles"),
        (PA_LEGACY_MUTATION, "serving_damage_reconciles"),
        # a false-zero history in a population denominator of the SVI summary
        (
            "update serving.svi_quartile_summary "
            "set history_comparable_population = history_comparable_population + 1 "
            "where analysis_universe = 'states_dc' and history_comparable_population is not null",
            "history_summaries_reconcile",
        ),
        (
            "update serving.hma_stage_summary set storm_damage_real_usd = "
            "coalesce(storm_damage_real_usd, 0) + 1 "
            "where analysis_universe = 'states_dc' and period_scope = 'all_periods'",
            "history_summaries_reconcile",
        ),
    ],
)
def test_funnel_dq_checks_catch_violations(pipeline, mutation, check):
    sql = {c["name"]: c for c in load_checks()}[check]["sql"]
    with psycopg.connect(pipeline["url"]) as c:
        try:
            assert int(c.execute(sql).fetchone()[0] or 0) == 0, "passes before the mutation"
            c.execute(mutation)
            assert int(c.execute(sql).fetchone()[0] or 0) > 0, "mutation not detected"
        finally:
            c.rollback()


# serving models that depend on curated.dim_county.hma_geography_comparable, in execution order
GEOGRAPHY_DEPENDENT_MODELS = (
    "31_county_resilience_profile.sql",
    "34_svi_quartile_summary.sql",
    "38_hma_county_period_activity.sql",
    "41_hma_stage_summary.sql",
    "42_hma_damage_burden_summary.sql",
)


def test_geography_not_comparable_unit_is_excluded(pipeline):
    """HMA mechanics in isolation: mark one current PA county as HMA-non-comparable, rebuild the
    dependent serving tables inside a transaction (rolled back), and check that the unit leaves
    every HMA denominator, rank, median and the follow-up screen, stays non-comparable in every
    period, and that every funnel check still passes. This flips only the HMA flag; it is not a
    full Connecticut simulation. A real geography break also removes the unit's storm and
    declaration history and its damage rank (the earlier assertion that the damage rank stays
    non-null described the old, wrong rule); that is tested end to end in
    test_storm_declaration_geography_break_end_to_end."""
    checks = {c["name"]: c for c in load_checks()}
    quartile_sql = (
        "select s.counties, s.counties_hma_comparable, s.counties_hma_geography_not_comparable "
        "from serving.svi_quartile_summary s join serving.county_resilience_profile p "
        "using (analysis_universe, svi_quartile_label) where p.county_fips = %s"
    )
    with psycopg.connect(pipeline["url"]) as c:
        try:
            fips = c.execute(
                "select min(county_fips) from serving.county_resilience_profile "
                "where state_abbr = 'PA'"
            ).fetchone()[0]
            ranked_before = c.execute(
                "select count(hma_pctile) from serving.county_resilience_profile "
                "where analysis_universe = 'states_dc'"
            ).fetchone()[0]
            before = c.execute(quartile_sql, (fips,)).fetchone()
            assert before[2] == 0
            c.execute(
                "update curated.dim_county set hma_geography_comparable = false "
                "where county_fips = %s",
                (fips,),
            )
            for name in GEOGRAPHY_DEPENDENT_MODELS:
                run_sql_file(c, SQL_DIR / "serving" / name)

            stage, per_capita, pctile, low, flag = c.execute(
                "select hma_activity_stage, hma_per_capita_real, hma_pctile, is_low_obligation, "
                "is_high_need_low_obligation "
                "from serving.county_resilience_profile where county_fips = %s",
                (fips,),
            ).fetchone()
            assert stage == "geography_not_comparable"
            assert (per_capita, pctile, low, flag) == (None, None, None, False)
            ranked_after = c.execute(
                "select count(hma_pctile) from serving.county_resilience_profile "
                "where analysis_universe = 'states_dc'"
            ).fetchone()[0]
            assert ranked_after == ranked_before - 1  # out of the HMA rank and median
            n_periods = c.execute("select count(*) from curated.dim_hma_period").fetchone()[0]
            assert c.execute(
                "select count(*), count(*) filter (where hma_activity_stage = "
                "'geography_not_comparable' and obligated_real_per_capita is null) "
                "from serving.hma_county_period_activity where county_fips = %s",
                (fips,),
            ).fetchone() == (n_periods, n_periods)
            # still counted as a county, but not in the Q3 denominator
            assert c.execute(quartile_sql, (fips,)).fetchone() == (before[0], before[1] - 1, 1)
            assert (
                c.execute(
                    "select sum(counties_geography_not_comparable) from serving.hma_stage_summary "
                    "where period_scope = 'all_periods'"
                ).fetchone()[0]
                == 1
            )
            assert (
                c.execute(
                    "select sum(counties_excluded_geography_not_comparable) "
                    "from serving.hma_damage_burden_summary"
                ).fetchone()[0]
                == 1
            )
            for name in FUNNEL_CHECKS + HISTORY_CHECKS:
                assert int(c.execute(checks[name]["sql"]).fetchone()[0] or 0) == 0, name
            sql = checks["hma_geography_not_comparable_units"]["sql"]
            assert c.execute(sql).fetchone()[0] == 1
        finally:
            c.rollback()


# models rebuilt for a full storm / declaration geography break, in execution order
HISTORY_DEPENDENT_MODELS = (
    ("curated", "10_dim_county.sql"),
    ("serving", "30_event_declaration_coverage.sql"),
) + tuple(("serving", name) for name in GEOGRAPHY_DEPENDENT_MODELS)

# historical storm / declaration columns that must be NULL (not zero) without comparable history
HISTORY_COLUMNS = (
    "storm_events",
    "damage_total_usd",
    "damage_total_real_usd",
    "damage_property_usd",
    "damage_crops_usd",
    "deaths",
    "injuries",
    "significant_events",
    "significant_events_covered",
    "significant_damage_usd",
    "covered_damage_usd",
    "major_disasters",
    "emergencies",
    "damage_per_capita",
    "damage_per_capita_real",
    "coverage_rate",
    "damage_pctile",
    "is_high_loss",
)


def test_storm_declaration_geography_break_end_to_end(pipeline):
    """The fixture has no Connecticut, so simulate the 2022 break the way the national data shows
    it: add a legacy Pennsylvania county to the county reference, move one real storm-event fact
    onto it, and rebuild curated.dim_county (the data-driven rule) and the dependent serving
    tables inside a rolled-back transaction. Every current PA unit must then lose its storm and
    declaration history (NULL, not zero), its damage rank, its place in SVI-quartile damage and
    coverage rates and in the follow-up screen, while keeping SVI and population; the legacy unit
    keeps its real history; the critical checks pass; and a disguised false-zero history is
    caught."""
    checks = {c["name"]: c for c in load_checks()}

    def failing(conn, name):
        return int(conn.execute(checks[name]["sql"]).fetchone()[0] or 0)

    with psycopg.connect(pipeline["url"]) as c:
        try:
            pa_current, pa_svi, pa_pop = c.execute(
                "select count(*), count(svi_overall), count(population_base) "
                "from serving.county_resilience_profile "
                "where state_abbr = 'PA' and analysis_universe = 'states_dc'"
            ).fetchone()
            pr_ranked = c.execute(
                "select count(damage_pctile) from serving.county_resilience_profile "
                "where analysis_universe = 'puerto_rico'"
            ).fetchone()[0]
            assert pa_current == 67 and pa_svi == 67 and pa_pop == 67
            c.execute(
                "insert into staging.county_ref (county_fips, state_fips, state_abbr, state_name, "
                "county_name, name_key, in_popest_2025, in_reference_2020) "
                "select '42999', state_fips, state_abbr, state_name, 'Legacy Test County', "
                "'LEGACY TEST', false, true from staging.county_ref "
                "where state_abbr = 'PA' and in_popest_2025 limit 1"
            )
            run_sql_file(c, SQL_DIR / "curated" / "10_dim_county.sql")
            moved = c.execute(
                "update curated.fact_storm_event_county set county_fips = '42999' "
                "where (event_id, county_fips) = (select event_id, county_fips "
                "from curated.fact_storm_event_county where county_fips like '42%' and is_weather "
                "order by event_id, county_fips limit 1)"
            ).rowcount
            assert moved == 1
            for layer, name in HISTORY_DEPENDENT_MODELS[1:]:
                run_sql_file(c, SQL_DIR / layer / name)

            # the data-driven rule: every current PA unit, and only those, is non-comparable
            assert c.execute(
                "select analysis_universe, storm_declaration_geography_comparable, "
                "hma_geography_comparable, count(*) from curated.dim_county "
                "where state_abbr = 'PA' group by 1, 2, 3 order by 1"
            ).fetchall() == [("legacy_geography", True, True, 1), ("states_dc", False, False, 67)]
            assert (
                c.execute(
                    "select count(*) from curated.dim_county where state_abbr <> 'PA' "
                    "and not (storm_declaration_geography_comparable and hma_geography_comparable)"
                ).fetchone()[0]
                == 0
            )

            # current PA units: history NULL (never zero), no rank, no screen; SVI/pop kept
            nonnull = " + ".join(f"({col} is not null)::int" for col in HISTORY_COLUMNS)
            row = c.execute(
                f"select count(*), coalesce(sum({nonnull}), 0), count(svi_overall), "
                "count(population_base), count(*) filter (where is_high_need_low_obligation), "
                "count(*) filter (where storm_declaration_geography_comparable), "
                "count(*) filter (where hma_activity_stage = 'geography_not_comparable') "
                "from serving.county_resilience_profile "
                "where state_abbr = 'PA' and analysis_universe = 'states_dc'"
            ).fetchone()
            assert row == (67, 0, 67, 67, 0, 0, 67)
            # the legacy unit keeps the real pre-change history attributed to it
            assert c.execute(
                "select analysis_universe, storm_declaration_geography_comparable, storm_events "
                "from serving.county_resilience_profile where county_fips = '42999'"
            ).fetchone() == ("legacy_geography", True, 1)
            # damage ranks: nothing left to rank in states + DC; Puerto Rico untouched
            assert c.execute(
                "select count(damage_pctile) filter (where analysis_universe = 'states_dc'), "
                "count(damage_pctile) filter (where analysis_universe = 'puerto_rico') "
                "from serving.county_resilience_profile"
            ).fetchone() == (0, pr_ranked)
            assert (
                c.execute(
                    "select count(*) from serving.hma_county_period_activity "
                    "where state_abbr = 'PA' and analysis_universe = 'states_dc' "
                    "and storm_damage_real_usd_same_years is not null"
                ).fetchone()[0]
                == 0
            )
            # SVI summary: the units stay counted (population kept), but in no damage or coverage
            # numerator or denominator
            assert c.execute(
                "select sum(counties), sum(population) > 0, sum(counties_history_comparable), "
                "sum(counties_history_not_comparable), count(history_comparable_population), "
                "count(damage_total_real_usd), count(damage_per_capita_real), count(coverage_rate) "
                "from serving.svi_quartile_summary where analysis_universe = 'states_dc'"
            ).fetchone() == (67, True, 0, 67, 0, 0, 0, 0)
            # damage-burden quartiles: no unranked unit sneaks in
            assert (
                c.execute(
                    "select count(*) from serving.hma_damage_burden_summary "
                    "where analysis_universe = 'states_dc'"
                ).fetchone()[0]
                == 0
            )

            # the critical checks hold on the broken geography, and the guard is non-trivial
            critical = FUNNEL_CHECKS + HISTORY_CHECKS + ("coverage_significance_in_real_dollars",)
            for name in critical:
                assert failing(c, name) == 0, name
            fails, total = c.execute(
                checks["storm_declaration_geography_break_guard"]["sql"]
            ).fetchone()
            assert int(fails) == 0 and int(total) >= 67
            info = checks["storm_declaration_geography_not_comparable_units"]["sql"]
            assert c.execute(info).fetchone()[0] == 67
            # PA facts still on current units are withheld from the profile: reported (warn) and
            # reconciled (serving_damage_reconciles passed above)
            assert failing(c, "history_facts_on_noncomparable_geography") > 0

            # mutations, each rolled back to a savepoint
            unit = c.execute(
                "select min(county_fips) from serving.county_resilience_profile "
                "where state_abbr = 'PA' and analysis_universe = 'states_dc'"
            ).fetchone()[0]
            mutations = [
                # a false-zero history disguised as comparable in the profile
                (
                    "update serving.county_resilience_profile "
                    "set storm_declaration_geography_comparable = true, storm_events = 0, "
                    f"damage_total_usd = 0, damage_total_real_usd = 0 where county_fips = '{unit}'",
                    "storm_declaration_geography_break_guard",
                ),
                # the stored dimension flag wrongly set back to comparable
                (
                    "update curated.dim_county set storm_declaration_geography_comparable = true "
                    "where state_abbr = 'PA' and analysis_universe = 'states_dc'",
                    "storm_declaration_geography_break_guard",
                ),
                # a damage rank (and high-loss flag) given back to a non-comparable unit; the flag
                # is flipped too so that the table constraint does not refuse it first
                (
                    "update serving.county_resilience_profile "
                    "set storm_declaration_geography_comparable = true, damage_pctile = 0.9, "
                    f"is_high_loss = true where county_fips = '{unit}'",
                    "storm_declaration_geography_break_guard",
                ),
                # the false-zero population put back into the damage denominator
                (
                    "update serving.svi_quartile_summary "
                    "set history_comparable_population = population, "
                    "counties_history_comparable = counties, counties_history_not_comparable = 0 "
                    "where analysis_universe = 'states_dc'",
                    "history_summaries_reconcile",
                ),
            ]
            for mutation, name in mutations:
                c.execute("savepoint m")
                c.execute(mutation)
                assert failing(c, name) > 0, (name, mutation)
                c.execute("rollback to savepoint m")
            # the profile's own constraint refuses a plain false zero
            c.execute("savepoint m")
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(
                    "update serving.county_resilience_profile set storm_events = 0 "
                    f"where county_fips = '{unit}'"
                )
            c.execute("rollback to savepoint m")
        finally:
            c.rollback()


def test_constant_dollars(pipeline):
    url = pipeline["url"]
    # real BLS values in the fixture response
    cpi, basis = q(url, "select cpi, cpi_basis from staging.cpi_annual where year = 2025")[0]
    assert (float(cpi), basis) == (321.943, "bls_published_annual_average")
    assert q(
        url,
        "select value_raw, index_value from staging.cpi_monthly where year = 2025 and month = 10",
    ) == [("-", None)]
    assert (
        float(
            q1(
                url,
                "select factor_to_base from curated.dim_inflation "
                "where period_type = 'calendar_year' and period = 2025",
            )
        )
        == 1.0
    )
    f2024 = float(
        q1(
            url,
            "select factor_to_base from curated.dim_inflation "
            "where period_type = 'calendar_year' and period = 2024",
        )
    )
    assert f2024 == pytest.approx(321.943 / 313.689)
    nominal, real = q(
        url,
        "select sum(damage_total_usd), sum(damage_total_real_usd) "
        "from curated.fact_storm_event_county",
    )[0]
    assert float(real) == pytest.approx(float(nominal) * f2024)  # all fixture events are 2024
    assert (
        q1(url, "select count(*) from curated.fact_hma_allocation where obligated_real_usd is null")
        == 0
    )
    assert (
        q1(
            url,
            "select count(*) from raw.bls_cpi_records r left join ops.source_objects o "
            "on o.object_key = r._object_key where o.object_key is null",
        )
        == 0
    )


def test_zone_mapping_is_date_effective_on_real_events(pipeline):
    url = pipeline["url"]
    rows = q(
        url,
        """
        select r.snapshot_file, min(r.event_date), max(r.event_date)
        from staging.storm_event_zone_resolution r
        join staging.nws_zone_snapshot s using (snapshot_file)
        where r.match_method in ('zone_effective_snapshot', 'zone_gap_snapshot_name_verified')
        group by 1 order by 1""",
    )
    # events through 9 Sep 2024 use bp05mr24; from 10 Sep 2024 bp10se24
    by = {r[0]: (r[1].isoformat(), r[2].isoformat()) for r in rows}
    assert by["bp05mr24"][1] <= "2024-09-09" and by["bp10se24"][0] >= "2024-09-10"
    assert (
        q1(
            url,
            """select count(*) from staging.storm_event_zone_resolution r
                      join staging.nws_zone_snapshot s using (snapshot_file)
                      where r.match_method = 'zone_effective_snapshot'
                        and r.event_date not between s.effective_date and s.valid_to""",
        )
        == 0
    )
    # the fixture omits bp18mr25, so bp10se24 sits in a gap: real NOAA zones PA105/PA106
    # ("UPPER/LOWER BUCKS") do not match its zone name "Bucks" and stay unmapped, not guessed
    assert set(
        q(
            url,
            """select state_zone, unmapped_reason from staging.storm_event_zone_resolution
                         where match_method is null""",
        )
    ) == {
        ("PA105", "gap_version_name_mismatch_and_no_same_name_version"),
        ("PA106", "gap_version_name_mismatch_and_no_same_name_version"),
    }
    tiers = dict(q(url, "select snapshot_file, provenance_tier from staging.nws_zone_snapshot"))
    assert tiers["bp04ap17"] == "internet_archive" and tiers["bp05mr24"] == "nws"


def test_no_critical_dq_failures(pipeline):
    assert (
        q1(
            pipeline["url"],
            "select count(*) from ops.dq_results where severity='critical' "
            "and status in ('fail','error')",
        )
        == 0
    )


def test_rerun_is_idempotent(pipeline):
    url = pipeline["url"]
    before = q(
        url,
        "select (select count(*) from raw.noaa_storm_events_details),"
        " (select count(*) from raw.openfema_records),"
        " (select count(*) from raw.cdc_svi_county)",
    )[0]
    fp_before = q1(url, SERVING_FINGERPRINT)
    marker = q1(url, "select now()")
    assert main(["--profile", "fixture", "ingest", "--offline-dir", str(FIXTURES)]) == 0
    statuses = q(
        url,
        f"select distinct status from ops.pipeline_runs "
        f"where step like 'ingest:%' and started_at >= '{marker}'",
    )
    assert statuses == [("unchanged",)]
    assert main(["--profile", "fixture", "transform"]) == 0
    after = q(
        url,
        "select (select count(*) from raw.noaa_storm_events_details),"
        " (select count(*) from raw.openfema_records),"
        " (select count(*) from raw.cdc_svi_county)",
    )[0]
    assert before == after
    assert q1(url, SERVING_FINGERPRINT) == fp_before
