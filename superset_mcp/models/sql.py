"""Params/Result for the Postgres read tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from superset_mcp.models.base import BaseParams, CachedResult


class TableRef(BaseModel):
    table_schema: str = "public"
    table_name: str
    database_id: int | None = None
    database_name: str | None = None
    description: str | None = None


class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: str = "YES"
    description: str | None = None


class ListDatasetsParams(BaseParams):
    schema: str | None = None


class ListDatasetsResult(CachedResult):
    schema: str | None = None
    count: int = 0
    tables: list[TableRef] = []


class DescribeTableParams(BaseParams):
    table_name: str
    schema: str | None = None
    database_id: int | None = None


class DescribeTableResult(CachedResult):
    schema: str | None = None
    table: str = ""
    database_id: int | None = None
    database_name: str | None = None
    columns: list[ColumnInfo] = []


class ExecuteSqlParams(BaseParams):
    sql: str | None = None
    query: str | None = None
    dataset_id: int | str | None = None
    columns: list[str] | None = None
    metrics: list[str] | None = None
    groupby: list[str] | None = None
    filters: list[dict[str, Any]] | None = None
    orderby: list[Any] | None = None
    database_id: int | None = None
    schema: str | None = None
    row_limit: int = 200


class ExecuteSqlResult(CachedResult):
    sql: str = ""
    dataset_id: int | None = None
    database_id: int | None = None
    row_count: int = 0
    columns: list[str] = []
    rows: list[dict[str, Any]] = []

