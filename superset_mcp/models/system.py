"""Params/Result for the health, instance and database tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from superset_mcp.models.base import BaseParams, BaseResult, CachedResult


class HealthDetails(BaseModel):
    database: str = ""
    superset: str = ""


class HealthCheckParams(BaseParams):
    pass


class HealthCheckResult(BaseResult):
    status: Literal["ok", "error"] = "ok"
    details: HealthDetails = HealthDetails()


class GetInstanceInfoParams(BaseParams):
    pass


class GetInstanceInfoResult(BaseResult):
    superset_url: str
    database_backend: str
    description: str


class DatabaseSummary(BaseModel):
    id: int | None = None
    database_name: str | None = None
    backend: str | None = None


class ListDatabasesParams(BaseParams):
    pass


class ListDatabasesResult(CachedResult):
    count: int = 0
    databases: list[DatabaseSummary] = []


class GetDatabaseInfoParams(BaseParams):
    database_id: int | str


class GetDatabaseInfoResult(CachedResult):
    id: int | None = None
    database_name: str | None = None
    backend: str | None = None
    expose_in_sqllab: bool | None = None


class OpenSqlLabWithContextParams(BaseParams):
    sql: str


class OpenSqlLabWithContextResult(BaseResult):
    sql: str
    sql_lab_url: str
