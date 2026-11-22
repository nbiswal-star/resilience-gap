"""Run the SQL models layer by layer: staging -> curated -> serving.

Every model is a full, deterministic rebuild (DROP + CREATE TABLE AS), so re-running a transform
on unchanged raw data produces identical tables (idempotent). Each SQL file runs in its own
transaction and is logged in ops.pipeline_runs.
"""

from __future__ import annotations

import csv
import logging

import psycopg
from psycopg import sql

from .config import SEEDS_DIR, SQL_DIR, Settings, load_sources
from .db import pipeline_run, run_sql_file, sql_files

log = logging.getLogger(__name__)
TRANSFORM_LAYERS = ("staging", "curated", "serving")


def set_active_profile(conn: psycopg.Connection, settings: Settings) -> None:
    p = settings.profile
    a = settings.analysis
    inflation = a.get("inflation", {})
    with conn.cursor() as cur:
        cur.execute("delete from ops.active_profile")
        cur.execute(
            """insert into ops.active_profile (profile, start_year, end_year, states,
                 significant_damage_real_usd, significant_casualties, match_days_before,
                 match_days_after, open_incident_cap_days, cpi_base_year, cpi_series_id,
                 zone_gap_threshold_days)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                p.name,
                p.start_year,
                p.end_year,
                list(p.states) if p.states else None,
                significant_damage_real_usd(a),
                a.get("significant_casualties", 1),
                a.get("match_window_days_before", 1),
                a.get("match_window_days_after", 1),
                a.get("open_incident_cap_days", 60),
                int(inflation.get("base_year", 2025)),
                inflation.get("series_id", "CUUR0000SA0"),
                int(load_sources()["nws_zone_county"].get("gap_threshold_days", 400)),
            ),
        )
    conn.commit()


def significant_damage_real_usd(analysis: dict) -> float:
    """Significance threshold in constant base-year dollars (`significant_damage_real_usd`).

    The older key `significant_damage_usd` is still accepted; it is interpreted the same way.
    """
    return float(
        analysis.get("significant_damage_real_usd", analysis.get("significant_damage_usd", 100000))
    )


# seed file stem -> primary-key columns. Seeds load as TEXT; SQL models cast where needed.
SEEDS = {
    "hazard_crosswalk": ("source", "source_value"),
    "storm_events_state_codes": ("storm_state_fips",),
    "county_name_aliases": ("state_abbr", "alias"),
    "fema_regions": ("state_abbr",),
    "hazard_match_rules": ("declaration_hazard_family", "event_hazard_family"),
}


def load_seeds(conn: psycopg.Connection) -> int:
    total = 0
    for stem, key in SEEDS.items():
        with open(SEEDS_DIR / f"{stem}.csv", newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            rows = [r for r in reader if r]
        idx = [header.index(k) for k in key]
        keys = [tuple(r[i] for i in idx) for r in rows]
        if len(keys) != len(set(keys)):
            raise ValueError(f"seeds/{stem}.csv has duplicate keys {key}")
        table = sql.Identifier("staging", f"seed_{stem}")
        cols = sql.SQL(", ").join(
            sql.SQL("{} text not null").format(sql.Identifier(c)) for c in header
        )
        pk = sql.SQL(", ").join(sql.Identifier(k) for k in key)
        with conn.cursor() as cur:
            cur.execute(sql.SQL("drop table if exists {}").format(table))
            cur.execute(sql.SQL("create table {} ({}, primary key ({}))").format(table, cols, pk))
            with cur.copy(sql.SQL("copy {} from stdin").format(table)) as copy:
                for r in rows:
                    copy.write_row([v.strip() for v in r])
        total += len(rows)
    conn.commit()
    return total


def run_transform(
    conn: psycopg.Connection, settings: Settings, layers: tuple[str, ...] = TRANSFORM_LAYERS
) -> None:
    set_active_profile(conn, settings)
    n = load_seeds(conn)
    log.info("seeds loaded: %s rows", n)
    for layer in layers:
        for path in sql_files(layer):
            rel = path.relative_to(SQL_DIR).as_posix()
            with pipeline_run(conn, f"transform:{rel}", settings.profile_name) as run:
                secs = run_sql_file(conn, path)
                conn.commit()
                run["message"] = f"{secs:.1f}s"
                log.info("transform %-45s %6.1fs", rel, secs)
