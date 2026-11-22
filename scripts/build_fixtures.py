"""Rebuild tests/fixtures from the live sources (real data excerpts, never synthetic).

Every fixture is a byte-for-byte subset of a real source file or a real API response:
  * delimited files keep the original header line and the original lines for Pennsylvania,
    Puerto Rico and the U.S. Virgin Islands (the PR population and PR SVI files are complete);
  * NOAA Storm Events keeps PA, PR and VI events from July-September 2024 (incl. Tropical Storm
    Debby -> DR-4815-PA and Hurricane Ernesto in PR/VI);
  * NWS crosswalks: the versions valid in 2024 plus the current one, and one archived version;
  * BLS: the unmodified CPI API response for the decade the profile needs;
  * OpenFEMA pages are unmodified API responses for the `fixture` profile filter;
  * OpenFEMA regression examples: one unmodified API response with real code-000 records
    (literal statewide and tribal-area designations), outside the offline run.

Usage:  python scripts/build_fixtures.py [--out tests/fixtures]
"""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import io
import json
import re
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from resgap.config import load_profile, load_sources  # noqa: E402
from resgap.http import make_session  # noqa: E402
from resgap.ingest.bls_cpi import decades, required_years  # noqa: E402
from resgap.ingest.noaa_storm_events import parse_listing  # noqa: E402
from resgap.ingest.openfema import build_scope  # noqa: E402

UA = "resgap-pipeline/0.1 (95-815 course project; fixture builder)"


def keep_lines(text: str, predicate, header: bool = True) -> str:
    lines = text.splitlines(keepends=True)
    head, body = (lines[:1], lines[1:]) if header else ([], lines)
    return "".join(head + [ln for ln in body if predicate(ln)])


def first_field(line: str, delimiter: str = ",") -> list[str]:
    return next(csv.reader(io.StringIO(line), delimiter=delimiter))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "tests" / "fixtures"))
    out = Path(ap.parse_args().out)
    s = make_session(UA)
    src = load_sources()
    prof = load_profile("fixture")  # PA, PR, VI; 2024
    abbrs = ("PA|", "PR|", "VI|")

    # Census county reference (pipe-delimited): PA, PR and VI rows
    cfg = src["census_county_reference"]
    t = s.get(cfg["url"], timeout=60).text
    _write(
        out / "census_county_reference" / "national_county2020.txt",
        keep_lines(t, lambda ln: ln.startswith(abbrs)).encode(),
    )

    # Census Vintage 2025 county totals: PA state + county rows (PR/VI are not in this file)
    cfg = src["census_popest"]
    raw = s.get(cfg["url"], timeout=60).content.decode("latin-1")
    _write(
        out / "census_popest" / "co-est2025-alldata.csv",
        keep_lines(raw, lambda ln: first_field(ln)[3] == "42").encode("latin-1"),
    )

    # Census Puerto Rico municipio estimates and CDC SVI Puerto Rico file: complete real files
    for name in ("census_popest_pr", "cdc_svi_pr"):
        cfg = src[name]
        _write(out / name / PurePosixPath(cfg["url"]).name, s.get(cfg["url"], timeout=60).content)

    # CDC/ATSDR SVI U.S. county file: PA rows (keeps the UTF-8 BOM of the header)
    cfg = src["cdc_svi"]
    raw = s.get(cfg["url"], timeout=60).content.decode("utf-8")
    _write(
        out / "cdc_svi" / "SVI_2022_US_county.csv",
        keep_lines(raw, lambda ln: ln.startswith("42,")).encode("utf-8"),
    )

    # NWS crosswalk versions valid during 2024 plus the current one (NWS-hosted), and one
    # archive-tier version (Internet Archive capture, SHA-1 verified) - PA/PR/VI rows only
    cfg = src["nws_zone_county"]
    for name in ("bp05mr24.dbx", "bp10se24.dbx", "bp16ap26.dbx"):
        t = s.get(cfg["base_url"] + name, timeout=60).content.decode("latin-1")
        _write(
            out / "nws_zone_county" / name,
            keep_lines(t, lambda ln: ln.startswith(abbrs), header=False).encode("latin-1"),
        )
    cap = next(c for c in cfg["archive"]["captures"] if c["file"] == "bp04ap17.dbx")
    body = s.get(cfg["archive"]["url_template"].format(**cap), timeout=120).content
    got = base64.b32encode(hashlib.sha1(body).digest()).decode()
    assert got == cap["sha1_base32"], "archive capture digest mismatch"
    _write(
        out / "nws_zone_county" / "internet_archive" / "bp04ap17.dbx",
        keep_lines(body.decode("latin-1"), lambda ln: ln.startswith(abbrs), header=False).encode(
            "latin-1"
        ),
    )

    # NOAA Storm Events 2024: PA, Puerto Rico (code 99) and Virgin Islands (96), Jul-Sep
    cfg = src["noaa_storm_events"]
    files = parse_listing(s.get(cfg["listing_url"], timeout=60).text, cfg["file_pattern"])
    name = files[2024]
    t = gzip.decompress(s.get(cfg["listing_url"] + name, timeout=120).content).decode("utf-8")
    keep = keep_lines(
        t,
        lambda ln: (
            re.match(r"20240[789],", ln) is not None
            and any(
                k in ln
                for k in (',"PENNSYLVANIA",42,', ',"PUERTO RICO",99,', ',"VIRGIN ISLANDS",96,')
            )
        ),
    )
    _write(out / "noaa_storm_events" / name, gzip.compress(keep.encode("utf-8"), mtime=0))

    # OpenFEMA: unmodified API responses for the fixture profile scope
    cfg = src["openfema"]
    for entity, ds in cfg["datasets"].items():
        scope = build_scope(ds, prof)
        params = {"$top": 10000, "$skip": 0, "$orderby": "id", "$count": "true"}
        if scope.filter:
            params["$filter"] = scope.filter
        r = s.get(f"{cfg['api_base']}/{ds['version']}/{entity}", params=params, timeout=120)
        r.raise_for_status()
        doc = json.loads(r.content)
        assert len(doc[entity]) == doc["metadata"]["count"], "fixture must be a single page"
        _write(out / "openfema" / entity / "page-00001.json", r.content)

    # OpenFEMA regression examples (not read by the offline run): every fipsCountyCode '000'
    # record of six real declarations, as one unmodified API response. Two are literal
    # "Statewide" designations (DR-1485-PA, DR-4669-AS); the others are tribal areas recorded
    # with code '000' (Omaha Indian Reservation DR-4013-NE; seven Seminole Tribe of Florida
    # reservations under tribal request DR-4844-FL; four Alaska Native villages DR-1423-AK;
    # Chickasaw OTSA DR-4802-OK). Used by tests of the statewide/tribal classification.
    entity = "DisasterDeclarationsSummaries"
    ds = cfg["datasets"][entity]
    numbers = (1485, 4669, 4013, 4844, 1423, 4802)
    flt = (
        "fipsCountyCode eq '000' and ("
        + " or ".join(f"disasterNumber eq {n}" for n in numbers)
        + ")"
    )
    params = {"$filter": flt, "$orderby": "id", "$count": "true", "$top": 10000}
    r = s.get(f"{cfg['api_base']}/{ds['version']}/{entity}", params=params, timeout=120)
    r.raise_for_status()
    doc = json.loads(r.content)
    assert len(doc[entity]) == doc["metadata"]["count"], "example must be a single page"
    _write(out / "openfema_examples" / entity / "code000-statewide-and-tribal.json", r.content)

    # BLS CPI-U: one unmodified API v1 response for the decade the fixture profile needs
    # (uses 1 of the 25 daily keyless queries)
    cfg = src["bls_cpi"]
    first, last = required_years(prof, 2025)
    for start, end in decades(first, last):
        body = fetch_decade_raw(s, cfg, start, end)
        _write(out / "bls_cpi" / f"{cfg['series_id']}_{start}-{end}.json", body)
    print(f"fixtures written to {out}")


def fetch_decade_raw(session, cfg, start: int, end: int) -> bytes:
    from datetime import date

    payload = {
        "seriesid": [cfg["series_id"]],
        "startyear": str(start),
        "endyear": str(min(end, date.today().year)),
        "annualaverage": True,
    }
    r = session.post(cfg["api_url"], json=payload, timeout=120)
    r.raise_for_status()
    assert json.loads(r.content).get("status") == "REQUEST_SUCCEEDED", r.content[:300]
    return r.content


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"{path.relative_to(ROOT)}: {len(data):,} bytes")


if __name__ == "__main__":
    main()
