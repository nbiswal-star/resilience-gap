"""Object storage for raw files and semi-structured payloads.

Keys look like S3 keys (``raw/openfema/<dataset>/...``). ``LocalObjectStore`` maps them onto a
directory so the pipeline runs with no credentials; ``S3ObjectStore`` writes the same keys to any
S3-compatible bucket (AWS S3, SeaweedFS). Switching is a configuration change only.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from .config import Settings


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    uri: str
    sha256: str
    size_bytes: int


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise ValueError(f"Invalid object key: {key!r}")
    return key


class ObjectStore(Protocol):
    def put_file(self, key: str, path: Path) -> ObjectInfo: ...
    def put_bytes(self, key: str, data: bytes) -> ObjectInfo: ...
    def get_bytes(self, key: str) -> bytes: ...
    def open(self, key: str) -> BinaryIO: ...
    def exists(self, key: str) -> bool: ...
    def list(self, prefix: str) -> list[str]: ...
    def uri(self, key: str) -> str: ...


class LocalObjectStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / _validate_key(key)

    def uri(self, key: str) -> str:
        return self._path(key).resolve().as_uri()

    def put_file(self, key: str, path: Path) -> ObjectInfo:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256_file(path)
        # Write to a temp file in the same directory, then atomically rename.
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-")
        os.close(fd)
        try:
            shutil.copyfile(path, tmp)
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return ObjectInfo(key, self.uri(key), digest, dest.stat().st_size)

    def put_bytes(self, key: str, data: bytes) -> ObjectInfo:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return ObjectInfo(key, self.uri(key), hashlib.sha256(data).hexdigest(), len(data))

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def open(self, key: str) -> BinaryIO:
        return open(self._path(key), "rb")

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, prefix: str) -> list[str]:
        base = self.root
        out = []
        for p in base.rglob("*"):
            if p.is_file() and not p.name.startswith(".tmp-"):
                k = p.relative_to(base).as_posix()
                if k.startswith(prefix):
                    out.append(k)
        return sorted(out)


class S3ObjectStore:
    def __init__(self, bucket: str, prefix: str = "", endpoint_url: str | None = None):
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError("S3 backend needs the optional extra: uv sync --extra s3") from exc
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = boto3.client("s3", endpoint_url=endpoint_url)

    def _k(self, key: str) -> str:
        key = _validate_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._k(key)}"

    def put_file(self, key: str, path: Path) -> ObjectInfo:
        digest = sha256_file(path)
        self.client.upload_file(
            str(path), self.bucket, self._k(key), ExtraArgs={"Metadata": {"sha256": digest}}
        )
        return ObjectInfo(key, self.uri(key), digest, Path(path).stat().st_size)

    def put_bytes(self, key: str, data: bytes) -> ObjectInfo:
        digest = hashlib.sha256(data).hexdigest()
        self.client.put_object(
            Bucket=self.bucket, Key=self._k(key), Body=data, Metadata={"sha256": digest}
        )
        return ObjectInfo(key, self.uri(key), digest, len(data))

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()

    def open(self, key: str) -> BinaryIO:
        tmp = tempfile.TemporaryFile()  # noqa: SIM115 - caller closes (used in `with`)
        self.client.download_fileobj(self.bucket, self._k(key), tmp)
        tmp.seek(0)
        return tmp  # type: ignore[return-value]

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except ClientError:
            return False

    def list(self, prefix: str) -> list[str]:
        out: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        strip = len(self.prefix) + 1 if self.prefix else 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self._k(prefix)):
            out.extend(obj["Key"][strip:] for obj in page.get("Contents", []))
        return sorted(out)


def get_object_store(settings: Settings) -> ObjectStore:
    if settings.object_store == "local":
        return LocalObjectStore(settings.lake_dir)
    if settings.object_store == "s3":
        return S3ObjectStore(settings.s3_bucket, settings.s3_prefix, settings.s3_endpoint_url)
    raise ValueError(f"RESGAP_OBJECT_STORE must be 'local' or 's3', got {settings.object_store!r}")
