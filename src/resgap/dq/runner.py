"""Run declarative data-quality checks (config/dq_checks.yml) and log results to ops.dq_results."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg
import yaml

from ..config import CONFIG_DIR

log = logging.getLogger(__name__)
LAYERS = ("raw", "staging", "curated", "serving")


@dataclass
class CheckResult:
    name: str
    layer: str
    severity: str
    status: str
    failing: float | None
    total: float | None
    ratio: float | None
    max_ratio: float
    description: str
    handling: str
    sample: list[dict[str, Any]] | None = None


class CriticalDataQualityError(RuntimeError):
    pass


def load_checks() -> list[dict[str, Any]]:
    with open(CONFIG_DIR / "dq_checks.yml", encoding="utf-8") as fh:
        checks = yaml.safe_load(fh)["checks"]
    names = [c["name"] for c in checks]
    if len(names) != len(set(names)):
        raise ValueError("duplicate DQ check names")
    for c in checks:
        if c["layer"] not in LAYERS or c["severity"] not in ("critical", "warn", "info"):
            raise ValueError(f"bad layer/severity in check {c['name']}")
    return checks


def evaluate(failing: float, total: float, max_ratio: float) -> tuple[float | None, bool]:
    if total in (None, 0):
        return None, (failing or 0) == 0
    ratio = float(failing) / float(total)
    return ratio, ratio <= max_ratio + 1e-12


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    return v if isinstance(v, (int, float, str, bool, type(None))) else str(v)


def run_checks(conn: psycopg.Connection, layers: list[str] | None = None) -> list[CheckResult]:
    layers = layers or list(LAYERS)
    dq_run_id = uuid.uuid4()
    results: list[CheckResult] = []
    for c in load_checks():
        if c["layer"] not in layers:
            continue
        max_ratio = float(c.get("max_fail_ratio", 0))
        sample = None
        try:
            with conn.cursor() as cur:
                cur.execute(c["sql"])
                failing, total = cur.fetchone()
                failing = float(failing or 0)
                total = float(total or 0)
                if c.get("sample_sql") and failing > 0:
                    cur.execute(c["sample_sql"])
                    cols = [d.name for d in cur.description]
                    sample = [
                        {k: _jsonable(v) for k, v in zip(cols, row, strict=True)}
                        for row in cur.fetchall()
                    ]
            conn.commit()
            ratio, ok = evaluate(failing, total, max_ratio)
            if ok:
                status = "pass"
            elif c["severity"] == "critical":
                status = "fail"
            else:
                status = "warn"
        except psycopg.Error as exc:
            conn.rollback()
            failing = total = ratio = None
            status = "error"
            sample = [{"error": str(exc).splitlines()[0]}]
        r = CheckResult(
            c["name"],
            c["layer"],
            c["severity"],
            status,
            failing,
            total,
            ratio,
            max_ratio,
            c.get("description", ""),
            c.get("handling", ""),
            sample,
        )
        results.append(r)
        with conn.cursor() as cur:
            cur.execute(
                """insert into ops.dq_results (dq_run_id, check_name, layer, severity, status,
                     failing_rows, total_rows, fail_ratio, max_fail_ratio, description, handling,
                     sample) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    dq_run_id,
                    r.name,
                    r.layer,
                    r.severity,
                    r.status,
                    r.failing,
                    r.total,
                    r.ratio,
                    r.max_ratio,
                    r.description,
                    r.handling,
                    json.dumps(r.sample) if r.sample is not None else None,
                ),
            )
        conn.commit()
        level = logging.INFO if status == "pass" else logging.WARNING
        log.log(
            level,
            "DQ %-8s %-40s failing=%s total=%s",
            status.upper(),
            r.name,
            _fmt(failing),
            _fmt(total),
        )
    return results


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:,.0f}"


def raise_on_critical(results: list[CheckResult]) -> None:
    bad = [r for r in results if r.status in ("fail", "error") and r.severity == "critical"]
    errored = [r for r in results if r.status == "error"]
    if bad or errored:
        names = ", ".join(sorted({r.name for r in bad + errored}))
        raise CriticalDataQualityError(f"Critical data-quality failure(s): {names}")
