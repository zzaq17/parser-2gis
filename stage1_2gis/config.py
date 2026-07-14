"""Environment-backed configuration for the Stage 1 service."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when required worker configuration is missing or invalid."""


def _positive_int(source: Mapping[str, str], name: str, default: int) -> int:
    raw = source.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def _runtime_env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    if env is not None:
        return env
    source = dict(os.environ)
    env_file = Path(".env")
    if not env_file.exists():
        return source
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            source.setdefault(key, value)
    return source


@dataclass(slots=True, frozen=True)
class PostgresSettings:
    host: str
    port: int
    database: str
    user: str
    password: str
    connect_timeout: int = 5
    application_name: str = "stage1-2gis"

    @property
    def dsn(self) -> str:
        return (
            f"host={self.host} port={self.port} dbname={self.database} "
            f"user={self.user} password={self.password} "
            f"connect_timeout={self.connect_timeout} application_name={self.application_name}"
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> PostgresSettings:
        source = _runtime_env(env)
        missing = [name for name in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD") if not source.get(name)]
        if missing:
            raise ConfigurationError("Missing PostgreSQL settings: " + ", ".join(sorted(missing)))
        return cls(
            host=source.get("POSTGRES_HOST", "127.0.0.1"),
            port=_positive_int(source, "POSTGRES_PORT", 5432),
            database=source["POSTGRES_DB"],
            user=source["POSTGRES_USER"],
            password=source["POSTGRES_PASSWORD"],
            connect_timeout=_positive_int(source, "POSTGRES_CONNECT_TIMEOUT", 5),
            application_name=source.get("POSTGRES_APP_NAME", "stage1-2gis"),
        )


@dataclass(slots=True, frozen=True)
class WorkerSettings:
    poll_interval_seconds: int = 5
    heartbeat_interval_seconds: int = 15
    stale_job_seconds: int = 300
    max_attempts: int = 3
    consecutive_browser_error_limit: int = 3
    browser_timeout_seconds: int = 120
    artifacts_dir: Path = Path("/var/lib/stage1-2gis/artifacts")
    disable_images: bool = True
    headed: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> WorkerSettings:
        source = _runtime_env(env)
        return cls(
            poll_interval_seconds=_positive_int(source, "STAGE1_POLL_INTERVAL_SECONDS", 5),
            heartbeat_interval_seconds=_positive_int(source, "STAGE1_HEARTBEAT_INTERVAL_SECONDS", 15),
            stale_job_seconds=_positive_int(source, "STAGE1_STALE_JOB_SECONDS", 300),
            max_attempts=_positive_int(source, "STAGE1_MAX_ATTEMPTS", 3),
            consecutive_browser_error_limit=_positive_int(
                source, "STAGE1_CONSECUTIVE_BROWSER_ERROR_LIMIT", 3
            ),
            browser_timeout_seconds=_positive_int(source, "STAGE1_BROWSER_TIMEOUT_SECONDS", 120),
            artifacts_dir=Path(source.get("STAGE1_ARTIFACTS_DIR", "/var/lib/stage1-2gis/artifacts")),
            disable_images=source.get("STAGE1_DISABLE_IMAGES", "1").lower() not in {"0", "false", "no"},
            headed=source.get("STAGE1_HEADED", "1").lower() not in {"0", "false", "no"},
        )
