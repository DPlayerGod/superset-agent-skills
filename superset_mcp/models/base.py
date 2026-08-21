"""Schemas every tool shares.

`BaseResult` is the contract: no tool returns a bare dict, every result carries
`success` / `message` / `errors`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class BaseParams(BaseModel):
    """Base for every `<ToolName>Params`. Rejects fields the tool does not declare."""

    model_config = ConfigDict(extra="forbid")


class BaseResult(BaseModel):
    """Base for every `<ToolName>Result`."""

    success: bool = True
    message: str = ""
    errors: list[str] = []


class CachedResult(BaseResult):
    """Result of a read that goes through the in-memory cache."""

    cached: bool = False


class ChartUrlFields(BaseModel):
    """Flat `type`/`url`/`embed_url` a chart result exposes.

    Kept flat (not nested under a key) because the gateway detects a previewable
    tool result by looking for a top-level `embed_url` - see `_collect_preview` in
    claude_gateway/gateway_server.py.
    """

    type: Literal["chart"] = "chart"
    url: str = ""
    embed_url: str = ""


class DashboardUrlFields(BaseModel):
    """Flat `type`/`url`/`embed_url` a dashboard result exposes."""

    type: Literal["dashboard"] = "dashboard"
    url: str = ""
    embed_url: str = ""


class ChartSpec(BaseModel):
    """A chart's configuration in tool terms, independent of Superset's params blob.

    Replaces the 16-element tuple `_extract_chart_spec` used to return, which had to
    be unpacked in the same order in three different places.
    """

    viz_type: str = "table"
    metrics: list[str] = []
    groupby: list[str] = []
    time_range: str = "No filter"
    row_limit: int = 1000
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
    # Big Number with Trendline specific fields
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
