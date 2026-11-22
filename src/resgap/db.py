"""PostgreSQL helpers: connections, SQL file execution, and run/provenance bookkeeping."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg

from .config import SQL_DIR

log = logging.getLogger(__name__)


def connect(database_url: str) -> psycopg.Connection:
    try:
        return psycopg.connect(database_url, autocommit=False)
    except psycopg.OperationalError as exc:
        raise SystemExit(
            f"Cannot connect to PostgreSQL ({_redact(database_url)}). "
            "Start it with `make up` (Docker) or set RESGAP_DATABASE_URL.\n"
            f"Driver error: {exc}"
        ) from exc


def _redact(url: str) -> str:
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        return f"{scheme}://{creds.split(':')[0]}:***@{host}"
    return url


def run_sql_file(conn: psycopg.Connection, path: Path) -> float:
    """Execute one SQL file inside the caller's transaction. Returns elapsed seconds."""
    started = time.perf_counter()
    sql = Path(path).read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)  # type: ignore[arg-type]
    return time.perf_counter() - started


def sql_files(layer: str) -> list[Path]:
    """SQL files for a layer, in lexical (= execution) order."""
    return sorted((SQL_DIR / layer).glob("*.sql"))


def init_db(conn: psycopg.Connection) -> None:
    for layer in ("init", "functions", "raw"):
        for path in sql_files(layer):
            log.info("init: %s", path.relative_to(SQL_DIR))
            run_sql_file(conn, path)
    conn.commit()


def scalar(conn: psycopg.Connection, sql: str, params: Any = None) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql, params)  # type: ignore[arg-type]
        row = cur.fetchone()
    return row[0] if row else None


def table_exists(conn: psycopg.Connection, qualified: str) -> bool:
    return scalar(conn, "select to_regclass(%s) is not null", (qualified,))


@contextmanager
def pipeline_run(
    conn: psycopg.Connection, step: str, profile: str | None, params: dict | None = None
) -> Iterator[dict]:
    """Record a pipeline step in ops.pipeline_runs.

    The bookkeeping row is committed independently of the step's own transaction so that a
    failure is still visible afterwards. Yields a dict the step can update with
    ``rows_affected``, ``status`` ('succeeded' or 'unchanged') and ``message``.
    """
    run_id = uuid.uuid4()
    state: dict[str, Any] = {"run_id": run_id, "rows_affected": 0, "status": "succeeded"}
    with conn.cursor() as cur:
        cur.execute(
            "insert into ops.pipeline_runs (run_id, step, profile, status, params) "
            "values (%s, %s, %s, 'running', %s)",
            (run_id, step, profile, json.dumps(params or {}, default=str)),
        )
    conn.commit()
    try:
        yield state
    except BaseException as exc:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                "update ops.pipeline_runs set status='failed', finished_at=now(), message=%s "
                "where run_id=%s",
                (f"{type(exc).__name__}: {exc}"[:4000], run_id),
            )
        conn.commit()
        raise
    else:
        with conn.cursor() as cur:
            cur.execute(
                "update ops.pipeline_runs set status=%s, finished_at=now(), rows_affected=%s, "
                "message=%s where run_id=%s",
                (state["status"], state["rows_affected"], state.get("message"), run_id),
            )
        conn.commit()


def record_source_object(
    conn: psycopg.Connection,
    *,
    run_id: uuid.UUID,
    source: str,
    partition_key: str,
    source_url: str,
    info: Any,
    content_type: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into ops.source_objects
              (object_key, object_uri, source, partition_key, source_url, sha256, size_bytes,
               content_type, http_etag, http_last_modified, run_id)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (object_key) do update set
              sha256 = excluded.sha256, size_bytes = excluded.size_bytes,
              retrieved_at = now(), run_id = excluded.run_id
            """,
            (
                info.key,
                info.uri,
                source,
                partition_key,
                source_url,
                info.sha256,
                info.size_bytes,
                content_type,
                etag,
                last_modified,
                run_id,
            ),
        )


def current_raw_load(conn: psycopg.Connection, raw_table: str, partition_key: str) -> str | None:
    return scalar(
        conn,
        "select content_sha256 from ops.raw_loads where raw_table=%s and partition_key=%s",
        (raw_table, partition_key),
    )


def record_raw_load(
    conn: psycopg.Connection,
    *,
    run_id: uuid.UUID,
    source: str,
    raw_table: str,
    partition_key: str,
    object_keys: list[str],
    content_sha256: str,
    rows_loaded: int,
    rows_rejected: int,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into ops.raw_loads (raw_table, partition_key, source, object_keys,
              content_sha256, rows_loaded, rows_rejected, run_id)
            values (%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (raw_table, partition_key) do update set
              source = excluded.source, object_keys = excluded.object_keys,
              content_sha256 = excluded.content_sha256, rows_loaded = excluded.rows_loaded,
              rows_rejected = excluded.rows_rejected, run_id = excluded.run_id, loaded_at = now()
            """,
            (
                raw_table,
                partition_key,
                source,
                object_keys,
                content_sha256,
                rows_loaded,
                rows_rejected,
                run_id,
            ),
        )
