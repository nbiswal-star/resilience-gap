"""BLS CPI-U (series CUUR0000SA0) via the keyless Public Data API v1 -> raw.bls_cpi_records.

API v1 limits (https://www.bls.gov/developers/api_faqs.htm): 25 queries/day, 10 years per
query. Requests are aligned to calendar decades (1990-1999, 2000-2009, ...), so each decade is
one stable raw partition. Each response is stored unchanged in the object store; each data
point is landed as a JSONB row. Annual averages are derived in SQL (staging.cpi_annual).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from datetime import UTC, date, datetime

import requests

from ..db import current_raw_load, pipeline_run, record_raw_load, record_source_object
from ..http import SourceUnavailableError, validate_redirects
from .context import IngestContext

log = logging.getLogger(__name__)
SOURCE = "bls_cpi"
RAW_TABLE = "raw.bls_cpi_records"


def decades(first_year: int, last_year: int) -> list[tuple[int, int]]:
    """Decade-aligned (start, end) pairs covering first_year..last_year."""
    out = []
    start = first_year - first_year % 10
    while start <= last_year:
        out.append((start, start + 9))
        start += 10
    return out


def required_years(profile, base_year: int) -> tuple[int, int]:
    # Federal fiscal year N starts in October of N-1, so one earlier year is needed.
    first = min(profile.start_year - 1, base_year)
    last = max(profile.end_year, base_year)
    return first, last


def fetch_decade(ctx: IngestContext, series: str, start: int, end: int) -> bytes:
    cfg = ctx.sources[SOURCE]
    payload = {
        "seriesid": [series],
        "startyear": str(start),
        "endyear": str(min(end, date.today().year)),
        "annualaverage": True,
    }
    try:
        r = ctx.session.post(cfg["api_url"], json=payload, timeout=ctx.settings.http_timeout)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise SourceUnavailableError(f"BLS API {start}-{end} failed: {exc}") from exc
    validate_redirects(r, cfg["allowed_hosts"])
    return r.content


def parse_response(body: bytes, series: str) -> list[dict]:
    try:
        doc = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SourceUnavailableError("BLS API returned non-JSON content") from exc
    if doc.get("status") != "REQUEST_SUCCEEDED":
        # e.g. REQUEST_NOT_PROCESSED when the daily query threshold is exceeded
        raise SourceUnavailableError(f"BLS API status {doc.get('status')}: {doc.get('message')}")
    series_list = doc.get("Results", {}).get("series", [])
    match = [s for s in series_list if s.get("seriesID") == series]
    if not match:
        raise SourceUnavailableError(f"BLS API response has no series {series}")
    return match[0].get("data", [])


def record_hash(rec: dict) -> str:
    return hashlib.sha256(
        json.dumps(rec, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def ingest(ctx: IngestContext) -> int:
    cfg = ctx.sources[SOURCE]
    series = cfg["series_id"]
    base_year = int(ctx.settings.analysis.get("inflation", {}).get("base_year", 2025))
    first, last = required_years(ctx.profile, base_year)
    total = 0
    for start, end in decades(first, last):
        partition = f"series={series};years={start}-{end}"
        with pipeline_run(
            ctx.conn,
            f"ingest:{SOURCE}",
            ctx.profile.name,
            {"series": series, "years": f"{start}-{end}"},
        ) as run:
            ctx.run_id = run["run_id"]
            if ctx.offline_dir:
                path = ctx.offline_dir / SOURCE / f"{series}_{start}-{end}.json"
                if not path.exists():
                    run["status"], run["message"] = "unchanged", f"no offline file {path.name}"
                    continue
                body = path.read_bytes()
                source_url = path.resolve().as_uri()
                prefix = "offline/"
            else:
                body = fetch_decade(ctx, series, start, end)
                source_url = cfg["api_url"]
                prefix = ""
            records = parse_response(body, series)
            if not records:
                raise SourceUnavailableError(f"BLS API returned no data for {series} {start}-{end}")
            content_sha = hashlib.sha256(
                "".join(sorted(record_hash(r) for r in records)).encode()
            ).hexdigest()
            if not ctx.force and current_raw_load(ctx.conn, RAW_TABLE, partition) == content_sha:
                run["status"] = "unchanged"
                run["message"] = f"{series} {start}-{end}: {len(records)} points, unchanged"
                continue
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            key = (
                f"{prefix}raw/{SOURCE}/{series}/years={start}-{end}/snapshot={ts}/response.json.gz"
            )
            info = ctx.store.put_bytes(key, gzip.compress(body, mtime=0))
            record_source_object(
                ctx.conn,
                run_id=run["run_id"],
                source=SOURCE,
                partition_key=partition,
                source_url=source_url,
                info=info,
                content_type="application/json+gzip",
            )
            with ctx.conn.cursor() as cur:
                cur.execute(f"delete from {RAW_TABLE} where partition_key = %s", (partition,))
                with cur.copy(
                    f"copy {RAW_TABLE} (series_id, partition_key, record, "
                    "record_sha256, _object_key, _run_id) from stdin"
                ) as copy:
                    for rec in records:
                        copy.write_row(
                            [
                                series,
                                partition,
                                json.dumps(rec),
                                record_hash(rec),
                                key,
                                run["run_id"],
                            ]
                        )
            record_raw_load(
                ctx.conn,
                run_id=run["run_id"],
                source=SOURCE,
                raw_table=RAW_TABLE,
                partition_key=partition,
                object_keys=[key],
                content_sha256=content_sha,
                rows_loaded=len(records),
                rows_rejected=0,
            )
            ctx.conn.commit()
            run["rows_affected"] = len(records)
            run["message"] = f"{series} {start}-{end}: {len(records)} data points"
            log.info(run["message"])
            total += len(records)
    return total


def offline_name(series: str, start: int, end: int) -> str:
    return f"{series}_{start}-{end}.json"
