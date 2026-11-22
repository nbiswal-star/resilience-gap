"""Source registry, fallbacks, redirect validation, health checks and CPI parsing."""

import csv
import gzip
import io
import json
from datetime import date
from types import SimpleNamespace

import pytest

from resgap.config import Profile, load_sources
from resgap.http import SourceUnavailableError, validate_redirects
from resgap.ingest.bls_cpi import decades, parse_response, required_years
from resgap.ingest.nws_zone_county import archive_urls, file_date, plan_versions
from resgap.ingest.openfema import in_scope
from resgap.sourcecheck import (
    Endpoint,
    SourceCheck,
    check_csv,
    header_fingerprint,
    keys_fingerprint,
)
from tests.conftest import FIXTURES

SOURCES = load_sources()


# ---------- registry fingerprints match the real headers committed as fixtures ----------
@pytest.mark.parametrize(
    "source,path,encoding,delimiter",
    [
        ("cdc_svi", "cdc_svi/SVI_2022_US_county.csv", "utf-8-sig", ","),
        ("cdc_svi_pr", "cdc_svi_pr/PuertoRico_county.csv", "utf-8-sig", ","),
        ("census_popest", "census_popest/co-est2025-alldata.csv", "latin-1", ","),
        ("census_popest_pr", "census_popest_pr/cc-est2025-agesex-72.csv", "latin-1", ","),
        (
            "census_county_reference",
            "census_county_reference/national_county2020.txt",
            "utf-8-sig",
            "|",
        ),
    ],
)
def test_registry_fingerprint_matches_real_header(source, path, encoding, delimiter):
    with open(FIXTURES / path, encoding=encoding, newline="") as fh:
        header = next(csv.reader(fh, delimiter=delimiter))
    assert header_fingerprint(header) == SOURCES[source]["health"]["fingerprint"]


def test_noaa_fingerprint_matches_real_header():
    f = next((FIXTURES / "noaa_storm_events").glob("*.csv.gz"))
    header = next(csv.reader(io.StringIO(gzip.decompress(f.read_bytes()).decode().split("\n")[0])))
    assert len(header) == 51
    assert header_fingerprint(header) == SOURCES["noaa_storm_events"]["health"]["fingerprint"]


def test_nws_fingerprint_matches_field_count():
    line = (FIXTURES / "nws_zone_county" / "bp16ap26.dbx").read_text("latin-1").splitlines()[0]
    fp = keys_fingerprint([f"fields={len(line.split('|'))}"])
    assert fp == SOURCES["nws_zone_county"]["health"]["fingerprint"]


def test_openfema_fingerprints_match_real_records():
    for entity, ds in SOURCES["openfema"]["datasets"].items():
        doc = json.loads((FIXTURES / "openfema" / entity / "page-00001.json").read_bytes())
        assert keys_fingerprint(doc[entity][0].keys()) == ds["fingerprint"], entity


# ---------- NWS version registry ----------
def test_file_date_decodes_two_letter_months():
    assert file_date("bp16ap26.dbx") == date(2026, 4, 16)
    assert file_date("bp01jn10") == date(2010, 6, 1)
    assert file_date("bp15nv10.dbx") == date(2010, 11, 15)
    with pytest.raises(ValueError):
        file_date("bp16xx26.dbx")


def test_plan_versions_respects_exclusions_tiers_and_fallbacks():
    cfg = SOURCES["nws_zone_county"]
    plan = plan_versions(cfg, ["bp18mr25.dbx", "bp16ap26.dbx"])
    files = [p["file"] for p in plan]
    assert "bp03mr26.dbx" not in files and "bp07se22.dbx" not in files  # never in effect
    assert len(files) == len(set(files))
    nws = [p for p in plan if p["tier"] == "nws"]
    arch = [p for p in plan if p["tier"] == "internet_archive"]
    assert len(nws) == 12 and len(arch) == 35
    assert all(p["sha1"] for p in arch)  # every archive download is digest-verified
    assert all(p["url"].startswith("https://web.archive.org/web/") for p in arch)
    with_fb = {p["file"] for p in nws if p["fallback"]}
    assert with_fb == {"bp05mr24.dbx", "bp18mr25.dbx", "bp16ap26.dbx"}
    assert {p["file"] for p in nws if p["required"]} == {"bp18mr25.dbx", "bp16ap26.dbx"}
    assert min(file_date(p["file"]) for p in arch) == date(2004, 12, 28)
    # captures recorded under host:80 also get a port-less replay URL to fall back on
    by_file = {p["file"]: p for p in arch}
    assert by_file["bp10fe06.dbx"]["alt_urls"] == [
        "https://web.archive.org/web/20060622165329id_/"
        "http://www.weather.gov/geodata/catalog/wsom/data/bp10fe06.dbx"
    ]
    assert by_file["bp25my05.dbx"]["alt_urls"] == []  # original had no port


def test_archive_urls_strip_only_default_port():
    tmpl = "https://web.archive.org/web/{timestamp}id_/{original}"
    cap = {"timestamp": "1", "original": "http://www.nws.noaa.gov:80/a/b.dbx"}
    assert archive_urls(tmpl, cap) == [
        "https://web.archive.org/web/1id_/http://www.nws.noaa.gov:80/a/b.dbx",
        "https://web.archive.org/web/1id_/http://www.nws.noaa.gov/a/b.dbx",
    ]
    assert archive_urls(tmpl, {"timestamp": "1", "original": "http://h:8080/x.dbx"}) == [
        "https://web.archive.org/web/1id_/http://h:8080/x.dbx"
    ]


# ---------- OpenFEMA bulk-fallback scope twin ----------
def test_in_scope_mirrors_odata_filter():
    p = Profile("t", 2022, 2024, ("PA",))
    decl = {"state_field": "state", "date_field": "incidentBeginDate"}
    assert in_scope({"state": "PA", "incidentBeginDate": "2024-12-31T00:00:00.000Z"}, decl, p)
    assert not in_scope({"state": "PA", "incidentBeginDate": "2025-01-01T00:00:00.000Z"}, decl, p)
    assert not in_scope({"state": "OH", "incidentBeginDate": "2023-01-01T00:00:00.000Z"}, decl, p)
    hma = {"state_field": "subapplicantStateAbbreviation", "year_field": "fiscalYear"}
    assert in_scope({"subapplicantStateAbbreviation": "PA", "fiscalYear": 2022}, hma, p)
    assert not in_scope({"subapplicantStateAbbreviation": "PA", "fiscalYear": None}, hma, p)


# ---------- BLS ----------
def test_decades_and_required_years():
    assert decades(1999, 2025) == [(1990, 1999), (2000, 2009), (2010, 2019), (2020, 2029)]
    assert required_years(Profile("f", 2000, 2025, None), 2025) == (1999, 2025)
    assert required_years(Profile("x", 2024, 2024, None), 2025) == (2023, 2025)


def test_parse_real_bls_response():
    body = (FIXTURES / "bls_cpi" / "CUUR0000SA0_2020-2029.json").read_bytes()
    data = parse_response(body, "CUUR0000SA0")
    periods = {(d["year"], d["period"]) for d in data}
    assert ("2025", "M13") in periods  # BLS annual average returned
    oct25 = next(d for d in data if d["year"] == "2025" and d["period"] == "M10")
    assert oct25["value"] == "-"  # not published (2025 lapse in appropriations)


@pytest.mark.parametrize(
    "doc",
    [
        {"status": "REQUEST_NOT_PROCESSED", "message": ["threshold"]},
        {"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"seriesID": "OTHER", "data": []}]}},
    ],
)
def test_bls_errors_fail_loudly(doc):
    with pytest.raises(SourceUnavailableError):
        parse_response(json.dumps(doc).encode(), "CUUR0000SA0")


# ---------- redirect validation ----------
def _resp(urls):
    hist = [SimpleNamespace(url=u) for u in urls[:-1]]
    return SimpleNamespace(history=hist, url=urls[-1])


def test_redirects_within_allowed_hosts_pass():
    validate_redirects(
        _resp(["http://svi.cdc.gov/x.csv", "https://svi.cdc.gov/x.csv"]), ["svi.cdc.gov"]
    )


@pytest.mark.parametrize(
    "urls",
    [
        ["https://hazards.fema.gov/nri/x.zip", "https://www.fema.gov/rapt"],  # retired download
        ["https://svi.cdc.gov/x.csv", "http://svi.cdc.gov/x.csv"],
    ],  # downgraded to HTTP
)
def test_bad_redirects_are_rejected(urls):
    with pytest.raises(SourceUnavailableError):
        validate_redirects(_resp(urls), [urls[0].split("/")[2]])


# ---------- source-check behaviour with a fake HTTP session ----------
class FakeStream:
    def __init__(self, body: bytes, url: str, ctype="text/csv", history=()):
        self.body, self.url, self.history = body, url, list(history)
        self.status_code = 200
        self.headers = {"Content-Type": ctype, "Last-Modified": "Tue, 03 Mar 2026 16:00:28 GMT"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.body


class FakeSession:
    def __init__(self, body, final_url=None, ctype="text/csv"):
        self.body, self.final_url, self.ctype = body, final_url, ctype

    def get(self, url, stream=False, timeout=None, **kw):
        hist = [SimpleNamespace(url=url)] if self.final_url else []
        return FakeStream(self.body, self.final_url or url, self.ctype, hist)


def _svi_header() -> bytes:
    return (FIXTURES / "cdc_svi" / "SVI_2022_US_county.csv").read_bytes().split(b"\n")[0] + b"\n"


def test_source_check_reports_match():
    sc = check_csv(FakeSession(_svi_header()), "cdc_svi", SOURCES["cdc_svi"], 10)
    assert sc.chosen == "primary" and not sc.drift
    assert sc.endpoints[0].last_modified


def test_source_check_detects_schema_drift():
    body = _svi_header().replace(b"RPL_THEMES", b"RPL_THEMES_V2")
    sc = check_csv(FakeSession(body), "cdc_svi", SOURCES["cdc_svi"], 10)
    assert sc.drift and sc.chosen is None


def test_source_check_rejects_foreign_redirect_and_html():
    sc = check_csv(
        FakeSession(_svi_header(), final_url="https://example.org/x.csv"),
        "cdc_svi",
        SOURCES["cdc_svi"],
        10,
    )
    assert sc.chosen is None and "unexpected host" in sc.endpoints[0].detail
    sc = check_csv(
        FakeSession(b"<html>moved</html>", ctype="text/html"), "cdc_svi", SOURCES["cdc_svi"], 10
    )
    assert sc.chosen is None


def test_chosen_prefers_primary_then_fallback():
    sc = SourceCheck(
        "x", [Endpoint("primary", "p", "u", ok=False), Endpoint("fallback", "f", "u", ok=True)]
    )
    assert sc.chosen == "fallback"


def test_fetch_archive_falls_back_to_alternate_replay_url(monkeypatch):
    from resgap.http import SourceUnavailableError
    from resgap.ingest import nws_zone_county as nzc

    monkeypatch.setattr(nzc.time, "sleep", lambda s: None)
    tried = []

    class Ctx:
        def fetch_to_store(self, *, url, expected_sha1_base32, **kw):
            tried.append((url, expected_sha1_base32))
            if ":80/" in url:
                raise SourceUnavailableError("404")
            return "stored"

    urls = ["https://a/http://h:80/f.dbx", "https://a/http://h/f.dbx"]
    assert nzc._fetch_archive(Ctx(), urls, "k", "p", ["a"], "SHA") == "stored"
    assert tried == [(urls[0], "SHA"), (urls[1], "SHA")]  # digest checked on every attempt
    with pytest.raises(SourceUnavailableError):
        nzc._fetch_archive(Ctx(), urls[:1], "k", "p", ["a"], "SHA")
