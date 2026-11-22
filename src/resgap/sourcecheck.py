"""Source health checks: availability, redirects, content type, schema fingerprint, freshness.

No large file is downloaded: delimited files are streamed only until their first line (gzip is
decompressed incrementally), APIs are asked for one record, fallbacks are probed with HEAD.
Used by `resgap source-check` and as a fail-fast preflight before ingestion.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any

import requests

from .http import SourceUnavailableError, validate_redirects
from .ingest.noaa_storm_events import parse_listing
from .ingest.nws_zone_county import parse_landing
from .ingest.rawload import sanitize_header

MAX_PROBE_BYTES = 2 << 20  # never read more than 2 MB of any file


def header_fingerprint(columns: list[str]) -> str:
    return hashlib.sha256(",".join(sanitize_header(columns)).encode()).hexdigest()[:16]


def keys_fingerprint(keys) -> str:
    return hashlib.sha256(",".join(sorted(keys)).encode()).hexdigest()[:16]


@dataclass
class Endpoint:
    role: str  # primary | fallback
    label: str
    url: str
    ok: bool = False
    status: int | None = None
    final_url: str | None = None
    redirects: int = 0
    content_type: str | None = None
    content_length: str | None = None
    last_modified: str | None = None
    fingerprint: str | None = None
    expected_fingerprint: str | None = None
    detail: str = ""

    @property
    def drift(self) -> bool:
        return bool(
            self.expected_fingerprint
            and self.fingerprint
            and self.fingerprint != self.expected_fingerprint
        )


@dataclass
class SourceCheck:
    source: str
    endpoints: list[Endpoint] = field(default_factory=list)

    @property
    def chosen(self) -> str | None:
        for role in ("primary", "fallback"):
            group = [e for e in self.endpoints if e.role == role]
            if group and all(e.ok and not e.drift for e in group):
                return role
        return None

    @property
    def drift(self) -> bool:
        return any(e.drift for e in self.endpoints)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "chosen": self.chosen,
            "drift": self.drift,
            "endpoints": [asdict(e) for e in self.endpoints],
        }


def _meta(ep: Endpoint, r: requests.Response) -> None:
    ep.status = r.status_code
    ep.final_url = r.url
    ep.redirects = len(r.history)
    ep.content_type = r.headers.get("Content-Type")
    ep.content_length = r.headers.get("Content-Length")
    ep.last_modified = r.headers.get("Last-Modified")


def _first_line(session, ep: Endpoint, hosts, timeout: int, gz: bool, encoding: str) -> str:
    with session.get(ep.url, stream=True, timeout=timeout) as r:
        _meta(ep, r)
        r.raise_for_status()
        validate_redirects(r, hosts)
        if (ep.content_type or "").startswith("text/html"):
            raise SourceUnavailableError("served an HTML page instead of a data file")
        dec = zlib.decompressobj(16 + zlib.MAX_WBITS) if gz else None
        buf, read = b"", 0
        for chunk in r.iter_content(chunk_size=16384):
            read += len(chunk)
            buf += dec.decompress(chunk) if dec else chunk
            if b"\n" in buf or read > MAX_PROBE_BYTES:
                break
    line = buf.split(b"\n", 1)[0].rstrip(b"\r")
    return line.decode(encoding)


def _head(session, ep: Endpoint, hosts, timeout: int, ok_types=()) -> None:
    r = session.head(ep.url, timeout=timeout, allow_redirects=True)
    _meta(ep, r)
    r.raise_for_status()
    validate_redirects(r, hosts)
    if ok_types and not any((ep.content_type or "").startswith(t) for t in ok_types):
        raise SourceUnavailableError(f"unexpected content type {ep.content_type}")


def _run(ep: Endpoint, fn) -> Endpoint:
    try:
        fn()
        ep.ok = True
    except (requests.RequestException, SourceUnavailableError, ValueError, zlib.error) as exc:
        ep.ok = False
        ep.detail = (ep.detail + " " if ep.detail else "") + f"{type(exc).__name__}: {exc}"[:300]
    return ep


def check_csv(session, name: str, cfg: dict, timeout: int) -> SourceCheck:
    sc = SourceCheck(name)
    ep = Endpoint(
        "primary",
        PurePosixPath(cfg["url"]).name,
        cfg["url"],
        expected_fingerprint=cfg["health"].get("fingerprint") or None,
    )

    def go():
        line = _first_line(
            session,
            ep,
            cfg["allowed_hosts"],
            timeout,
            gz=False,
            encoding=cfg.get("encoding", "utf-8"),
        )
        cols = next(csv.reader(io.StringIO(line), delimiter=cfg.get("delimiter", ",")))
        ep.fingerprint = header_fingerprint(cols)
        ep.detail = f"{len(cols)} columns"

    sc.endpoints.append(_run(ep, go))
    return sc


def check_noaa(session, cfg: dict, timeout: int) -> SourceCheck:
    sc = SourceCheck("noaa_storm_events")
    listing = Endpoint("primary", "directory listing", cfg["listing_url"])
    files: dict[int, str] = {}

    def go_listing():
        with session.get(listing.url, timeout=timeout) as r:
            _meta(listing, r)
            r.raise_for_status()
            validate_redirects(r, cfg["allowed_hosts"])
            files.update(parse_listing(r.text, cfg["file_pattern"]))
        if not files:
            raise SourceUnavailableError("no details files found in listing")
        listing.detail = f"{len(files)} yearly files ({min(files)}-{max(files)})"

    sc.endpoints.append(_run(listing, go_listing))
    if files:
        newest = files[max(files)]
        ep = Endpoint(
            "primary",
            newest,
            cfg["listing_url"] + newest,
            expected_fingerprint=cfg["health"].get("fingerprint") or None,
        )

        def go_file():
            line = _first_line(
                session, ep, cfg["allowed_hosts"], timeout, gz=True, encoding="utf-8"
            )
            cols = next(csv.reader(io.StringIO(line)))
            ep.fingerprint = header_fingerprint(cols)
            ep.detail = f"{len(cols)} columns"

        sc.endpoints.append(_run(ep, go_file))
    return sc


def check_nws(session, cfg: dict, timeout: int) -> SourceCheck:
    sc = SourceCheck("nws_zone_county")
    landing = Endpoint("primary", "landing page", cfg["landing_page"])
    linked: list[str] = []

    def go_landing():
        with session.get(landing.url, timeout=timeout) as r:
            _meta(landing, r)
            r.raise_for_status()
            validate_redirects(r, cfg["allowed_hosts"])
            linked.extend(parse_landing(r.text, cfg["file_pattern"]))
        if not linked:
            raise SourceUnavailableError("no correlation files linked")
        landing.detail = "linked: " + ", ".join(linked)

    sc.endpoints.append(_run(landing, go_landing))
    newest = linked[-1] if linked else None
    if newest:
        ep = Endpoint(
            "primary",
            newest,
            cfg["base_url"] + newest,
            expected_fingerprint=cfg["health"].get("fingerprint") or None,
        )

        def go_file():
            line = _first_line(
                session, ep, cfg["allowed_hosts"], timeout, gz=False, encoding="latin-1"
            )
            n = len(line.split("|"))
            ep.fingerprint = keys_fingerprint([f"fields={n}"])
            ep.detail = f"{n} pipe-delimited fields"
            if n != len(cfg["columns"]):
                raise SourceUnavailableError(f"expected {len(cfg['columns'])} fields, got {n}")

        sc.endpoints.append(_run(ep, go_file))
    archive = cfg.get("archive", {})
    caps = [c for c in archive.get("captures", []) if c.get("role") == "fallback_for_nws_hosted"]
    if caps:
        c = sorted(caps, key=lambda c: c["timestamp"])[-1]
        fb = Endpoint(
            "fallback", f"archive capture of {c['file']}", archive["url_template"].format(**c)
        )
        sc.endpoints.append(_run(fb, lambda: _head(session, fb, archive["allowed_hosts"], timeout)))
    return sc


def check_openfema(session, cfg: dict, timeout: int) -> list[SourceCheck]:
    out = []
    for entity, ds in cfg["datasets"].items():
        sc = SourceCheck(f"openfema:{entity}")
        url = f"{cfg['api_base']}/{ds['version']}/{entity}"
        ep = Endpoint(
            "primary", "API ($top=1)", url, expected_fingerprint=ds.get("fingerprint") or None
        )

        def go(ep=ep, url=url, entity=entity):
            r = session.get(
                url, params={"$top": 1, "$count": "true", "$orderby": "id"}, timeout=timeout
            )
            _meta(ep, r)
            r.raise_for_status()
            validate_redirects(r, cfg["allowed_hosts"])
            doc = r.json()
            recs = doc.get(entity) or []
            if not recs:
                raise SourceUnavailableError("API returned no records")
            ep.fingerprint = keys_fingerprint(recs[0].keys())
            ep.detail = f"{doc.get('metadata', {}).get('count')} records available"

        sc.endpoints.append(_run(ep, go))
        bulk = f"{url}.{cfg.get('bulk_format', 'jsonl')}"
        fb = Endpoint("fallback", "bulk export (HEAD)", bulk)
        sc.endpoints.append(
            _run(
                fb,
                lambda fb=fb: _head(
                    session, fb, cfg["allowed_hosts"], timeout, ok_types=("application/jsonl",)
                ),
            )
        )
        out.append(sc)
    return out


def check_bls(session, cfg: dict, timeout: int, light: bool = False) -> SourceCheck:
    """light=True only sends HEAD (does not spend one of the 25 daily API v1 queries)."""
    sc = SourceCheck("bls_cpi")
    url = cfg["api_url"] + cfg["series_id"]
    ep = Endpoint(
        "primary",
        "API v1 latest" if not light else "API v1 (HEAD)",
        url,
        expected_fingerprint=None if light else cfg["health"].get("fingerprint") or None,
    )

    def go():
        if light:
            _head(session, ep, cfg["allowed_hosts"], timeout, ok_types=("application/json",))
            return
        r = session.get(url, timeout=timeout)
        _meta(ep, r)
        r.raise_for_status()
        validate_redirects(r, cfg["allowed_hosts"])
        doc = r.json()
        if doc.get("status") != "REQUEST_SUCCEEDED":
            raise SourceUnavailableError(f"{doc.get('status')}: {doc.get('message')}")
        data = doc["Results"]["series"][0]["data"]
        keys = set(data[0]) - {"latest"}
        ep.fingerprint = keys_fingerprint(keys)
        latest = next((d for d in data if d.get("latest") == "true"), data[0])
        ep.detail = f"latest {latest['year']}-{latest['period']} = {latest['value']}"

    sc.endpoints.append(_run(ep, go))
    return sc


def check_sources(
    session, sources: dict, timeout: int, only: list[str] | None = None, light: bool = False
) -> list[SourceCheck]:
    results: list[SourceCheck] = []
    for name, cfg in sources.items():
        if only and name not in only:
            continue
        kind = cfg.get("health", {}).get("kind")
        if kind == "csv_header":
            results.append(check_csv(session, name, cfg, timeout))
        elif kind == "noaa_listing":
            results.append(check_noaa(session, cfg, timeout))
        elif kind == "nws_landing":
            results.append(check_nws(session, cfg, timeout))
        elif kind == "openfema":
            results.extend(check_openfema(session, cfg, timeout))
        elif kind == "bls_api":
            results.append(check_bls(session, cfg, timeout, light=light))
    return results


def format_table(results: list[SourceCheck]) -> str:
    lines = []
    for sc in results:
        state = sc.chosen or "UNAVAILABLE"
        if sc.drift:
            state += " (SCHEMA DRIFT)"
        lines.append(f"{sc.source:40s} -> {state}")
        for e in sc.endpoints:
            fp = ""
            if e.fingerprint:
                match = (
                    ""
                    if not e.expected_fingerprint
                    else " match"
                    if not e.drift
                    else f" != expected {e.expected_fingerprint}"
                )
                fp = f" fp={e.fingerprint}{match}"
            lines.append(
                f"   {e.role:8s} {'OK ' if e.ok else 'ERR'} {e.status or '-'} {e.label}"
                f" | type={e.content_type or '-'} len={e.content_length or '-'}"
                f" modified={e.last_modified or '-'} redirects={e.redirects}{fp}"
                + (f" | {e.detail}" if e.detail else "")
            )
    return "\n".join(lines)


def to_json(results: list[SourceCheck]) -> str:
    return json.dumps([r.to_dict() for r in results], indent=2)
