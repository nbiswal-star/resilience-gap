"""OpenFEMA REST API (semi-structured JSON) -> object store pages -> raw.openfema_records (JSONB).

Storage decision: every API page is kept byte-for-byte (gzip-compressed) in the object store,
which is the system of record for this semi-structured source. Records are then also landed
unmodified as JSONB rows so that flattening can be done with SQL inside PostgreSQL.

Paging: OData-style ``$top``/``$skip`` with a stable ``$orderby=id`` so pages cannot overlap.
The first page asks for ``$count=true`` and the final record count must match it, otherwise
the run fails (a partial snapshot must never replace a complete one).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from ..config import Profile
from ..db import current_raw_load, pipeline_run, record_raw_load, record_source_object
from ..http import SourceUnavailableError, download, validate_redirects
from .context import IngestContext

log = logging.getLogger(__name__)
SOURCE = "openfema"
RAW_TABLE = "raw.openfema_records"


class IncompleteSnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class Scope:
    key: str  # human readable, e.g. "states=OH,PA;years=2022-2024"
    filter: str | None

    @property
    def scope_id(self) -> str:
        return hashlib.sha1(self.key.encode()).hexdigest()[:10]


def build_scope(ds_cfg: dict[str, Any], profile: Profile) -> Scope:
    """Translate a run profile into an OData $filter for one dataset."""
    parts: list[str] = []
    key_parts: list[str] = []
    state_field = ds_cfg.get("state_field")
    if profile.states and state_field:
        ors = " or ".join(f"{state_field} eq '{s}'" for s in profile.states)
        parts.append(f"({ors})")
        key_parts.append("states=" + ",".join(sorted(profile.states)))
    else:
        key_parts.append("states=ALL")
    if ds_cfg.get("date_field"):
        f = ds_cfg["date_field"]
        parts.append(
            f"{f} ge '{profile.start_year}-01-01T00:00:00.000Z' and "
            f"{f} le '{profile.end_year}-12-31T23:59:59.999Z'"
        )
    elif ds_cfg.get("year_field"):
        f = ds_cfg["year_field"]
        parts.append(f"{f} ge {profile.start_year} and {f} le {profile.end_year}")
    key_parts.append(f"years={profile.start_year}-{profile.end_year}")
    return Scope(";".join(key_parts), " and ".join(parts) if parts else None)


def iter_pages(
    session: requests.Session,
    url: str,
    entity: str,
    scope: Scope,
    page_size: int,
    timeout: int,
    allowed_hosts: list[str] | None = None,
) -> Iterator[tuple[int, bytes, list[dict], int | None]]:
    """Yield (page_number, raw_response_bytes, records, total_count_or_None)."""
    skip = 0
    page = 1
    while True:
        params: dict[str, Any] = {"$top": page_size, "$skip": skip, "$orderby": "id"}
        if scope.filter:
            params["$filter"] = scope.filter
        if page == 1:
            params["$count"] = "true"
        try:
            r = session.get(url, params=params, timeout=timeout)
            r.raise_for_status()
        except requests.RequestException as exc:
            raise SourceUnavailableError(f"OpenFEMA {entity} page {page} failed: {exc}") from exc
        validate_redirects(r, allowed_hosts)
        body = r.content
        try:
            doc = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SourceUnavailableError(f"OpenFEMA {entity} page {page}: not JSON") from exc
        if "error" in doc:
            raise SourceUnavailableError(f"OpenFEMA {entity} error: {doc['error']}")
        records = doc.get(entity)
        if not isinstance(records, list):
            raise SourceUnavailableError(f"OpenFEMA response has no '{entity}' array")
        total = doc.get("metadata", {}).get("count") if page == 1 else None
        yield page, body, records, total
        if len(records) < page_size:
            return
        skip += page_size
        page += 1


def in_scope(record: dict, ds_cfg: dict[str, Any], profile: Profile) -> bool:
    """Python twin of build_scope's $filter, used for the bulk-export fallback."""
    state_field = ds_cfg.get("state_field")
    if profile.states and state_field and record.get(state_field) not in profile.states:
        return False
    if ds_cfg.get("date_field"):
        v = record.get(ds_cfg["date_field"])
        if not isinstance(v, str) or len(v) < 10:
            return False
        return f"{profile.start_year}-01-01" <= v[:10] <= f"{profile.end_year}-12-31"
    if ds_cfg.get("year_field"):
        v = record.get(ds_cfg["year_field"])
        return isinstance(v, int) and profile.start_year <= v <= profile.end_year
    return True


def iter_bulk_pages(
    ctx: IngestContext,
    entity: str,
    version: str,
    ds_cfg: dict[str, Any],
    prefix: str,
    partition: str,
    page_size: int,
) -> Iterator[tuple[int, bytes, list[dict], int | None]]:
    """Fallback: download the dataset's bulk .jsonl export, keep it, and page the in-scope records.

    The original export is stored (gzip, lossless) in the object store; pages are re-built from
    the matching records so that loading, hashing and idempotency behave exactly as for the API.
    """
    cfg = ctx.sources[SOURCE]
    bulk_url = f"{cfg['api_base']}/{version}/{entity}.{cfg.get('bulk_format', 'jsonl')}"
    dl = download(
        ctx.session, bulk_url, ctx.settings.http_timeout, allowed_hosts=cfg["allowed_hosts"]
    )
    try:
        gz_path = dl.path.with_suffix(".gz")
        with open(dl.path, "rb") as src, gzip.open(gz_path, "wb", mtime=0) as dst:
            shutil.copyfileobj(src, dst)
        info = ctx.store.put_file(f"{prefix}/bulk/{entity}.jsonl.gz", gz_path)
        gz_path.unlink(missing_ok=True)
        record_source_object(
            ctx.conn,
            run_id=ctx.run_id,
            source=SOURCE,
            partition_key=partition,
            source_url=bulk_url,
            info=info,
            content_type="application/jsonl+json+gzip",
        )
        ctx.conn.commit()
        page, batch = 1, []
        with open(dl.path, encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SourceUnavailableError(
                        f"bulk {entity}: line {line_no} is not valid JSON (truncated download?)"
                    ) from exc
                if in_scope(rec, ds_cfg, ctx.profile):
                    batch.append(rec)
                if len(batch) == page_size:
                    yield page, _envelope(entity, bulk_url, batch), batch, None
                    page, batch = page + 1, []
        if batch or page == 1:
            yield page, _envelope(entity, bulk_url, batch), batch, None
    finally:
        dl.path.unlink(missing_ok=True)


def _envelope(entity: str, url: str, records: list[dict]) -> bytes:
    meta = {"source": "bulk_export_fallback", "url": url, "count": len(records)}
    return json.dumps({"metadata": meta, entity: records}, ensure_ascii=False).encode()


def canonical_record_hash(record: dict) -> str:
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def snapshot_hash(record_hashes: list[str]) -> str:
    h = hashlib.sha256()
    for rh in sorted(record_hashes):
        h.update(rh.encode())
    return h.hexdigest()


def _offline_pages(offline_dir: Path, entity: str) -> Iterator[tuple[int, bytes, list[dict], int]]:
    files = sorted((offline_dir / SOURCE / entity).glob("page-*.json*"))
    if not files:
        raise SourceUnavailableError(f"no offline pages for {entity} in {offline_dir}")
    docs = []
    for f in files:
        body = f.read_bytes()
        if f.suffix == ".gz":
            body = gzip.decompress(body)
        docs.append((body, json.loads(body)))
    total = sum(len(d[entity]) for _, d in docs)
    for i, (body, doc) in enumerate(docs, start=1):
        yield i, body, doc[entity], total if i == 1 else None


def ingest_dataset(ctx: IngestContext, entity: str) -> int:
    cfg = ctx.sources[SOURCE]
    ds_cfg = cfg["datasets"][entity]
    version = ds_cfg["version"]
    scope = build_scope(ds_cfg, ctx.profile)
    partition = f"{entity}/{version}/{scope.key}"
    url = f"{cfg['api_base']}/{version}/{entity}"
    params = {"entity": entity, "version": version, "scope": scope.key, "filter": scope.filter}
    with pipeline_run(ctx.conn, f"ingest:{SOURCE}:{entity}", ctx.profile.name, params) as run:
        ctx.run_id = run["run_id"]
        snapshot_ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        prefix = f"raw/{SOURCE}/{entity}/{version}/scope={scope.scope_id}/snapshot={snapshot_ts}"
        source_url = url
        if ctx.offline_dir:  # truthful provenance + separate namespace for offline copies
            prefix = f"offline/{prefix}"
            source_url = (ctx.offline_dir / SOURCE / entity).as_uri()
        tmpdir = Path(tempfile.mkdtemp(prefix="resgap-openfema-"))
        try:

            def collect(pages):
                expected: int | None = None
                hashes: list[str] = []
                files: list[tuple[int, Path]] = []
                for page_no, body, records, total in pages:
                    if total is not None:
                        expected = total
                    p = tmpdir / f"page-{page_no:05d}.json.gz"
                    p.write_bytes(gzip.compress(body, mtime=0))
                    files.append((page_no, p))
                    hashes.extend(canonical_record_hash(r) for r in records)
                    log.info("%s page %s: %s records", entity, page_no, len(records))
                return expected, hashes, files

            via = "offline" if ctx.offline_dir else "api"
            if ctx.offline_dir:
                expected_total, record_hashes, page_files = collect(
                    _offline_pages(ctx.offline_dir, entity)
                )
            else:
                try:
                    expected_total, record_hashes, page_files = collect(
                        iter_pages(
                            ctx.session,
                            url,
                            entity,
                            scope,
                            int(cfg["page_size"]),
                            ctx.settings.http_timeout,
                            allowed_hosts=cfg["allowed_hosts"],
                        )
                    )
                except SourceUnavailableError as exc:
                    log.warning("%s: API failed (%s); falling back to the bulk export", entity, exc)
                    for f in tmpdir.iterdir():
                        f.unlink()
                    via = "bulk_export_fallback"
                    expected_total, record_hashes, page_files = collect(
                        iter_bulk_pages(
                            ctx, entity, version, ds_cfg, prefix, partition, int(cfg["page_size"])
                        )
                    )
            if expected_total is not None and expected_total != len(record_hashes):
                raise IncompleteSnapshotError(
                    f"{entity}: API reported {expected_total} records but {len(record_hashes)} "
                    "were retrieved; refusing to load a partial snapshot"
                )
            content_sha = snapshot_hash(record_hashes)
            if not ctx.force and current_raw_load(ctx.conn, RAW_TABLE, partition) == content_sha:
                run["status"] = "unchanged"
                run["message"] = (
                    f"{entity}: {len(record_hashes)} records, content unchanged (via {via})"
                )
                log.info(run["message"])
                return 0
            keys: list[str] = []
            for page_no, p in page_files:
                key = f"{prefix}/page-{page_no:05d}.json.gz"
                info = ctx.store.put_file(key, p)
                record_source_object(
                    ctx.conn,
                    run_id=run["run_id"],
                    source=SOURCE,
                    partition_key=partition,
                    source_url=source_url,
                    info=info,
                    content_type="application/json+gzip",
                )
                keys.append(key)
            loaded = _load_pages(ctx, entity, version, scope, keys, run["run_id"])
            record_raw_load(
                ctx.conn,
                run_id=run["run_id"],
                source=SOURCE,
                raw_table=RAW_TABLE,
                partition_key=partition,
                object_keys=keys,
                content_sha256=content_sha,
                rows_loaded=loaded,
                rows_rejected=0,
            )
            ctx.conn.commit()
            run["rows_affected"] = loaded
            run["message"] = (
                f"{entity}: {loaded} records in {len(keys)} pages ({scope.key}) via {via}"
            )
            log.info(run["message"])
            return loaded
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _load_pages(
    ctx: IngestContext, entity: str, version: str, scope: Scope, keys: list[str], run_id
) -> int:
    """Replace this dataset+scope in raw.openfema_records with the stored pages."""
    n = 0
    with ctx.conn.cursor() as cur:
        cur.execute(
            "delete from raw.openfema_records where dataset=%s and scope_key=%s",
            (entity, scope.key),
        )
        with cur.copy(
            "copy raw.openfema_records (dataset, version, scope_key, record_id, record, "
            "record_sha256, _object_key, _page, _run_id) from stdin"
        ) as copy:
            for page_no, key in enumerate(keys, start=1):
                doc = json.loads(gzip.decompress(ctx.store.get_bytes(key)))
                for rec in doc[entity]:
                    rec_id = rec.get("id")
                    copy.write_row(
                        [
                            entity,
                            version,
                            scope.key,
                            None if rec_id is None else str(rec_id),
                            json.dumps(rec, ensure_ascii=False),
                            canonical_record_hash(rec),
                            key,
                            page_no,
                            run_id,
                        ]
                    )
                    n += 1
    return n


def ingest(ctx: IngestContext) -> int:
    return sum(ingest_dataset(ctx, e) for e in ctx.sources[SOURCE]["datasets"])
