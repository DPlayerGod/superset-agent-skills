"""Params/Result for the Superset dataset tools."""

from __future__ import annotations

from pydantic import BaseModel

from superset_mcp.models.base import BaseParams, BaseResult, CachedResult


class DatasetColumn(BaseModel):
    column_name: str | None = None
    type: str | None = None


class CreateDatasetParams(BaseParams):
    table_name: str
    schema: str = "public"
    sql: str | None = None
    database_id: int | None = None


class CreateDatasetResult(BaseResult):
    dataset_id: int
    already_existed: bool = False
    virtual: bool = False
    sql_updated: bool = False


class GetDatasetInfoParams(BaseParams):
    dataset_id: int | str


class GetDatasetInfoResult(CachedResult):
    id: int | None = None
    table_name: str | None = None
    schema: str | None = None
    sql: str | None = None
    columns: list[DatasetColumn] = []


class GenerateExploreLinkParams(BaseParams):
    dataset_id: int | str


class GenerateExploreLinkResult(BaseResult):
    dataset_id: int
    explore_url: str
