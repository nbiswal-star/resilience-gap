from resgap.ingest.noaa_storm_events import parse_listing
from resgap.ingest.nws_zone_county import parse_landing
from resgap.ingest.rawload import sanitize_header

NOAA_PATTERN = r"StormEvents_details-ftp_v1\.0_d(?P<year>\d{4})_c(?P<created>\d{8})\.csv\.gz"


def test_listing_picks_newest_revision_per_year():
    html = """
    <a href="StormEvents_details-ftp_v1.0_d2024_c20260323.csv.gz">x</a>
    <a href="StormEvents_details-ftp_v1.0_d2024_c20260728.csv.gz">x</a>
    <a href="StormEvents_fatalities-ftp_v1.0_d2024_c20260728.csv.gz">x</a>
    <a href="StormEvents_details-ftp_v1.0_d2023_c20260323.csv.gz">x</a>
    """
    files = parse_listing(html, NOAA_PATTERN)
    assert files == {
        2024: "StormEvents_details-ftp_v1.0_d2024_c20260728.csv.gz",
        2023: "StormEvents_details-ftp_v1.0_d2023_c20260323.csv.gz",
    }


def test_nws_landing_ignores_files_only_mentioned_in_text():
    # Mirrors the real page: a retired file is named in prose, current files are linked.
    html = """Starting with the 1 May 2018 file (bp01my18.dbx - now invalid) ...
    <a href="/source/gis/Shapefiles/County/bp18mr25.dbx">bp18mr25.dbx</a>
    <a href="/source/gis/Shapefiles/County/bp16ap26.dbx">bp16ap26.dbx</a>"""
    # ordered by the valid date encoded in the name (2025-03-18 before 2026-04-16)
    assert parse_landing(html, r"bp\d{2}[a-z]{2}\d{2}\.dbx") == ["bp18mr25.dbx", "bp16ap26.dbx"]


def test_sanitize_header_handles_bom_case_and_duplicates():
    assert sanitize_header(["﻿ST", "E_TOTPOP", "Damage Property", "x", "X", "2020pop"]) == (
        "st",
        "e_totpop",
        "damage_property",
        "x",
        "x_1",
        "c_2020pop",
    )
