"""Params/Result for the Superset dashboard tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from superset_mcp.models.base import (
    BaseParams,
    BaseResult,
    CachedResult,
    DashboardUrlFields,
)


class ChartAttachPreview(BaseModel):
    chart_id: int
    chart_name: str | None = None
    # Dashboards this chart would be moved OFF of, because attaching replaces
    # membership instead of adding to it.
    moved_off: list[str | None] = []


class DashboardPreview(BaseModel):
    dashboard_title: str
    charts: list[ChartAttachPreview] = []


class CreateDashboardParams(BaseParams):
    dashboard_title: str
    chart_ids: list[int | str]
    confirm_token: str | None = None


class CreateDashboardPreviewResult(BaseResult):
    """First phase of create_dashboard: nothing saved, plus a token."""

    created: bool = False
    requires_confirmation: bool = True
    preview: DashboardPreview
    confirm_token: str = ""
    next_step: str = ""


class CreateDashboardResult(BaseResult, DashboardUrlFields):
    """Second phase of create_dashboard: the dashboard now exists."""

    created: bool = True
    dashboard_id: int
    chart_ids: list[int] = []


class AddChartsToDashboardParams(BaseParams):
    dashboard_id: int | str
    chart_ids: list[int | str]


class AddChartsToDashboardResult(BaseResult, DashboardUrlFields):
    dashboard_id: int
    dashboard_title: str | None = None
    added: list[int] = []
    already_present: list[int] = []


class DashboardChartInfo(BaseModel):
    id: int | None = None
    slice_name: str | None = None
    viz_type: str | None = None
    datasource_id: int | None = None
    metrics: list[str] = []
    groupby: list[str] = []
    time_range: str = "No filter"
    row_limit: int = 1000
    sql: str | None = None


class GetDashboardInfoParams(BaseParams):
    dashboard_id: int | str


class GetDashboardInfoResult(CachedResult):
    id: int | None = None
    dashboard_title: str | None = None
    slug: str | None = None
    published: bool | None = None
    charts_count: int = 0
    charts: list[DashboardChartInfo] = []
    position_json: str | None = None
    css: str | None = None


class DashboardSummary(BaseModel):
    id: int | None = None
    dashboard_title: str | None = None
    slug: str | None = None
    published: bool | None = None


class ListDashboardsParams(BaseParams):
    pass


class ListDashboardsResult(CachedResult):
    count: int = 0
    dashboards: list[DashboardSummary] = []


class UpdateDashboardParams(BaseParams):
    dashboard_id: int | str
    dashboard_title: str | None = None
    color_scheme: str | None = None
    label_colors: dict[str, str] | None = None
    clear_label_colors: bool = False


class UpdateDashboardResult(BaseResult, DashboardUrlFields):
    dashboard_id: int
    dashboard_title: str | None = None
    updated: bool = True

