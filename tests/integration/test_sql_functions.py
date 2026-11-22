import pytest

pytestmark = pytest.mark.integration


def one(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", None),
        (None, None),
        ("0.00K", 0),
        ("1.00K", 1000),
        ("2.5M", 2_500_000),
        ("1.2B", 1_200_000_000),
        ("15", 15),
        (".5K", 500),
        ("10k", 10_000),
        ("abc", None),
        ("1.5H", None),
    ],
)
def test_parse_damage(conn, raw, expected):
    v = one(conn, "select staging.parse_damage(%s)", raw)
    assert (v is None and expected is None) or float(v) == expected


@pytest.mark.parametrize(
    "a,b",
    [
        ("St. Mary's County", "SAINT MARYS"),
        ("Allegheny (County)", "ALLEGHENY COUNTY"),
        ("DeKalb County", "De Kalb"),
        ("Ste. Genevieve County", "Sainte Genevieve"),
        ("Baltimore (Independent City)", "Baltimore city"),
        ("Miami-Dade County", "MIAMI DADE"),
        ("Orleans Parish", "ORLEANS"),
        ("Capitol Planning Region", "Capitol"),
        # accents fold to ASCII; the Census reference file's double-encoded names are repaired
        ("Añasco Municipio", "Anasco"),
        ("AÃ±asco Municipio", "ANASCO"),
        ("MayagÃ¼ez Municipio", "Mayaguez"),
        ("RÃ­o Grande Municipio", "Rio Grande"),
        ("CanÃ³vanas Municipio", "Canóvanas"),
        ("Doña Ana County", "DONA ANA"),
    ],
)
def test_county_name_key_matches_variants(conn, a, b):
    assert one(conn, "select staging.county_name_key(%s)", a) == one(
        conn, "select staging.county_name_key(%s)", b
    )


def test_county_name_key_keeps_independent_cities_distinct(conn):
    assert one(conn, "select staging.county_name_key('Richmond city')") != one(
        conn, "select staging.county_name_key('Richmond County')"
    )


def test_storm_ts_rejects_impossible_dates(conn):
    assert one(conn, "select staging.storm_ts('202402','30','1200')") is None
    assert (
        one(conn, "select staging.storm_ts('202402','29','2359')").isoformat()
        == "2024-02-29T23:59:00"
    )
    assert one(conn, "select staging.storm_ts('202413','01','0000')") is None


def test_iso_date_ignores_session_time_zone(conn):
    conn.execute("set time zone 'America/New_York'")
    assert (
        one(conn, "select staging.iso_date('2024-09-11T00:00:00.000Z')").isoformat() == "2024-09-11"
    )


@pytest.mark.parametrize(
    "fiscal_year,period_start",
    [
        (1996, 1996),
        (2000, 1996),  # FY2000 is the last year of FY1996-2000 (a one-year period in the profile)
        (2001, 2001),
        (2005, 2001),
        (2006, 2006),
        (2020, 2016),
        (2021, 2021),
        (2024, 2021),
        (2025, 2021),
        (2026, 2026),
        (None, None),
    ],
)
def test_hma_period_start_fixed_boundaries(conn, fiscal_year, period_start):
    assert one(conn, "select staging.hma_period_start(%s::int)", fiscal_year) == period_start


def test_try_casts_never_raise(conn):
    assert one(conn, "select staging.try_int('12a')") is None
    assert one(conn, "select staging.try_numeric('-1.5e3')") == -1500
