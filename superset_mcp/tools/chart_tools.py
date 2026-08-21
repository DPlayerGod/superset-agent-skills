"""MCP wrappers for the Superset chart tools."""

from __future__ import annotations

from typing import Any

from superset_mcp.app import mcp
from superset_mcp.logic import chart_logic
from superset_mcp.models.chart import (
    CreateChartParams,
    GetChartParams,
    GetChartPreviewParams,
    GetChartSqlParams,
    ListChartsParams,
    UpdateChartParams,
)


@mcp.tool()
def create_chart(
    dataset_id: int | str,
    chart_name: str,
    viz_type: str,
    metrics: list[str],
    groupby: list[str] | None = None,
    time_range: str = "No filter",
    row_limit: int = 1000,
    color_scheme: str | None = None,
    show_legend: bool | None = None,
    number_format: str | None = None,
    x_axis_sort: str | None = None,
    x_axis_sort_asc: bool | None = None,
    order_desc: bool | None = None,
    orientation: str | None = None,
    color: str | None = None,
    description: str | None = None,
    x_axis_title: str | None = None,
    y_axis_title: str | None = None,
    x_axis_label: str | None = None,
    y_axis_label: str | None = None,
    # Big Number with Trendline parameters
    time_grain_sqla: str | None = None,
    comparison_period_lag: int | None = None,
    compare_lag: int | None = None,
    comparison_suffix: str | None = None,
    compare_suffix: str | None = None,
    show_timestamp: bool | None = None,
    show_trend_line: bool | None = None,
    start_y_axis_at_zero: bool | None = None,
    resample_rule: str | None = None,
    resample_fill_method: str | None = None,
    rolling_type: str | None = None,
    rolling_periods: int | None = None,
    min_periods: int | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Creates a Superset chart on an existing dataset. Two calls, with the user's
    answer in between - nothing is saved to Superset until they confirm.

    Call it WITHOUT confirm_token first: no chart is created, and you get back a
    live preview plus a confirm_token. Show the user the preview and ask them to confirm.
    Only after they say yes, call again with the SAME arguments plus that confirm_token.

    dataset_id: id from create_dataset or list_datasets.
    chart_name: the title the user will see, e.g. "Tổng Doanh Thu Hàng Tháng".

    viz_type: one of "table", "echarts_timeseries_line", "echarts_timeseries_bar",
    "pie", "big_number_total", "big_number", "sunburst_v2", "heatmap_v2".

    Big Number with Trendline ("big_number"):
    - metrics: ["SUM(net_revenue_vnd)"] (metric aggregate)
    - groupby: ["order_date"] (temporal date column for the trendline X-axis)
    - time_grain_sqla: "P1D" (Daily), "P1W" (Weekly), "P1M" (Monthly, default), "P3M" (Quarterly), "P1Y" (Yearly).
    - comparison_period_lag (hoặc compare_lag): int, khoảng thời gian so sánh (e.g. 1 cho tháng/ngày trước).
    - comparison_suffix (hoặc compare_suffix): str, hậu tố hiển thị cạnh tỷ lệ phần trăm (e.g. "MoM", "so với tháng trước").
    - show_timestamp: True/False, display timestamp on data points.
    - show_trend_line: True (default) / False, show/hide the mini trendline graph.
    - start_y_axis_at_zero: True (default) / False, start Y-axis from 0.

    metrics: SQL aggregate expressions, e.g. ["SUM(project_allocated_hc)"].
    groupby: list of dimension column names.
    time_range: Superset time range expression, e.g. "No filter" (default) or "Last quarter".
    row_limit: maximum rows the chart query returns, default 1000.
    color: Natural color name ("đỏ", "xanh dương", "xanh lá", "cam", "tím", "vàng", "#HEX").
    description: Subtitle / description / explanation markdown for the chart.
    confirm_token: leave unset on the first call.
    """
    return chart_logic.create_chart(
        CreateChartParams(
            dataset_id=dataset_id,
            chart_name=chart_name,
            viz_type=viz_type,
            metrics=metrics,
            groupby=groupby,
            time_range=time_range,
            row_limit=row_limit,
            color_scheme=color_scheme,
            show_legend=show_legend,
            number_format=number_format,
            x_axis_sort=x_axis_sort,
            x_axis_sort_asc=x_axis_sort_asc,
            order_desc=order_desc,
            orientation=orientation,
            color=color,
            description=description,
            x_axis_title=x_axis_title,
            y_axis_title=y_axis_title,
            x_axis_label=x_axis_label,
            y_axis_label=y_axis_label,
            time_grain_sqla=time_grain_sqla,
            comparison_period_lag=comparison_period_lag,
            compare_lag=compare_lag,
            comparison_suffix=comparison_suffix,
            compare_suffix=compare_suffix,
            show_timestamp=show_timestamp,
            show_trend_line=show_trend_line,
            start_y_axis_at_zero=start_y_axis_at_zero,
            resample_rule=resample_rule,
            resample_fill_method=resample_fill_method,
            rolling_type=rolling_type,
            rolling_periods=rolling_periods,
            min_periods=min_periods,
            confirm_token=confirm_token,
        )
    ).model_dump()


@mcp.tool()
def update_chart(
    chart_id: int | str,
    chart_name: str | None = None,
    viz_type: str | None = None,
    metrics: list[str] | None = None,
    groupby: list[str] | None = None,
    time_range: str | None = None,
    row_limit: int | None = None,
    color_scheme: str | None = None,
    show_legend: bool | None = None,
    number_format: str | None = None,
    x_axis_sort: str | None = None,
    x_axis_sort_asc: bool | None = None,
    order_desc: bool | None = None,
    orientation: str | None = None,
    color: str | None = None,
    description: str | None = None,
    x_axis_title: str | None = None,
    y_axis_title: str | None = None,
    x_axis_label: str | None = None,
    y_axis_label: str | None = None,
    time_grain_sqla: str | None = None,
    comparison_period_lag: int | None = None,
    compare_lag: int | None = None,
    comparison_suffix: str | None = None,
    compare_suffix: str | None = None,
    show_timestamp: bool | None = None,
    show_trend_line: bool | None = None,
    start_y_axis_at_zero: bool | None = None,
    resample_rule: str | None = None,
    resample_fill_method: str | None = None,
    rolling_type: str | None = None,
    rolling_periods: int | None = None,
    min_periods: int | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Edits an existing Superset chart in place."""
    return chart_logic.update_chart(
        UpdateChartParams(
            chart_id=chart_id,
            chart_name=chart_name,
            viz_type=viz_type,
            metrics=metrics,
            groupby=groupby,
            time_range=time_range,
            row_limit=row_limit,
            color_scheme=color_scheme,
            show_legend=show_legend,
            number_format=number_format,
            x_axis_sort=x_axis_sort,
            x_axis_sort_asc=x_axis_sort_asc,
            order_desc=order_desc,
            orientation=orientation,
            color=color,
            description=description,
            x_axis_title=x_axis_title,
            y_axis_title=y_axis_title,
            x_axis_label=x_axis_label,
            y_axis_label=y_axis_label,
            time_grain_sqla=time_grain_sqla,
            comparison_period_lag=comparison_period_lag,
            compare_lag=compare_lag,
            comparison_suffix=comparison_suffix,
            compare_suffix=compare_suffix,
            show_timestamp=show_timestamp,
            show_trend_line=show_trend_line,
            start_y_axis_at_zero=start_y_axis_at_zero,
            resample_rule=resample_rule,
            resample_fill_method=resample_fill_method,
            rolling_type=rolling_type,
            rolling_periods=rolling_periods,
            min_periods=min_periods,
            confirm_token=confirm_token,
        )
    ).model_dump()


@mcp.tool()
def get_chart(chart_id: int | str) -> dict[str, Any]:
    """Reads one chart's full configuration: viz_type, metrics, groupby, styling.

    Call it before update_chart when you need to know what the chart currently
    shows - for example to answer "what is on this chart?" or to decide which
    fields an edit has to change.

    chart_id: the numeric chart id, e.g. 7. Get it from list_charts.
    """
    return chart_logic.get_chart(GetChartParams(chart_id=chart_id)).model_dump()


@mcp.tool()
def get_chart_preview(chart_id: int | str) -> dict[str, Any]:
    """Returns the URL and embeddable URL of a chart that already exists.

    Use it to show the user a chart again without changing anything. It does not
    render a preview of unsaved changes - create_chart/update_chart do that.

    chart_id: the numeric chart id, e.g. 7.
    """
    return chart_logic.get_chart_preview(
        GetChartPreviewParams(chart_id=chart_id)
    ).model_dump()


@mcp.tool()
def get_chart_sql(chart_id: int | str) -> dict[str, Any]:
    """Returns the SQL query Superset runs for a chart.

    Use it to explain where a chart's numbers come from, or as the starting point
    for a variant query you then run with execute_sql. Do NOT call it for charts you
    got from get_dashboard_info - that tool already returns each chart's SQL.

    chart_id: the numeric chart id, e.g. 7.
    """
    return chart_logic.get_chart_sql(GetChartSqlParams(chart_id=chart_id)).model_dump()


@mcp.tool()
def list_charts() -> dict[str, Any]:
    """Lists every chart in Superset with its id, name, viz_type and dataset.

    Call it when the user refers to a chart by name and you need its id, or to check
    whether a similar chart already exists before creating another one.
    """
    return chart_logic.list_charts(ListChartsParams()).model_dump()
