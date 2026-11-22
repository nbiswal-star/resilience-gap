"""Statewide vs tribal declared areas, hazard compatibility and the real-dollar significance rule.

Models under test: sql/staging/06_fema_declarations.sql, sql/curated/21_fact_declaration_area.sql,
sql/serving/30_event_declaration_coverage.sql, and the DQ checks that guard them.

Declarations are REAL OpenFEMA records (tests/fixtures/openfema_examples/: every code-000 record
of DR-1485-PA and DR-4669-AS, literal "Statewide", and of four declarations for tribal areas).
The storm events below are labelled TEST VECTORS for the matching rules, not project data.
"""

import json

import pytest

from resgap.config import SQL_DIR
from resgap.db import run_sql_file
from resgap.dq.runner import load_checks
from resgap.transform import load_seeds
from tests.conftest import FIXTURES

pytestmark = pytest.mark.integration

EXAMPLES = (
    FIXTURES
    / "openfema_examples"
    / "DisasterDeclarationsSummaries"
    / "code000-statewide-and-tribal.json"
)
CHECKS = {c["name"]: c for c in load_checks()}

SETUP = """
create table staging.county_ref (county_fips text primary key);
insert into staging.county_ref values ('42001'), ('31173'), ('12011');
create table curated.dim_county (county_fips text primary key, county_name text,
  state_abbr text, svi_overall numeric, svi_quartile int, svi_quartile_label text,
  analysis_universe text, fema_region int);
insert into curated.dim_county values
  ('42001', 'Adams County', 'PA', 0.2, 1, 'Q1 (least vulnerable)', 'states_dc', 3),
  ('31173', 'Thurston County', 'NE', 0.9, 4, 'Q4 (most vulnerable)', 'states_dc', 7),
  ('12011', 'Broward County', 'FL', 0.6, 3, 'Q3', 'states_dc', 4);
insert into ops.active_profile (profile, start_year, end_year, states, significant_damage_real_usd,
  significant_casualties, match_days_before, match_days_after, open_incident_cap_days,
  cpi_base_year, cpi_series_id, zone_gap_threshold_days)
values ('test', 2000, 2025, null, 100000, 1, 1, 1, 60, 2025, 'CUUR0000SA0', 400);
"""

# event_id, county, state, date, NOAA type, hazard family, nominal $, real $, casualties
EVENTS = [
    # Omaha Indian Reservation (DR-4013-NE, Flood, 2011-05-24..08-01) lies in Thurston County,
    # but a tribal-area designation is not a county or statewide designation
    (1, "31173", "NE", "2011-06-15", "Flood", "Flood", 400000, 500000, 0),
    # Seminole reservations (DR-4844-FL, Hurricane, 2024-10-05..11-02), tribal request
    (2, "12011", "FL", "2024-10-10", "Hurricane", "Tropical Cyclone", 1e6, 1.02e6, 0),
    # DR-1485-PA is a literal "Statewide" Severe Storm declaration (2003-07-21..09-12)
    (3, "42001", "PA", "2003-08-04", "Flash Flood", "Flood", 200000, 300000, 0),
    (4, "42001", "PA", "2003-08-05", "Heat", "Heat", 0, 0, 2),  # incompatible hazard
    (5, "42001", "PA", "2003-08-06", "Hail", "Severe Storm & Wind", 120000, 95000, 0),
    (6, "42001", "PA", "2003-08-07", "Thunderstorm Wind", "Severe Storm & Wind", 90000, 130000, 0),
    (7, "42001", "PA", "2003-10-01", "Flood", "Flood", 500000, 800000, 0),  # after the window
]


def _check(conn, name):
    with conn.cursor() as cur:
        cur.execute(CHECKS[name]["sql"])
        failing, total = cur.fetchone()
    return int(failing or 0), int(total or 0)


@pytest.fixture()
def declared(conn):
    doc = json.loads(EXAMPLES.read_bytes())
    with conn.cursor() as cur:
        cur.execute(SETUP)
        for rec in doc["DisasterDeclarationsSummaries"]:
            cur.execute(
                """insert into raw.openfema_records (dataset, version, scope_key, record_id,
                     record, record_sha256, _object_key, _page, _run_id)
                   values ('DisasterDeclarationsSummaries', 'v2', 'examples', %s, %s, 'x',
                           'tests/fixtures/openfema_examples', 1, gen_random_uuid())""",
                (rec["id"], json.dumps(rec)),
            )
    load_seeds(conn)
    for rel in (
        "staging/06_fema_declarations.sql",
        "curated/11_dim_hazard.sql",
        "curated/21_fact_declaration_area.sql",
    ):
        run_sql_file(conn, SQL_DIR / rel)
    conn.commit()
    return conn


@pytest.fixture()
def coverage(declared):
    conn = declared
    with conn.cursor() as cur:
        cur.execute(
            """create table curated.fact_storm_event_county (event_id bigint, county_fips text,
                 state_abbr text, event_date date, event_year int, event_type text,
                 hazard_family text, is_weather boolean, damage_total_usd numeric,
                 damage_total_real_usd numeric, deaths numeric, injuries numeric,
                 event_casualties int)"""
        )
        for e in EVENTS:
            eid, county, st, day, etype, fam, nominal, real, cas = e
            cur.execute(
                """insert into curated.fact_storm_event_county values (%s, %s, %s, %s,
                   extract(year from %s::date), %s, %s, true, %s, %s, %s, 0, %s)""",
                (eid, county, st, day, day, etype, fam, nominal, real, cas, cas),
            )
    run_sql_file(conn, SQL_DIR / "serving" / "30_event_declaration_coverage.sql")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            "select event_id, covered_by_major_disaster, matched_declaration, matched_area_level,"
            " matched_hazard_rule from serving.event_declaration_coverage"
        )
        return {r[0]: r[1:] for r in cur.fetchall()}


def test_only_literal_statewide_is_statewide(declared):
    with declared.cursor() as cur:
        cur.execute(
            "select fema_declaration_string, area_level, county_fips, designated_area,"
            " tribal_request from staging.fema_declarations"
        )
        rows = cur.fetchall()
    assert len(rows) == 15
    statewide = {r[0] for r in rows if r[1] == "statewide"}
    assert statewide == {"DR-1485-PA", "DR-4669-AS"}
    tribal = [r for r in rows if r[1] == "tribal"]
    assert len(tribal) == 13
    assert all(r[2] is None for r in rows)  # nothing is expanded to a county
    names = {r[3] for r in tribal}
    assert {"Omaha Indian Reservation", "Chickasaw (OTSA)", "Crooked Creek (ANV/ANVSA)"} <= names
    # tribal classification does not depend on tribalRequest (DR-4013-NE has it false)
    assert ("DR-4013-NE", "tribal", None, "Omaha Indian Reservation", False) in rows


def test_curated_keeps_region_and_tribal_fields(declared):
    with declared.cursor() as cur:
        cur.execute(
            "select fema_declaration_string, fema_region, area_level, tribal_request, place_code"
            " from curated.fact_declaration_area where designated_area = 'Hollywood Indian"
            " Reservation'"
        )
        assert cur.fetchone() == ("DR-4844-FL", 4, "tribal", True, "32125")
        cur.execute(
            "select fema_region from curated.fact_declaration_area"
            " where fema_declaration_string = 'DR-1485-PA'"
        )
        assert cur.fetchone() == (3,)


@pytest.mark.parametrize(
    "area,code,place,tribal,known,expected",
    [
        ("Statewide", "000", "0", False, False, "statewide"),
        ("  STATEWIDE ", "000", "0", False, False, "statewide"),
        ("Omaha Indian Reservation", "000", "37035", False, False, "tribal"),
        ("Hollywood Indian Reservation", "000", "32125", True, False, "tribal"),
        ("Somewhere", "000", "0", False, False, "unresolved"),  # code 000 is not statewide
        ("Somewhere", "000", None, True, False, "tribal"),
        ("Adams (County)", "001", "99001", False, True, "county"),
        ("Tripp (County)", "123", "99123", True, True, "county"),
        ("Bedford", "515", "99515", False, False, "unresolved"),
        ("Statewide (except X)", "000", "0", False, False, "unresolved"),
    ],
)
def test_area_level_function(conn, area, code, place, tribal, known, expected):
    with conn.cursor() as cur:
        cur.execute(
            "select staging.fema_area_level(%s, %s, %s, %s, %s)", (area, code, place, tribal, known)
        )
        assert cur.fetchone()[0] == expected


def test_tribal_designation_never_covers_a_county(coverage):
    assert coverage[1] == (False, None, None, None)
    assert coverage[2] == (False, None, None, None)


def test_literal_statewide_covers_compatible_hazards_in_window(coverage):
    assert coverage[3] == (True, "DR-1485-PA", "statewide", "companion_hazard")
    assert coverage[6] == (True, "DR-1485-PA", "statewide", "same_family")
    assert coverage[4][0] is False  # Heat has no compatible declaration type
    assert coverage[7][0] is False  # outside the incident window (+1 day)


def test_significance_uses_constant_dollars(coverage):
    assert 5 not in coverage  # $120k nominal but $95k in base-year dollars
    assert 6 in coverage  # $90k nominal but $130k in base-year dollars


def test_dq_checks_pass_on_correct_rules(coverage, declared):
    for name in (
        "declarations_statewide_only_when_literal",
        "declarations_non_county_areas_not_expanded",
        "coverage_only_county_or_literal_statewide",
        "coverage_significance_in_real_dollars",
    ):
        assert _check(declared, name)[0] == 0, name


def test_dq_checks_catch_the_old_code000_rule(coverage, declared):
    conn = declared
    with conn.cursor() as cur:
        # reintroduce the old bug: fipsCountyCode '000' => statewide
        cur.execute("update staging.fema_declarations set area_level = 'statewide'")
        cur.execute("update curated.fact_declaration_area set area_level = 'statewide'")
    assert _check(conn, "declarations_statewide_only_when_literal") == (13, 15)
    run_sql_file(conn, SQL_DIR / "serving" / "30_event_declaration_coverage.sql")
    failing, _ = _check(conn, "coverage_only_county_or_literal_statewide")
    assert failing >= 2  # the NE and FL events are now "covered" through tribal areas
    assert CHECKS["coverage_only_county_or_literal_statewide"]["severity"] == "critical"
    assert CHECKS["declarations_statewide_only_when_literal"]["severity"] == "critical"
