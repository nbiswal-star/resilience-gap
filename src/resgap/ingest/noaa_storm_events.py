"""NOAA NCEI Storm Events Database: yearly bulk CSV details files -> raw table."""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from ..db import current_raw_load, pipeline_run, record_raw_load
from ..http import SourceUnavailableError, get_text
from .context import IngestContext
from .rawload import load_delimited

log = logging.getLogger(__name__)
SOURCE = "noaa_storm_events"


def parse_listing(html: str, file_pattern: str) -> dict[int, str]:
    """Map data year -> newest file name (highest cYYYYMMDD) from the NCEI directory listing."""
    rx = re.compile(file_pattern)
    best: dict[int, tuple[str, str]] = {}
    for name in set(re.findall(r'href="([^"]+)"', html)):
        m = rx.fullmatch(name)
        if not m:
            continue
        year, created = int(m.group("year")), m.group("created")
        if year not in best or created > best[year][1]:
            best[year] = (name, created)
    return {y: v[0] for y, v in best.items()}


def ingest(ctx: IngestContext) -> int:
    cfg = ctx.sources[SOURCE]
    raw_table = cfg["raw_table"]
    years = ctx.profile.years
    if ctx.offline_dir:
        files = {
            int(re.search(r"_d(\d{4})_", p.name).group(1)): p.name  # type: ignore[union-attr]
            for p in (ctx.offline_dir / SOURCE).glob("StormEvents_details-*.csv.gz")
        }
    else:
        files = parse_listing(
            get_text(
                ctx.session,
                cfg["listing_url"],
                ctx.settings.http_timeout,
                allowed_hosts=cfg["allowed_hosts"],
            ),
            cfg["file_pattern"],
        )
    missing = [y for y in years if y not in files]
    if missing:
        current_year = date.today().year
        hard = [y for y in missing if y < current_year]
        if hard and not ctx.offline_dir:
            raise SourceUnavailableError(f"NCEI listing has no details file for years {hard}")
        log.warning("no Storm Events file for years %s - skipped", missing)

    total = 0
    for year in [y for y in years if y in files]:
        name = files[year]
        partition = f"year={year}"
        key = f"raw/{SOURCE}/details/{partition}/{name}"
        with pipeline_run(
            ctx.conn, f"ingest:{SOURCE}", ctx.profile.name, {"year": year, "file": name}
        ) as run:
            ctx.run_id = run["run_id"]
            if ctx.offline_dir:
                info = ctx.stage_local_file(
                    source=SOURCE,
                    path=ctx.offline_dir / SOURCE / name,
                    key=key,
                    partition_key=partition,
                )
            else:
                info = ctx.fetch_to_store(
                    source=SOURCE, url=cfg["listing_url"] + name, key=key, partition_key=partition
                )
            if not ctx.force and current_raw_load(ctx.conn, raw_table, partition) == info.sha256:
                run["status"], run["message"] = "unchanged", f"{name} already loaded"
                log.info("%s unchanged - skip", name)
                continue
            with ctx.store.open(info.key) as fh:
                res = load_delimited(
                    ctx.conn,
                    stream=fh,
                    raw_table=raw_table,
                    partition_key=partition,
                    object_key=info.key,
                    run_id=run["run_id"],
                    gz=True,
                    encoding="utf-8",
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
            run["message"] = f"{name}: {res.rows_loaded} rows, {res.rows_rejected} rejected"
            log.info(run["message"])
            total += res.rows_loaded
    return total


def offline_file(offline_dir: Path) -> list[Path]:
    return sorted((offline_dir / SOURCE).glob("*.csv.gz"))
