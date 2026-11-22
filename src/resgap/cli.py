"""Command-line entry point: `resgap <command>` (see README for the full workflow)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import psycopg

from .config import PROJECT_ROOT, load_settings, load_sources
from .db import connect, init_db
from .dq.runner import CriticalDataQualityError, raise_on_critical, run_checks
from .http import SourceUnavailableError, make_session
from .ingest import bls_cpi, noaa_storm_events, nws_zone_county, openfema
from .ingest.context import IngestContext
from .ingest.single_file import SINGLE_FILE_SOURCES, ingest_one
from .objectstore import get_object_store
from .sourcecheck import check_sources, format_table, to_json
from .transform import run_transform, set_active_profile

log = logging.getLogger("resgap")

SOURCES = [
    "census_county_reference",
    "census_popest",
    "census_popest_pr",
    "cdc_svi",
    "cdc_svi_pr",
    "bls_cpi",
    "nws_zone_county",
    "noaa_storm_events",
    "openfema",
]


def _preflight(session, sources: dict, name: str, timeout: int) -> None:
    """Fail fast before downloading: no reachable endpoint, or the schema fingerprint changed."""
    results = check_sources(session, sources, timeout, only=[name], light=True)
    for sc in results:
        if sc.drift and name in ("openfema", "bls_cpi"):
            # JSON sources: an added field is harmless (records land as JSONB) - warn only.
            log.warning("schema fingerprint changed for %s; review the data contract", sc.source)
        elif sc.drift:
            raise SourceUnavailableError(
                f"schema drift at {sc.source}; run `resgap source-check {name}` and review the "
                "data contract before ingesting"
            )
        if sc.chosen is None:
            errs = "; ".join(f"{e.label}: {e.detail}" for e in sc.endpoints if not e.ok)
            raise SourceUnavailableError(f"preflight: no usable endpoint for {sc.source} ({errs})")
        if sc.chosen == "fallback":
            log.warning(
                "preflight: %s primary unavailable, verified fallback will be used", sc.source
            )


def _ingest(args, settings, conn) -> None:
    ctx = IngestContext(
        settings=settings,
        profile=settings.profile,
        conn=conn,
        store=get_object_store(settings),
        session=make_session(settings.user_agent),
        sources=load_sources(),
        force=args.force,
        offline_dir=Path(args.offline_dir).resolve() if args.offline_dir else None,
    )
    set_active_profile(conn, settings)
    selected = args.sources or SOURCES
    failures: list[str] = []
    for name in selected:
        if name not in SOURCES:
            raise SystemExit(f"unknown source {name!r}; choose from {SOURCES}")
        log.info("=== ingest %s (profile=%s)", name, settings.profile_name)
        try:
            if not ctx.offline_dir and not getattr(args, "no_preflight", False):
                _preflight(ctx.session, ctx.sources, name, settings.http_timeout)
            if name in SINGLE_FILE_SOURCES:
                ingest_one(ctx, name)
            elif name == "noaa_storm_events":
                noaa_storm_events.ingest(ctx)
            elif name == "nws_zone_county":
                nws_zone_county.ingest(ctx)
            elif name == "openfema":
                openfema.ingest(ctx)
            elif name == "bls_cpi":
                bls_cpi.ingest(ctx)
        except SourceUnavailableError as exc:
            # Explicit, visible failure; other sources still run so the cause is isolated.
            log.error("SOURCE UNAVAILABLE: %s: %s", name, exc)
            failures.append(name)
    if failures:
        raise SystemExit(f"ingestion failed for: {', '.join(failures)} (see docs/runbook.md)")


def _source_check(args, settings) -> int:
    unknown = [s for s in args.sources if s not in SOURCES]
    if unknown:
        raise SystemExit(f"unknown source(s) {unknown}; choose from {SOURCES}")
    results = check_sources(
        make_session(settings.user_agent),
        load_sources(),
        settings.http_timeout,
        only=args.sources or None,
    )
    print(format_table(results))
    if args.json:
        Path(args.json).write_text(to_json(results), encoding="utf-8")
        print(f"\nwrote {args.json}")
    bad = [r.source for r in results if r.chosen is None or r.drift]
    if bad:
        print(f"\nNOT READY: {', '.join(bad)}")
        return 1
    print(f"\nall {len(results)} source checks usable")
    return 0


def _dq(args, settings, conn) -> None:
    results = run_checks(conn, args.layers)
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    log.info("DQ summary: %s", counts)
    raise_on_critical(results)


def _report(args, settings, conn) -> None:
    from .report.build import build_report

    out = build_report(conn, Path(args.out))
    log.info("report written to %s", out)


def _status(args, settings, conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "select step, status, rows_affected, to_char(started_at,'YYYY-MM-DD HH24:MI:SS'), "
            "left(coalesce(message,''),70) from ops.pipeline_runs order by started_at desc limit 25"
        )
        for row in cur.fetchall():
            print(" | ".join("" if v is None else str(v) for v in row))
        print()
        cur.execute(
            "select schemaname||'.'||relname, n_live_tup from pg_stat_user_tables "
            "where schemaname in ('raw','staging','curated','serving') order by 1"
        )
        for name, n in cur.fetchall():
            print(f"{name:55s} ~{n:>12,}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="resgap", description=__doc__)
    p.add_argument(
        "--profile", help="run profile: sample | full | fixture (default: RESGAP_PROFILE or sample)"
    )
    p.add_argument("-v", "--verbose", action="store_true")
    # --profile is also accepted after the command (resgap run --profile full)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", required=True)
    _add = sub.add_parser

    def add_parser(name, **kw):
        return _add(name, parents=[common], **kw)

    sub.add_parser = add_parser  # type: ignore[method-assign]

    sub.add_parser("init-db", help="create schemas, ops tables and helper functions")

    ing = sub.add_parser("ingest", help="download sources into object storage and raw tables")
    ing.add_argument("sources", nargs="*", help=f"subset of {SOURCES}")
    ing.add_argument("--force", action="store_true", help="re-download and reload")
    ing.add_argument("--offline-dir", help="read real source files from a local directory")
    ing.add_argument(
        "--no-preflight",
        action="store_true",
        help="skip the fail-fast source health check before downloading",
    )

    sub.add_parser("transform", help="build staging, curated and serving tables (SQL)")

    dq = sub.add_parser("dq", help="run data-quality checks")
    dq.add_argument("--layers", nargs="*", choices=["raw", "staging", "curated", "serving"])

    rep = sub.add_parser("report", help="write the HTML findings report")
    rep.add_argument("--out", default=str(PROJECT_ROOT / "reports"))

    run = sub.add_parser("run", help="init-db + ingest + dq(raw) + transform + dq + report")
    run.add_argument("--force", action="store_true", help="re-download and reload")
    run.add_argument("--offline-dir", help="read real source files from a local directory")
    run.add_argument(
        "--no-preflight",
        action="store_true",
        help="skip the fail-fast source health check before downloading",
    )
    run.add_argument("--out", default=str(PROJECT_ROOT / "reports"), help="report directory")

    sub.add_parser("status", help="show recent pipeline runs and table sizes")

    sc = sub.add_parser(
        "source-check", help="probe every source (no large downloads; no database needed)"
    )
    sc.add_argument("sources", nargs="*", help=f"subset of {SOURCES}")
    sc.add_argument("--json", help="also write the results to this JSON file")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    settings = load_settings(args.profile)
    if args.command == "source-check":
        return _source_check(args, settings)
    conn = connect(settings.database_url)
    try:
        if args.command == "init-db":
            init_db(conn)
        elif args.command == "ingest":
            init_db(conn)
            _ingest(args, settings, conn)
        elif args.command == "transform":
            # idempotent; makes SQL helper functions added since the database was created
            # available to the models (e.g. staging.hma_period_start)
            init_db(conn)
            run_transform(conn, settings)
        elif args.command == "dq":
            _dq(args, settings, conn)
        elif args.command == "report":
            _report(args, settings, conn)
        elif args.command == "status":
            _status(args, settings, conn)
        elif args.command == "run":
            init_db(conn)
            args.sources = None
            _ingest(args, settings, conn)
            raise_on_critical(run_checks(conn, ["raw"]))
            run_transform(conn, settings)
            raise_on_critical(run_checks(conn, ["staging", "curated", "serving"]))
            _report(args, settings, conn)
    except CriticalDataQualityError as exc:
        log.error("%s", exc)
        return 2
    except psycopg.Error as exc:
        log.error("database step failed: %s", str(exc).strip().splitlines()[0])
        log.error("details are recorded in ops.pipeline_runs (resgap status)")
        return 3
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
