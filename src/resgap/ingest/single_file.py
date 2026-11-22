"""Single-file delimited sources (CDC/ATSDR SVI, Census population and reference files).

Partition key, text encoding and delimiter come from each source's entry in config/sources.yml.
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

from ..db import current_raw_load, pipeline_run, record_raw_load
from .context import IngestContext
from .rawload import load_delimited

log = logging.getLogger(__name__)

SINGLE_FILE_SOURCES = (
    "census_county_reference",
    "census_popest",
    "census_popest_pr",
    "cdc_svi",
    "cdc_svi_pr",
)


def ingest_one(ctx: IngestContext, source: str) -> int:
    cfg = ctx.sources[source]
    partition = cfg["partition"]
    encoding = cfg.get("encoding", "utf-8")
    raw_table = cfg["raw_table"]
    name = PurePosixPath(cfg["url"]).name
    key = f"raw/{source}/{partition}/{name}"
    with pipeline_run(ctx.conn, f"ingest:{source}", ctx.profile.name, {"url": cfg["url"]}) as run:
        ctx.run_id = run["run_id"]
        if ctx.offline_dir:
            local = ctx.offline_dir / source / name
            if not local.exists():
                run["status"], run["message"] = "unchanged", f"no offline file {local}"
                log.warning("offline: %s not provided - skipped", local)
                return 0
            info = ctx.stage_local_file(source=source, path=local, key=key, partition_key=partition)
        else:
            info = ctx.fetch_to_store(
                source=source, url=cfg["url"], key=key, partition_key=partition
            )
        if not ctx.force and current_raw_load(ctx.conn, raw_table, partition) == info.sha256:
            run["status"], run["message"] = "unchanged", f"{name} already loaded"
            return 0
        with ctx.store.open(info.key) as fh:
            res = load_delimited(
                ctx.conn,
                stream=fh,
                raw_table=raw_table,
                partition_key=partition,
                object_key=info.key,
                run_id=run["run_id"],
                delimiter=cfg.get("delimiter", ","),
                encoding=encoding,
            )
        record_raw_load(
            ctx.conn,
            run_id=run["run_id"],
            source=source,
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
        log.info("%s: %s", source, run["message"])
        return res.rows_loaded
