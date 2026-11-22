"""Load delimited text files into all-TEXT raw tables, faithfully.

Raw-layer rules (course requirement C.10):
* every source column is stored as TEXT exactly as it appears (empty string stays ''),
* no keys or constraints that could reject a problematic record,
* lineage columns record where each row came from,
* rows with the wrong number of fields are not dropped silently: they go to ops.raw_rejects,
* a header that differs from the existing raw table is schema drift and fails loudly.
"""

from __future__ import annotations

import csv
import gzip
import io
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import BinaryIO

import psycopg
from psycopg import sql

LINEAGE_COLUMNS = ("_partition_key", "_object_key", "_source_row", "_run_id", "_loaded_at")
csv.field_size_limit(16 * 1024 * 1024)  # long narrative fields


class SchemaDriftError(RuntimeError):
    """The source header no longer matches the raw table - a critical, loud failure."""


@dataclass(frozen=True)
class LoadResult:
    rows_loaded: int
    rows_rejected: int
    columns: tuple[str, ...]


def sanitize_column(name: str) -> str:
    name = name.replace("﻿", "").strip().lower()
    name = re.sub(r"[^a-z0-9_]+", "_", name).strip("_")
    if not name:
        name = "col"
    if name[0].isdigit():
        name = f"c_{name}"
    return name


def sanitize_header(header: Iterable[str]) -> tuple[str, ...]:
    seen: dict[str, int] = {}
    out = []
    for raw in header:
        col = sanitize_column(raw)
        if col in seen:
            seen[col] += 1
            col = f"{col}_{seen[col]}"
        else:
            seen[col] = 0
        out.append(col)
    return tuple(out)


def _split_table(qualified: str) -> tuple[str, str]:
    schema, _, table = qualified.partition(".")
    return schema, table


def existing_columns(conn: psycopg.Connection, qualified: str) -> tuple[str, ...] | None:
    schema, table = _split_table(qualified)
    with conn.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema=%s and table_name=%s order by ordinal_position",
            (schema, table),
        )
        cols = [r[0] for r in cur.fetchall()]
    if not cols:
        return None
    return tuple(c for c in cols if c not in LINEAGE_COLUMNS)


def ensure_raw_table(conn: psycopg.Connection, qualified: str, columns: tuple[str, ...]) -> None:
    current = existing_columns(conn, qualified)
    if current is None:
        schema, table = _split_table(qualified)
        col_defs = [sql.SQL("{} text").format(sql.Identifier(c)) for c in columns]
        col_defs += [
            sql.SQL("_partition_key text not null"),
            sql.SQL("_object_key text not null"),
            sql.SQL("_source_row integer not null"),
            sql.SQL("_run_id uuid not null"),
            sql.SQL("_loaded_at timestamptz not null default now()"),
        ]
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("create table {}.{} ({})").format(
                    sql.Identifier(schema), sql.Identifier(table), sql.SQL(", ").join(col_defs)
                )
            )
            cur.execute(
                sql.SQL("create index on {}.{} (_partition_key)").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            )
        return
    if current != columns:
        added = sorted(set(columns) - set(current))
        removed = sorted(set(current) - set(columns))
        raise SchemaDriftError(
            f"{qualified}: source header changed (added={added}, removed={removed}). "
            "Review the data contract (docs/data-contracts.md) before reloading."
        )


def open_text(stream: BinaryIO, *, gz: bool, encoding: str) -> io.TextIOWrapper:
    raw: BinaryIO = gzip.GzipFile(fileobj=stream) if gz else stream  # type: ignore[assignment]
    return io.TextIOWrapper(raw, encoding=encoding, newline="")


def load_delimited(
    conn: psycopg.Connection,
    *,
    stream: BinaryIO,
    raw_table: str,
    partition_key: str,
    object_key: str,
    run_id: uuid.UUID,
    delimiter: str = ",",
    encoding: str = "utf-8",
    gz: bool = False,
    columns: tuple[str, ...] | None = None,
    quoting: int = csv.QUOTE_MINIMAL,
    replace_partition: bool = True,
) -> LoadResult:
    """Replace one partition of a raw table with the rows of one file (single transaction).

    If ``columns`` is given the file has no header row. The caller commits.
    """
    text = open_text(stream, gz=gz, encoding=encoding)
    reader = csv.reader(text, delimiter=delimiter, quoting=quoting)
    if columns is None:
        try:
            header = next(reader)
        except StopIteration as exc:
            raise SchemaDriftError(f"{object_key}: file is empty") from exc
        cols = sanitize_header(header)
        first_data_row = 2
    else:
        cols = tuple(columns)
        first_data_row = 1
    ensure_raw_table(conn, raw_table, cols)
    schema, table = _split_table(raw_table)
    ident = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))
    loaded = rejected = 0
    with conn.cursor() as cur:
        if replace_partition:
            cur.execute(
                sql.SQL("delete from {} where _partition_key = %s").format(ident), (partition_key,)
            )
        rejects: list[tuple] = []
        target_cols = [*cols, "_partition_key", "_object_key", "_source_row", "_run_id"]
        copy_sql = sql.SQL("copy {} ({}) from stdin").format(
            ident, sql.SQL(", ").join(sql.Identifier(c) for c in target_cols)
        )
        with cur.copy(copy_sql) as copy:
            for i, row in enumerate(reader, start=first_data_row):
                if len(row) != len(cols):
                    if not any(field.strip() for field in row):
                        continue  # blank line, e.g. trailing newline
                    rejected += 1
                    rejects.append(
                        (
                            run_id,
                            object_key,
                            i,
                            f"expected {len(cols)} fields, got {len(row)}",
                            delimiter.join(row)[:10000],
                        )
                    )
                    continue
                if any("\x00" in f for f in row):  # PostgreSQL text cannot hold NUL bytes
                    row = [f.replace("\x00", "") for f in row]
                copy.write_row([*row, partition_key, object_key, i, run_id])
                loaded += 1
        if rejects:
            cur.executemany(
                "insert into ops.raw_rejects (run_id, object_key, source_row, reason, raw_line) "
                "values (%s,%s,%s,%s,%s)",
                rejects,
            )
    return LoadResult(loaded, rejected, cols)
