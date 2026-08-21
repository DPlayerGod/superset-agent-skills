"""Translation between a `ChartSpec` and Superset's `params` blob.

`build_chart_params` writes the blob Superset stores on a chart; `extract_chart_spec`
reads it back. They are exact inverses, which is what lets update_chart inherit
every field the caller omits.
"""

from __future__ import annotations

import re
from typing import Any

from superset_mcp.models.base import ChartSpec
from superset_mcp.services.colors import (
    hex_to_rgba,
    resolve_color,
    resolve_color_scheme,
    rgba_to_hex,
)

# Superset drops an ad-hoc SQL metric into the SELECT list verbatim and groups by
# the dimensions only, so a metric with no aggregate function ("monthly_fte")
# compiles to `SELECT working_month_year, employee_full_name, monthly_fte FROM
# (...) AS virtual_table GROUP BY working_month_year, employee_full_name` and
# Postgres rejects it with "must appear in the GROUP BY clause". The trap is
# worst on virtual datasets whose own SQL already aggregates: the column reads as
# pre-computed, but the chart query still re-aggregates it over its own grouping.
# Caught here rather than at render time so the model gets a fixable message on
# the preview call, before anything is saved.
# count_distinct/covar_ must precede count/corr - Python alternation is
# first-match-wins and the shorter name would not reach the `\s*\(`.
_AGGREGATE_RE = re.compile(
    r"\b(count_distinct|count|sum|avg|min|max|median|mode|any_value"
    r"|stddev\w*|var\w*|percentile_cont|percentile_disc|covar_\w+|corr"
    r"|string_agg|array_agg|jsonb_agg|json_agg|bool_and|bool_or|every"
    r"|bit_and|bit_or)\s*\(",
    re.IGNORECASE,
)

_ALIAS_RE = re.compile(
    r"^(.*?)\s+(?:AS|as)\s+[\"']?([^\"']+)[\"']?$",
    re.IGNORECASE,
)

# The one metric Superset auto-creates on every dataset. Kept as a bare string -
# how the API refers to a saved metric - instead of being rejected as a missing
# aggregate, so update_chart can still edit charts built in the Superset UI.
_SAVED_METRICS = {"count"}


def parse_metric_expression(expression: str) -> tuple[str, str]:
    """Extracts (sqlExpression, label) from expressions like 'SUM(col) AS "My Label"'.

    If no alias is present, both return values are the stripped expression.
    """
    raw = expression.strip()
    match = _ALIAS_RE.match(raw)
    if match:
        sql_expr = match.group(1).strip()
        label = match.group(2).strip()
        return sql_expr, label
    return raw, raw


def adhoc_metric(expression: str) -> dict[str, Any]:
    sql_expr, label = parse_metric_expression(expression)
    if not _AGGREGATE_RE.search(sql_expr):
        raise ValueError(
            f"metric {expression!r} has no aggregate function, so the chart query would "
            f"select it next to the groupby columns without grouping by it and the "
            f"database would reject it. Wrap it in one, e.g. \"SUM({sql_expr})\". "
            f"This holds even when the dataset SQL already aggregated that column: the "
            f"chart re-groups by its own dimensions, so the metric must aggregate again."
        )
    return {
        "expressionType": "SQL",
        "sqlExpression": sql_expr,
        "label": label,
    }


def metric_param(expression: str) -> dict[str, Any] | str:
    sql_expr, label = parse_metric_expression(expression)
    if sql_expr.lower() in _SAVED_METRICS and label == sql_expr:
        return sql_expr.lower()
    return adhoc_metric(expression)


def build_chart_params(spec: ChartSpec) -> dict[str, Any]:
    """Superset `params` for the chart described by `spec`.

    Raises ValueError (via `adhoc_metric`) when a metric carries no aggregate
    function - that guardrail stays a hard failure rather than a soft result, so
    the assistant cannot report a broken chart as saved.
    """
    viz_type = spec.viz_type
    metrics = spec.metrics
    groupby = spec.groupby
    adhoc_metrics = [metric_param(metric) for metric in metrics]
    params: dict[str, Any] = {
        "viz_type": viz_type,
        "metrics": adhoc_metrics,
        "groupby": list(groupby),
        "adhoc_filters": [],
        "row_limit": spec.row_limit,
        "time_range": spec.time_range,
    }
    if spec.description is not None:
        params["description"] = spec.description
        params["subheader"] = spec.description
        params["subtitle"] = spec.description

    effective_scheme = spec.color_scheme or resolve_color_scheme(spec.color)
    if effective_scheme:
        params["color_scheme"] = effective_scheme

    resolved_color = resolve_color(spec.color)
    if resolved_color:
        rgba = hex_to_rgba(resolved_color)
        params["color_picker"] = rgba
        params["colorPicker"] = rgba
        label_colors: dict[str, str] = {}
        for m in adhoc_metrics:
            if isinstance(m, dict) and "label" in m:
                label_colors[m["label"]] = resolved_color
            elif isinstance(m, str):
                label_colors[m] = resolved_color
        for raw_m in metrics:
            _, label = parse_metric_expression(raw_m)
            label_colors[label] = resolved_color
        if label_colors:
            params["label_colors"] = label_colors

    effective_x_title = spec.x_axis_title
    effective_y_title = spec.y_axis_title

    if viz_type in ("echarts_timeseries_line", "echarts_timeseries_bar"):
        params["x_axis"] = groupby[0] if groupby else None
        params["groupby"] = list(groupby[1:])
        if spec.orientation is not None:
            params["orientation"] = spec.orientation
        if spec.order_desc is not None:
            params["order_desc"] = spec.order_desc
        if spec.x_axis_sort is not None:
            params["x_axis_sort"] = spec.x_axis_sort
        if spec.x_axis_sort_asc is not None:
            params["x_axis_sort_asc"] = spec.x_axis_sort_asc
            if spec.order_desc is None:
                params["order_desc"] = not spec.x_axis_sort_asc
        elif spec.order_desc is not None:
            params["x_axis_sort_asc"] = not spec.order_desc
        if effective_x_title is not None:
            params["x_axis_title"] = effective_x_title
            params["x_axis_label"] = effective_x_title
            params["x_axis_title_margin"] = 30
        if effective_y_title is not None:
            params["y_axis_title"] = effective_y_title
            params["y_axis_label"] = effective_y_title
            params["y_axis_title_margin"] = 30
            params["y_axis_title_position"] = "Left"
    elif viz_type == "pie":
        # Pie's control panel is [["groupby"], ["metric"]] - it reads a single
        # `metric` and ignores `metrics` entirely, so a plural list renders blank.
        params["metric"] = adhoc_metrics[0] if adhoc_metrics else None
        params.pop("metrics", None)
    elif viz_type == "big_number_total":
        params["metric"] = adhoc_metrics[0] if adhoc_metrics else None
        params.pop("metrics", None)
        params.pop("groupby", None)
    elif viz_type in ("big_number", "big_number_trendline"):
        viz_type = "big_number"
        params["viz_type"] = "big_number"
        params["metric"] = adhoc_metrics[0] if adhoc_metrics else None
        params.pop("metrics", None)

        time_col = groupby[0] if groupby else None
        params["granularity_sqla"] = time_col
        params["x_axis"] = time_col
        params.pop("groupby", None)

        params["time_grain_sqla"] = spec.time_grain_sqla or "P1M"

        effective_lag = spec.comparison_period_lag if spec.comparison_period_lag is not None else spec.compare_lag
        if effective_lag is not None:
            params["compare_lag"] = effective_lag
            params["compareLag"] = effective_lag
            params["comparison_period_lag"] = effective_lag

        effective_suffix = spec.comparison_suffix if spec.comparison_suffix is not None else spec.compare_suffix
        if effective_suffix is not None:
            params["compare_suffix"] = effective_suffix
            params["compareSuffix"] = effective_suffix
            params["comparison_suffix"] = effective_suffix

        if spec.show_timestamp is not None:
            params["show_timestamp"] = spec.show_timestamp
            params["showTimestamp"] = spec.show_timestamp
        if spec.show_trend_line is not None:
            params["show_trend_line"] = spec.show_trend_line
            params["showTrendLine"] = spec.show_trend_line
        else:
            params["show_trend_line"] = True
            params["showTrendLine"] = True

        if spec.start_y_axis_at_zero is not None:
            params["start_y_axis_at_zero"] = spec.start_y_axis_at_zero
            params["startYAxisAtZero"] = spec.start_y_axis_at_zero
        else:
            params["start_y_axis_at_zero"] = True
            params["startYAxisAtZero"] = True

        if spec.resample_rule is not None:
            params["resample_rule"] = spec.resample_rule
        if spec.resample_fill_method is not None:
            params["resample_fill_method"] = spec.resample_fill_method
        if spec.rolling_type is not None:
            params["rolling_type"] = spec.rolling_type
        if spec.rolling_periods is not None:
            params["rolling_periods"] = spec.rolling_periods
        if spec.min_periods is not None:
            params["min_periods"] = spec.min_periods
    elif viz_type == "sunburst_v2":
        # Hierarchy plugin: `groupby` stays a list and its order is the ring order,
        # outermost ring last. Reads a singular `metric` like pie does.
        params["metric"] = adhoc_metrics[0] if adhoc_metrics else None
        params.pop("metrics", None)
    elif viz_type == "heatmap_v2":
        # Verified against this build's bundle: the y-axis control is `groupby` with
        # multi:false, so it holds a single column string, not a list - passing a
        # list here renders an empty grid.
        params["x_axis"] = groupby[0] if groupby else None
        params["groupby"] = groupby[1] if len(groupby) > 1 else None
        params["metric"] = adhoc_metrics[0] if adhoc_metrics else None
        params.pop("metrics", None)

    # Style controls: confirmed against this build's bundle
    # (superset/static/assets/*.js) that `color_scheme`/`show_legend` are real
    # control names on the shared echarts legend/palette block, and that
    # `linear_color_scheme` is a distinct control (ColorSchemeControl) used for
    # continuous-scale plugins rather than `color_scheme`. Not round-trip verified
    # in the running Explore UI - if a value silently doesn't take effect for a
    # given viz_type, that is the first thing to re-check.
    if spec.color_scheme is not None:
        if viz_type == "heatmap_v2":
            params["linear_color_scheme"] = spec.color_scheme
        elif viz_type not in ("table", "big_number_total"):
            params["color_scheme"] = spec.color_scheme
    if spec.show_legend is not None and viz_type in (
        "echarts_timeseries_line",
        "echarts_timeseries_bar",
        "pie",
        "sunburst_v2",
    ):
        params["show_legend"] = spec.show_legend
    if spec.number_format is not None:
        if viz_type in ("echarts_timeseries_line", "echarts_timeseries_bar"):
            params["y_axis_format"] = spec.number_format
        elif viz_type != "table":
            params["number_format"] = spec.number_format
    return params


def extract_chart_spec(params: dict[str, Any]) -> ChartSpec:
    """Reverses `build_chart_params` so update_chart can inherit fields the caller omits."""
    viz_type = params.get("viz_type", "table")
    time_range = params.get("time_range", "No filter")
    row_limit = params.get("row_limit", 1000)
    color_scheme = (
        params.get("linear_color_scheme") if viz_type == "heatmap_v2" else params.get("color_scheme")
    )
    show_legend = params.get("show_legend")
    number_format = (
        params.get("y_axis_format")
        if viz_type in ("echarts_timeseries_line", "echarts_timeseries_bar")
        else params.get("number_format")
    )
    x_axis_sort = params.get("x_axis_sort")
    x_axis_sort_asc = params.get("x_axis_sort_asc")
    order_desc = params.get("order_desc")
    orientation = params.get("orientation")
    description = params.get("description") or params.get("subheader") or params.get("subtitle")
    x_axis_title = params.get("x_axis_title") or params.get("x_axis_label")
    y_axis_title = params.get("y_axis_title") or params.get("y_axis_label")

    # Big Number with Trendline fields
    time_grain_sqla = params.get("time_grain_sqla")
    compare_lag = params.get("comparison_period_lag") if params.get("comparison_period_lag") is not None else params.get("compare_lag")
    comparison_period_lag = compare_lag
    compare_suffix = params.get("comparison_suffix") if params.get("comparison_suffix") is not None else params.get("compare_suffix")
    comparison_suffix = compare_suffix
    show_timestamp = params.get("show_timestamp")
    show_trend_line = params.get("show_trend_line")
    start_y_axis_at_zero = params.get("start_y_axis_at_zero")
    resample_rule = params.get("resample_rule")
    resample_fill_method = params.get("resample_fill_method")
    rolling_type = params.get("rolling_type")
    rolling_periods = params.get("rolling_periods")
    min_periods = params.get("min_periods")

    # Try extracting color from label_colors or color_picker
    color = None
    if isinstance(params.get("label_colors"), dict) and params["label_colors"]:
        color = next(iter(params["label_colors"].values()), None)
    if not color and params.get("color_picker"):
        color = rgba_to_hex(params.get("color_picker"))

    def expr(metric: Any) -> str:
        if isinstance(metric, dict):
            sql = metric.get("sqlExpression", "")
            lbl = metric.get("label", "")
            if lbl and sql and lbl != sql:
                return f'{sql} AS "{lbl}"'
            return sql or lbl
        return str(metric)

    if viz_type in ("pie", "big_number_total", "sunburst_v2"):
        # `or params.get("metrics")` recovers charts written before pie was mapped
        # to the singular `metric`, so a partial update does not blank them out.
        metric = params.get("metric") or next(iter(params.get("metrics") or []), None)
        metrics = [expr(metric)] if metric else []
        groupby: list[str] = [] if viz_type == "big_number_total" else list(params.get("groupby", []))
    elif viz_type in ("big_number", "big_number_trendline"):
        metric = params.get("metric") or next(iter(params.get("metrics") or []), None)
        metrics = [expr(metric)] if metric else []
        time_col = params.get("granularity_sqla") or params.get("x_axis")
        groupby = [time_col] if time_col else []
    elif viz_type == "heatmap_v2":
        metric = params.get("metric") or next(iter(params.get("metrics") or []), None)
        metrics = [expr(metric)] if metric else []
        y_axis = params.get("groupby")
        groupby = [c for c in (params.get("x_axis"), y_axis) if c]
    elif viz_type in ("echarts_timeseries_line", "echarts_timeseries_bar"):
        x_axis = params.get("x_axis")
        metrics = [expr(m) for m in params.get("metrics", [])]
        groupby = ([x_axis] if x_axis else []) + list(params.get("groupby", []))
    else:
        metrics = [expr(m) for m in params.get("metrics", [])]
        groupby = list(params.get("groupby", []))

    return ChartSpec(
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
    )
