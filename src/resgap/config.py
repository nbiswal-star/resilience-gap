"""Settings come from environment variables (optionally a local .env file) plus YAML config."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
SQL_DIR = PROJECT_ROOT / "sql"
SEEDS_DIR = PROJECT_ROOT / "seeds"


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env reader. Existing environment variables always win."""
    path = path or PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Profile:
    name: str
    start_year: int
    end_year: int
    states: tuple[str, ...] | None
    description: str = ""

    @property
    def years(self) -> list[int]:
        return list(range(self.start_year, self.end_year + 1))


@dataclass(frozen=True)
class Settings:
    database_url: str
    object_store: str
    lake_dir: Path
    s3_bucket: str
    s3_prefix: str
    s3_endpoint_url: str | None
    profile_name: str
    http_timeout: int
    user_agent: str
    analysis: dict[str, Any] = field(default_factory=dict)

    @property
    def profile(self) -> Profile:
        return load_profile(self.profile_name)


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "")
    return value if value != "" else default


def load_settings(profile: str | None = None) -> Settings:
    load_dotenv()
    lake = Path(_env("RESGAP_LAKE_DIR", "./data/lake")).expanduser()
    if not lake.is_absolute():
        lake = PROJECT_ROOT / lake
    profiles = _read_yaml("profiles.yml")
    return Settings(
        database_url=_env(
            "RESGAP_DATABASE_URL", "postgresql://resgap:resgap@localhost:5433/resgap"
        ),
        object_store=_env("RESGAP_OBJECT_STORE", "local").lower(),
        lake_dir=lake,
        s3_bucket=_env("RESGAP_S3_BUCKET", "resgap-lake"),
        s3_prefix=_env("RESGAP_S3_PREFIX", ""),
        s3_endpoint_url=os.environ.get("RESGAP_S3_ENDPOINT_URL") or None,
        profile_name=profile or _env("RESGAP_PROFILE", "sample"),
        http_timeout=int(_env("RESGAP_HTTP_TIMEOUT_SECONDS", "120")),
        user_agent=_env("RESGAP_HTTP_USER_AGENT", "resgap-pipeline/0.1 (95-815 course project)"),
        analysis=profiles.get("analysis", {}),
    )


def _read_yaml(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_profile(name: str) -> Profile:
    profiles = _read_yaml("profiles.yml")["profiles"]
    if name not in profiles:
        raise ValueError(f"Unknown profile {name!r}; choose one of {sorted(profiles)}")
    p = profiles[name]
    states = tuple(s.upper() for s in p["states"]) if p.get("states") else None
    if p["start_year"] > p["end_year"]:
        raise ValueError(f"Profile {name!r}: start_year must be <= end_year")
    return Profile(name, int(p["start_year"]), int(p["end_year"]), states, p.get("description", ""))


def load_sources() -> dict[str, Any]:
    return _read_yaml("sources.yml")["sources"]
