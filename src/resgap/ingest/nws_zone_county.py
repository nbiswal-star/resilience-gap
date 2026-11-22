"""NWS County-Public Forecast Zones correlation files (versioned) -> raw.nws_zone_county.

About 40 % of Storm Events records are reported against NWS forecast *zones* (CZ_TYPE='Z').
Zones are renumbered and redrawn over time, so a zone event must be mapped with the crosswalk
version that was in effect on the event date (see sql/staging/05_storm_event_county.sql).

Versions and provenance tiers (object key shows the tier):
* ``raw/nws_zone_county/<file>``                   - downloaded from NWS (2020-2026 versions)
* ``raw/nws_zone_county/internet_archive/<file>``  - byte-for-byte Internet Archive capture of
  the original NWS URL, verified against the archive's SHA-1 digest (2004-2017 versions, and a
  fallback for an NWS-hosted file only when the capture's digest equals the NWS file)
Each version is one raw partition ``file=<bpDDmonYY>``.
"""

from __future__ import annotations

import csv
import logging
import re
import time

from ..db import current_raw_load, pipeline_run, record_raw_load
from ..http import SourceUnavailableError, get_text
from .context import IngestContext
from .rawload import load_delimited

log = logging.getLogger(__name__)
SOURCE = "nws_zone_county"
ARCHIVE_DIR = "internet_archive"


MONTHS = {
    "ja": 1,
    "fe": 2,
    "mr": 3,
    "ap": 4,
    "my": 5,
    "jn": 6,
    "jl": 7,
    "au": 8,
    "se": 9,
    "oc": 10,
    "nv": 11,
    "de": 12,
}


def file_date(name: str):
    """Valid date encoded in an NWS correlation file name: bp16ap26.dbx -> 2026-04-16."""
    from datetime import date

    m = re.match(r"bp(\d{2})([a-z]{2})(\d{2})", name)
    if not m or m.group(2) not in MONTHS:
        raise ValueError(f"not an NWS correlation file name: {name}")
    return date(2000 + int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))


def parse_landing(html: str, file_pattern: str) -> list[str]:
    """Correlation-file names that are actually linked (href) from the landing page.

    The page text also mentions retired files (e.g. "bp01my18.dbx - now invalid"), so plain
    text matches must not be used.
    """
    rx = re.compile(file_pattern)
    names = set()
    for href in re.findall(r'href="([^"]+)"', html):
        name = href.rsplit("/", 1)[-1]
        if rx.fullmatch(name):
            names.add(name)
    return sorted(names, key=file_date)


def archive_urls(tmpl: str, capture: dict) -> list[str]:
    """Replay URLs for an archive capture: the recorded original first, then without ':80'.

    The Wayback Machine sometimes stops replaying a capture under its recorded ``host:80`` form
    (HTTP 404) while still serving the same bytes for the port-less URL. Every candidate is
    verified against the capture's SHA-1 digest, so trying the second form is safe.
    """
    urls = [tmpl.format(**capture)]
    original = capture.get("original", "")
    stripped = re.sub(r"^(https?://[^/:]+):80/", r"\1/", original)
    if stripped != original:
        urls.append(tmpl.format(**{**capture, "original": stripped}))
    return urls


def plan_versions(cfg: dict, linked: list[str]) -> list[dict]:
    """Ordered list of versions to load: {'file', 'tier', 'url', 'sha1', 'fallback'}.

    Archive entries also carry ``alt_urls``: further replay URLs tried in order if ``url`` fails.
    """
    excluded = set(cfg.get("excluded_files", {}))
    archive = cfg.get("archive", {}) if cfg.get("archive", {}).get("enabled") else {}
    caps = {c["file"]: c for c in archive.get("captures", [])}
    tmpl = archive.get("url_template", "")
    hosted = [
        n
        for n in dict.fromkeys(list(cfg.get("nws_hosted_files", [])) + linked)
        if n not in excluded
    ]
    plan = []
    for name in hosted:
        fb = None
        c = caps.get(name)
        if c and c.get("role") == "fallback_for_nws_hosted":
            fb_urls = archive_urls(tmpl, c)
            fb = {"url": fb_urls[0], "alt_urls": fb_urls[1:], "sha1": c["sha1_base32"]}
        plan.append(
            {
                "file": name,
                "tier": "nws",
                "url": cfg["base_url"] + name,
                "sha1": None,
                "fallback": fb,
                "required": name in linked,
            }
        )
    for name, c in caps.items():
        if c.get("role") == "archive_only" and name not in excluded and name not in hosted:
            urls = archive_urls(tmpl, c)
            plan.append(
                {
                    "file": name,
                    "tier": ARCHIVE_DIR,
                    "url": urls[0],
                    "alt_urls": urls[1:],
                    "sha1": c["sha1_base32"],
                    "fallback": None,
                    "required": False,
                }
            )
    return plan


def ingest(ctx: IngestContext) -> int:
    cfg = ctx.sources[SOURCE]
    raw_table = cfg["raw_table"]
    if ctx.offline_dir:
        base = ctx.offline_dir / SOURCE
        plan = [{"file": p.name, "tier": "nws", "path": p} for p in sorted(base.glob("bp*.dbx"))]
        plan += [
            {"file": p.name, "tier": ARCHIVE_DIR, "path": p}
            for p in sorted((base / ARCHIVE_DIR).glob("bp*.dbx"))
        ]
        if not plan:
            raise SourceUnavailableError(f"no offline crosswalk files in {base}")
    else:
        html = get_text(
            ctx.session,
            cfg["landing_page"],
            ctx.settings.http_timeout,
            allowed_hosts=cfg["allowed_hosts"],
        )
        linked = parse_landing(html, cfg["file_pattern"])
        if not linked:
            raise SourceUnavailableError(
                "No NWS zone-county correlation files linked on landing page"
            )
        plan = plan_versions(cfg, linked)

    total = 0
    missing: list[str] = []
    for item in plan:
        name, tier = item["file"], item["tier"]
        partition = f"file={name.removesuffix('.dbx')}"
        with pipeline_run(
            ctx.conn, f"ingest:{SOURCE}", ctx.profile.name, {"file": name, "tier": tier}
        ) as run:
            ctx.run_id = run["run_id"]
            key = f"raw/{SOURCE}/{name}" if tier == "nws" else f"raw/{SOURCE}/{ARCHIVE_DIR}/{name}"
            if ctx.offline_dir:
                info = ctx.stage_local_file(
                    source=SOURCE, path=item["path"], key=key, partition_key=partition
                )
            else:
                info = _fetch_version(ctx, cfg, item, key, partition, run, missing)
                if info is None:
                    continue
            if not ctx.force and current_raw_load(ctx.conn, raw_table, partition) == info.sha256:
                run["status"], run["message"] = "unchanged", f"{name} already loaded"
                continue
            with ctx.store.open(info.key) as fh:
                res = load_delimited(
                    ctx.conn,
                    stream=fh,
                    raw_table=raw_table,
                    partition_key=partition,
                    object_key=info.key,
                    run_id=run["run_id"],
                    delimiter="|",
                    encoding="latin-1",
                    columns=tuple(cfg["columns"]),
                    quoting=csv.QUOTE_NONE,
                )
            record_raw_load(
                ctx.conn,
                run_id=run["run_id"],
                source=SOURCE,
                raw_table=raw_table,
                partition_key=partition,
                object_keys=[info.key],
                content_sha256=info.sha256,
                rows_loaded=res.rows_loaded,
                rows_rejected=res.rows_rejected,
            )
            ctx.conn.commit()
            run["rows_affected"] = res.rows_loaded
            run["message"] = (
                f"{name} ({info.key.split('/')[-2]}): {res.rows_loaded} rows, "
                f"{res.rows_rejected} rejected"
            )
            total += res.rows_loaded
    if missing:
        log.warning("crosswalk versions not loaded (coverage reported by DQ): %s", missing)
    return total


def _fetch_version(
    ctx: IngestContext,
    cfg: dict,
    item: dict,
    key: str,
    partition: str,
    run: dict,
    missing: list[str],
):
    name = item["file"]
    archive_hosts = cfg.get("archive", {}).get("allowed_hosts", [])
    try:
        if item["tier"] == "nws":
            return ctx.fetch_to_store(
                source=SOURCE,
                url=item["url"],
                key=key,
                partition_key=partition,
                allowed_hosts=cfg["allowed_hosts"],
            )
        return _fetch_archive(
            ctx,
            [item["url"], *item.get("alt_urls", [])],
            key,
            partition,
            archive_hosts,
            item["sha1"],
        )
    except SourceUnavailableError as exc:
        fb = item.get("fallback")
        if fb:
            log.warning("%s unavailable from NWS (%s); using verified archive capture", name, exc)
            try:
                return _fetch_archive(
                    ctx,
                    [fb["url"], *fb.get("alt_urls", [])],
                    f"raw/{SOURCE}/{ARCHIVE_DIR}/{name}",
                    partition,
                    archive_hosts,
                    fb["sha1"],
                )
            except SourceUnavailableError as exc2:
                exc = exc2
        if item.get("required"):
            raise
        run["status"], run["message"] = "unchanged", f"version not available: {exc}"[:500]
        missing.append(name)
        return None


def _fetch_archive(
    ctx: IngestContext,
    urls: list[str],
    key: str,
    partition: str,
    allowed_hosts: list[str],
    sha1: str,
):
    """Fetch an archive capture, trying each replay URL in order (each digest-verified)."""
    last: SourceUnavailableError | None = None
    for url in urls:
        time.sleep(1.0)  # be polite to the archive
        try:
            return ctx.fetch_to_store(
                source=SOURCE,
                url=url,
                key=key,
                partition_key=partition,
                allowed_hosts=allowed_hosts,
                expected_sha1_base32=sha1,
            )
        except SourceUnavailableError as exc:
            if last is None and len(urls) > 1:
                log.info("archive replay failed for %s (%s); trying alternate URL", url, exc)
            last = exc
    assert last is not None
    raise last
