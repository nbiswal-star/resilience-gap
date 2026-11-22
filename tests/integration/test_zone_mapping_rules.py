"""Logic tests for the date-effective zone -> county mapping.

Model under test: sql/staging/05_storm_event_county.sql.

The inputs below are TEST VECTORS for the SQL rules (not project data): two invented crosswalk
versions of one zone and a handful of events placed around them, so every rule and every
unmapped reason is exercised deterministically. Real-data behaviour is covered by
test_pipeline_offline.py on the committed fixture excerpts.
"""

import pytest

from resgap.config import SQL_DIR
from resgap.db import run_sql_file

pytestmark = pytest.mark.integration

SETUP = """
create table staging.county_ref (state_fips text, state_abbr text);
insert into staging.county_ref values ('42', 'PA');
create table staging.storm_events (event_id bigint, cz_type text, is_marine_area boolean,
  cz_fips int, census_state_fips int, begin_local timestamp, cz_name text);
create table staging.nws_zone_snapshot (snapshot_file text, effective_date date, valid_to date,
  in_gap boolean, provenance_tier text);
create table staging.nws_zone_county_version (snapshot_file text, state_zone text,
  county_fips text, zone_name_key text);
-- version A (2010-01-01 .. 2014-12-31) is followed by a 5-year gap; version B from 2015.
insert into staging.nws_zone_snapshot values
  ('vA', '2010-01-01', '2014-12-31', true,  'internet_archive'),
  ('vB', '2015-01-01', '9999-12-31', false, 'nws');
-- zone PA001 "Ridge" covers two counties in A; in B the number 001 is reused for "Valley".
insert into staging.nws_zone_county_version values
  ('vA', 'PA001', '42001', 'RIDGE'), ('vA', 'PA001', '42003', 'RIDGE'),
  ('vB', 'PA001', '42005', 'VALLEY'),
  ('vB', 'PA002', '42007', 'RIDGE');
insert into staging.storm_events values
  (1, 'Z', false, 1, 42, '2016-06-01', 'VALLEY'),     -- in-effect B, authoritative
  (2, 'Z', false, 1, 42, '2012-06-01', 'RIDGE'),      -- in-effect A (gap) + name agrees
  (3, 'Z', false, 1, 42, '2012-06-01', 'VALLEY'),     -- A gap, other name -> nearest = B
  (4, 'Z', false, 1, 42, '2005-06-01', 'RIDGE'),      -- before A -> nearest same name = A
  (5, 'Z', false, 1, 42, '2005-06-01', 'MOUNTAIN'),   -- before first version, no same name
  (6, 'Z', false, 9, 42, '2016-06-01', 'NOWHERE'),    -- zone code in no version
  (7, 'Z', false, 2, 42, '2012-06-01', 'LAKE'),       -- not in in-effect A, no same name
  (8, 'C', false, 1, 42, '2016-06-01', 'ANY COUNTY'), -- county-based event
  (9, 'Z', true,  1, 42, '2016-06-01', 'MARINE');     -- marine: excluded
"""


@pytest.fixture()
def mapped(conn):
    with conn.cursor() as cur:
        cur.execute(SETUP)
    run_sql_file(conn, SQL_DIR / "staging" / "05_storm_event_county.sql")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            "select event_id, snapshot_file, match_method, unmapped_reason "
            "from staging.storm_event_zone_resolution order by event_id"
        )
        res = {r[0]: r[1:] for r in cur.fetchall()}
        cur.execute(
            "select event_id, county_fips, allocation_weight, crosswalk_tier "
            "from staging.storm_event_county order by 1, 2"
        )
        counties = cur.fetchall()
    return res, counties


def test_each_rule_and_reason(mapped):
    res, _ = mapped
    assert res[1] == ("vB", "zone_effective_snapshot", None)
    assert res[2] == ("vA", "zone_gap_snapshot_name_verified", None)
    assert res[3] == ("vB", "zone_nearest_snapshot_name_verified", None)
    assert res[4] == ("vA", "zone_nearest_snapshot_name_verified", None)
    assert res[5] == (None, None, "before_first_version_and_no_same_name_version")
    assert res[6] == (None, None, "zone_code_in_no_crosswalk_version")
    assert res[7] == (None, None, "zone_not_in_effective_version_and_no_same_name_version")
    assert 8 not in res and 9 not in res  # county events and marine events are not zone-resolved


def test_counties_come_only_from_the_chosen_version(mapped):
    _, counties = mapped
    by_event = {}
    for ev, fips, w, tier in counties:
        by_event.setdefault(ev, []).append((fips, float(w), tier))
    assert by_event[1] == [("42005", 1.0, "nws")]  # B, not A's counties
    assert by_event[2] == [("42001", 0.5, "internet_archive"), ("42003", 0.5, "internet_archive")]
    assert by_event[8] == [("42001", 1.0, None)]  # county-based
    assert not ({5, 6, 7, 9} & set(by_event))  # never guessed


def test_zone_name_key_equates_only_cosmetic_variants(conn):
    pairs = [
        ("N ELKO CNTY", "Northern Elko County", True),
        ("ST. MARYS", "Saint Marys", True),
        ("NORTHERN COLLETON", "Inland Colleton", False),
        ("UPPER BUCKS", "Bucks", False),
    ]
    with conn.cursor() as cur:
        for a, b, same in pairs:
            cur.execute("select staging.zone_name_key(%s) = staging.zone_name_key(%s)", (a, b))
            assert cur.fetchone()[0] is same, (a, b)


def test_nws_file_date(conn):
    with conn.cursor() as cur:
        cur.execute(
            "select staging.nws_file_date('bp16ap26'), staging.nws_file_date('bp01jn10'),"
            " staging.nws_file_date('bp28de04'), staging.nws_file_date('nonsense')"
        )
        a, b, c, d = cur.fetchone()
    assert (a.isoformat(), b.isoformat(), c.isoformat(), d) == (
        "2026-04-16",
        "2010-06-01",
        "2004-12-28",
        None,
    )
