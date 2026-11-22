import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


def test_database_url() -> str | None:
    # Integration tests DROP and recreate all pipeline schemas, so they only ever run against a
    # database explicitly designated for tests.
    return os.environ.get("RESGAP_TEST_DATABASE_URL")


def _dotenv_value(name: str, path: Path = ROOT / ".env") -> str | None:
    """Read one key from .env without changing the environment."""
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == name:
            return value.strip().strip('"').strip("'") or None
    return None


def pipeline_database_urls() -> list[str]:
    """The databases the pipeline itself would use (environment and .env)."""
    urls = [os.environ.get("RESGAP_DATABASE_URL"), _dotenv_value("RESGAP_DATABASE_URL")]
    return [u for u in dict.fromkeys(urls) if u]


# captured at import, before any test monkeypatches RESGAP_DATABASE_URL
PIPELINE_DATABASE_URLS = pipeline_database_urls()


def database_identity(url: str) -> tuple[str, str, str]:
    """(host, port, database) with local host aliases and the default port normalised."""
    from psycopg.conninfo import conninfo_to_dict

    d = conninfo_to_dict(url)
    host = str(d.get("host") or "")
    host = "localhost" if host in LOCAL_HOSTS or host.startswith("/") else host.lower()
    port = str(d.get("port") or "5432")
    dbname = str(d.get("dbname") or d.get("user") or os.environ.get("USER", ""))
    return host, port, dbname


def _server_identity(url: str):
    import psycopg

    try:
        with psycopg.connect(url, connect_timeout=3) as c:
            return c.execute(
                "select current_database(), inet_server_port(), pg_postmaster_start_time()"
            ).fetchone()
    except psycopg.Error:
        return None


def assert_not_pipeline_database(test_url: str, pipeline_urls: list[str]) -> None:
    """Refuse to run destructive integration tests against the pipeline's own database."""
    for url in pipeline_urls:
        same = database_identity(url) == database_identity(test_url)
        if not same:
            live_test, live_pipeline = _server_identity(test_url), _server_identity(url)
            same = live_test is not None and live_test == live_pipeline
        if same:
            raise RuntimeError(
                "RESGAP_TEST_DATABASE_URL points at the same database as RESGAP_DATABASE_URL "
                f"({database_identity(test_url)}). Integration tests drop all pipeline schemas; "
                "use a separate database such as resgap_test (see `make test`)."
            )


@pytest.fixture(scope="session")
def db_url() -> str:
    url = test_database_url()
    if not url:
        pytest.skip("set RESGAP_TEST_DATABASE_URL to run integration tests")
    try:
        assert_not_pipeline_database(url, PIPELINE_DATABASE_URLS)
    except RuntimeError as exc:
        pytest.exit(str(exc), returncode=4)
    import psycopg

    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.OperationalError as exc:
        pytest.skip(f"test database not reachable: {exc}")
    return url
