import psycopg
import pytest

from resgap.db import init_db

SCHEMAS = ("serving", "curated", "staging", "raw", "ops")


def reset_database(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        for s in SCHEMAS:
            conn.execute(f"drop schema if exists {s} cascade")
    with psycopg.connect(url) as conn:
        init_db(conn)


@pytest.fixture()
def conn(db_url):
    reset_database(db_url)
    with psycopg.connect(db_url) as c:
        yield c
