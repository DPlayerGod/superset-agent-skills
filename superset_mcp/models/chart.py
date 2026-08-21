"""Params/Result for the Superset chart tools."""

from __future__ import annotations

from pydantic import BaseModel

from superset_mcp.models.base import (
    BaseParams,
    BaseResult,
    CachedResult,
    ChartSpec,
    ChartUrlFields,
)


class ChartStyleParams(BaseParams):
    """Styling shared verbatim by create_chart and update_chart.

    `viz_type` and `orientation` stay plain strings rather than Literals: charts
    built in the Superset UI can carry viz types this server never writes, and
    update_chart has to be able to round-trip them.
    """

    color_scheme: str | None = None
    show_legend: bool | None = None
    number_format: str | None = None
    x_axis_sort: str | None = None
    x_axis_sort_asc: bool | None = None
    order_desc: bool | None = None
    orientation: str | None = None
    color: str | None = None
    description: str | None = None
    x_axis_title: str | None = None
    y_axis_title: str | None = None
    # Historical aliases, folded into x_axis_title/y_axis_title by the logic layer.
    x_axis_label: str | None = None
    y_axis_label: str | None = None
    # Big Number with Trendline parameters
    time_grain_sqla: str | None = None
    comparison_period_lag: int | None = None
    compare_lag: int | None = None
    comparison_suffix: str | None = None
    compare_suffix: str | None = None
    show_timestamp: bool | None = None
    show_trend_line: bool | None = None
    start_y_axis_at_zero: bool | None = None
    resample_rule: str | None = None
    resample_fill_method: str | None = None
    rolling_type: str | None = None
    rolling_periods: int | None = None
    min_periods: int | None = None


class DashboardRef(BaseModel):
    id: int | None = None
    title: str | None = None


class CreateChartParams(ChartStyleParams):
    dataset_id: int | str
    chart_name: str
    viz_type: str
    metrics: list[str]
    groupby: list[str] | None = None
    time_range: str = "No filter"
    row_limit: int = 1000
    confirm_token: str | None = None


class ChartPreviewResult(BaseResult, ChartUrlFields):
    """First phase of create_chart: nothing saved, a live preview plus a token."""

    created: bool = False
    requires_confirmation: bool = True
    chart_name: str = ""
    viz_type: str = ""
    metrics: list[str] = []
    groupby: list[str] = []
    confirm_token: str = ""
    next_step: str = ""


class CreateChartResult(BaseResult, ChartUrlFields):
    """Second phase of create_chart: the chart now exists."""

    created: bool = True
    chart_id: int


class UpdateChartParams(ChartStyleParams):
    chart_id: int | str
    chart_name: str | None = None
    viz_type: str | None = None
    metrics: list[str] | None = None
    groupby: list[str] | None = None
    time_range: str | None = None
    row_limit: int | None = None
    confirm_token: str | None = None


class UpdateChartPreviewResult(BaseResult, ChartUrlFields):
    """First phase of update_chart: nothing saved, a live preview plus a token."""

    updated: bool = False
    requires_confirmation: bool = True
    chart_id: int
    chart_name: str = ""
    confirm_token: str = ""
    next_step: str = ""


class UpdateChartResult(BaseResult, ChartUrlFields):
    """Second phase of update_chart: the chart has been saved."""

    updated: bool = True
    chart_id: int


class GetChartParams(BaseParams):
    chart_id: int | str


class GetChartResult(CachedResult, ChartSpec, ChartUrlFields):
    chart_id: int
    chart_name: str | None = None
    dataset_id: int | None = None
    dashboards: list[DashboardRef] = []


class GetChartPreviewParams(BaseParams):
    chart_id: int | str


class GetChartPreviewResult(BaseResult, ChartUrlFields):
    pass


class GetChartSqlParams(BaseParams):
    chart_id: int | str


class GetChartSqlResult(CachedResult):
    chart_id: int
    sql: str = ""


class ChartSummary(BaseModel):
    id: int | None = None
    slice_name: str | None = None
    viz_type: str | None = None
    datasource_id: int | None = None
    datasource_name: str | None = None


class ListChartsParams(BaseParams):
    pass


class ListChartsResult(CachedResult):
    count: int = 0
    charts: list[ChartSummary] = []
