"""HTTP session with retries, timeouts, redirect validation and streaming downloads."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class SourceUnavailableError(RuntimeError):
    """Raised when a source cannot be reached or returns unusable content."""


@dataclass(frozen=True)
class Download:
    path: Path
    sha256: str
    sha1: str
    size_bytes: int
    content_type: str | None
    etag: str | None
    last_modified: str | None
    final_url: str


def make_session(user_agent: str, retries: int = 5) -> requests.Session:
    retry = Retry(
        total=retries,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD", "POST"),
        respect_retry_after_header=True,
    )
    s = requests.Session()
    s.headers["User-Agent"] = user_agent
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def validate_redirects(response: requests.Response, allowed_hosts: Iterable[str] | None) -> None:
    """Every hop and the final URL must be on an allowed host, and the final URL must be HTTPS.

    A redirect to another host (e.g. a retired download that now lands on a landing page or a
    different agency site) is treated as the source being unavailable, never followed silently.
    """
    if not allowed_hosts:
        return
    allowed = {h.lower() for h in allowed_hosts}
    hops = [h.url for h in response.history] + [response.url]
    for url in hops:
        host = (urlparse(url).hostname or "").lower()
        if host not in allowed:
            raise SourceUnavailableError(
                f"redirected to unexpected host {host!r} ({url}); allowed: {sorted(allowed)}"
            )
    if urlparse(response.url).scheme != "https":
        raise SourceUnavailableError(f"final URL is not HTTPS: {response.url}")


def get_text(
    session: requests.Session,
    url: str,
    timeout: int,
    allowed_hosts: Iterable[str] | None = None,
) -> str:
    try:
        r = session.get(url, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise SourceUnavailableError(f"GET {url} failed: {exc}") from exc
    validate_redirects(r, allowed_hosts)
    return r.text


def download(
    session: requests.Session,
    url: str,
    timeout: int,
    expect_html: bool = False,
    allowed_hosts: Iterable[str] | None = None,
) -> Download:
    """Stream a URL to a temp file while hashing it. Caller deletes ``Download.path``."""
    h256 = hashlib.sha256()
    h1 = hashlib.sha1()  # the Internet Archive publishes SHA-1 digests; used to verify captures
    size = 0
    try:
        with session.get(url, timeout=timeout, stream=True) as r:
            r.raise_for_status()
            validate_redirects(r, allowed_hosts)
            ctype = r.headers.get("Content-Type")
            if not expect_html and ctype and ctype.startswith("text/html"):
                # e.g. a retired download link that now redirects to a landing page.
                raise SourceUnavailableError(f"{url} returned an HTML page, not a data file")
            fd, name = tempfile.mkstemp(prefix="resgap-dl-")
            with open(fd, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
                    h256.update(chunk)
                    h1.update(chunk)
                    size += len(chunk)
            if size == 0:
                Path(name).unlink(missing_ok=True)
                raise SourceUnavailableError(f"{url} returned an empty body")
            return Download(
                Path(name),
                h256.hexdigest(),
                h1.hexdigest(),
                size,
                ctype,
                r.headers.get("ETag"),
                r.headers.get("Last-Modified"),
                r.url,
            )
    except requests.RequestException as exc:
        raise SourceUnavailableError(f"Download {url} failed: {exc}") from exc
