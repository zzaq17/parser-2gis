"""Typed records exchanged between the database, worker, and browser."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PositiveInt


class UrlJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    run_id: str
    city_key: str
    query_key: str
    source_url: str
    max_records: PositiveInt
    attempt_no: PositiveInt
    lock_token: str


class RawCatalogItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    payload: dict[str, Any]
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))  # noqa: UP017


class NormalizedItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    two_gis_item_id: str
    two_gis_org_id: str | None = None
    name: str | None = None
    description: str | None = None
    address: str | None = None
    city: str | None = None
    primary_rubric: str | None = None
    is_advertised: bool = False
    phones: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    websites: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    website_domains: tuple[tuple[str, str], ...] = ()
    two_gis_url: str
    normalized_payload: dict[str, Any]


class WorkerResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    status: str
    items_received: int
    error_code: str | None = None
    error_message: str | None = None
