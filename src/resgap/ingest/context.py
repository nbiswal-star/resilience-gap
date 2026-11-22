"""Shared state passed to every ingestion module."""

from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
import requests

from ..config import Profile, Settings
from ..db import record_source_object
from ..http import SourceUnavailableError, download
from ..objectstore import ObjectInfo, ObjectStore

log = logging.getLogger(__name__)


@dataclass
class IngestContext:
    settings: Settings
    profile: Profile
    conn: psycopg.Connection
    store: ObjectStore
    session: requests.Session
    sources: dict[str, Any]
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    force: bool = False
    # When set, sources read real files from this directory instead of the network
    # (used by tests and offline demos; see tests/fixtures/README.md).
    offline_dir: Path | None = None

    def fetch_to_store(
        self,
        *,
        source: str,
        url: str,
        key: str,
        partition_key: str,
        allowed_hosts: list[str] | None = None,
        expected_sha1_base32: str | None = None,
    ) -> ObjectInfo:
        """Download ``url`` into the object store under ``key`` (immutable; reused if present).

        ``allowed_hosts`` defaults to the source's registry entry; every redirect must stay on
        them. ``expected_sha1_base32`` (archive captures) must match the downloaded bytes.
        """
        if self.store.exists(key) and not self.force:
            # Reuse only an object this database recorded as downloaded from the same URL.
            data_sha = _stored_sha(self.conn, key, url)
            if data_sha:
                log.info("reuse stored object %s", key)
                return ObjectInfo(key, self.store.uri(key), data_sha, -1)
        hosts = (
            allowed_hosts
            if allowed_hosts is not None
            else self.sources.get(source.split(":")[0], {}).get("allowed_hosts")
        )
        dl = download(self.session, url, self.settings.http_timeout, allowed_hosts=hosts)
        try:
            if expected_sha1_base32:
                got = base64.b32encode(bytes.fromhex(dl.sha1)).decode()
                if got != expected_sha1_base32:
                    raise SourceUnavailableError(
                        f"{url}: SHA-1 {got} does not match the expected {expected_sha1_base32}"
                    )
            info = self.store.put_file(key, dl.path)
        finally:
            dl.path.unlink(missing_ok=True)
        record_source_object(
            self.conn,
            run_id=self.run_id,
            source=source,
            partition_key=partition_key,
            source_url=url,
            info=info,
            content_type=dl.content_type,
            etag=dl.etag,
            last_modified=dl.last_modified,
        )
        self.conn.commit()
        log.info("stored %s (%s bytes, sha256 %s…)", key, info.size_bytes, info.sha256[:12])
        return info

    def stage_local_file(
        self, *, source: str, path: Path, key: str, partition_key: str
    ) -> ObjectInfo:
        """Copy a local file (offline mode) into the object store with provenance.

        Offline copies live under a separate ``offline/`` namespace so they can never be
        mistaken for (and reused instead of) the full file downloaded from the source.
        """
        key = f"offline/{key}"
        info = self.store.put_file(key, path)
        record_source_object(
            self.conn,
            run_id=self.run_id,
            source=source,
            partition_key=partition_key,
            source_url=Path(path).resolve().as_uri(),
            info=info,
        )
        self.conn.commit()
        return info


def _stored_sha(conn: psycopg.Connection, key: str, url: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            "select sha256 from ops.source_objects where object_key=%s and source_url=%s",
            (key, url),
        )
        row = cur.fetchone()
    return row[0] if row else None
