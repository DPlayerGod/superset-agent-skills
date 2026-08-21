"""Business logic for the Superset chart tools.

The two-phase confirm flow is two functions per tool - `preview_*` issues the token,
`commit_*` redeems it - with a dispatcher of the tool's own name in between, so the
MCP surface the assistant sees is unchanged while each phase is separately testable.
"""

from __future__ import annotations

import json
from typing import Any

import requests
try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger("superset_mcp")

from superset_mcp.config import SUPERSET_URL
from superset_mcp.models.base import ChartSpec
from superset_mcp.models.chart import (
    ChartPreviewResult,
    ChartSummary,
    CreateChartParams,
    CreateChartResult,
    DashboardRef,
    GetChartParams,
    GetChartPreviewParams,
    GetChartPreviewResult,
    GetChartResult,
    GetChartSqlParams,
    GetChartSqlResult,
    ListChartsParams,
    ListChartsResult,
    UpdateChartParams,
    UpdateChartPreviewResult,
    UpdateChartResult,
)
from superset_mcp.services.cache import (
    get_cached_model,
    invalidate_sql_cache,
    set_cached_model,
)
from superset_mcp.services.chart_params import (
    build_chart_params,
    extract_chart_spec,
    parse_metric_expression,
)
from superset_mcp.services.colors import resolve_color, resolve_color_scheme
from superset_mcp.services.ids import parse_id
from superset_mcp.services.superset_client import superset_session
from superset_mcp.services.tokens import consume_create_token, issue_create_token
from superset_mcp.services.urls import chart_urls, explore_preview_url

_SPEC_FIELDS = tuple(ChartSpec.model_fields)

_CREATE_NEXT_STEP = (
    "Show this preview to the user and ask them to confirm before it is "
    "saved. Only call create_chart again, with confirm_token, once they "
    "answer yes."
)
_UPDATE_NEXT_STEP = (
    "Show this preview to the user and ask them to confirm before the "
    "chart is updated. Only call update_chart again, with confirm_token, "
    "once they answer yes."
)


def _spec_from_payload(payload: dict[str, Any]) -> ChartSpec:
    """Rebuilds a ChartSpec from a stored confirm-token payload."""
    return ChartSpec(**{k: payload[k] for k in _SPEC_FIELDS if payload.get(k) is not None})


def _axis_titles(params: CreateChartParams | UpdateChartParams) -> tuple[str | None, str | None]:
    """Folds the historical `*_label` aliases into the `*_title` fields."""
    return (
        params.x_axis_title or params.x_axis_label,
        params.y_axis_title or params.y_axis_label,
    )


# --- create_chart ------------------------------------------------------------


def _create_chart_payload(params: CreateChartParams, dataset_id: int) -> dict[str, Any]:
    target_x_title, target_y_title = _axis_titles(params)
    effective_lag = params.comparison_period_lag if params.comparison_period_lag is not None else params.compare_lag
    effective_suffix = params.comparison_suffix if params.comparison_suffix is not None else params.compare_suffix
    return {
        "dataset_id": dataset_id,
        "chart_name": params.chart_name,
        "viz_type": params.viz_type,
        "metrics": params.metrics,
        "groupby": params.groupby or [],
        "time_range": params.time_range,
        "row_limit": params.row_limit,
        "color_scheme": params.color_scheme,
        "show_legend": params.show_legend,
        "number_format": params.number_format,
        "x_axis_sort": params.x_axis_sort,
        "x_axis_sort_asc": params.x_axis_sort_asc,
        "order_desc": params.order_desc,
        "orientation": params.orientation,
        "color": params.color,
        "description": params.description,
        "x_axis_title": target_x_title,
        "y_axis_title": target_y_title,
        "time_grain_sqla": params.time_grain_sqla,
        "comparison_period_lag": effective_lag,
        "compare_lag": effective_lag,
        "comparison_suffix": effective_suffix,
        "compare_suffix": effective_suffix,
        "show_timestamp": params.show_timestamp,
        "show_trend_line": params.show_trend_line,
        "start_y_axis_at_zero": params.start_y_axis_at_zero,
        "resample_rule": params.resample_rule,
        "resample_fill_method": params.resample_fill_method,
        "rolling_type": params.rolling_type,
        "rolling_periods": params.rolling_periods,
        "min_periods": params.min_periods,
    }


def preview_chart(params: CreateChartParams) -> ChartPreviewResult:
    """Phase 1 of create_chart: renders unsaved form_data and issues a confirm token.

    Nothing is written to Superset. Metric validation happens here, so a bad metric
    fails before the user is ever shown a preview.
    """
    dataset_id = parse_id(params.dataset_id)
    payload = _create_chart_payload(params, dataset_id)
    chart_params = build_chart_params(_spec_from_payload(payload))
    sess = superset_session()

    return ChartPreviewResult(
        message=f"Preview of '{params.chart_name}'. Not saved yet - confirm to create it.",
        created=False,
        requires_confirmation=True,
        chart_name=params.chart_name,
        viz_type=params.viz_type,
        metrics=params.metrics,
        groupby=params.groupby or [],
        confirm_token=issue_create_token("create_chart", payload),
        next_step=_CREATE_NEXT_STEP,
        **explore_preview_url(sess, dataset_id, params.chart_name, chart_params),
    )


def commit_chart(confirm_token: str) -> CreateChartResult:
    """Phase 2 of create_chart: redeems the token and writes the chart.

    Everything is rebuilt from the token payload, never from the current call's
    arguments, so the chart that gets saved is the one the user was shown.
    """
    payload = consume_create_token(confirm_token, "create_chart")
    saved_params = build_chart_params(_spec_from_payload(payload))

    chart_payload: dict[str, Any] = {
        "slice_name": payload["chart_name"],
        "viz_type": payload["viz_type"],
        "datasource_id": payload["dataset_id"],
        "datasource_type": "table",
        "params": json.dumps(saved_params),
    }
    if payload.get("description"):
        chart_payload["description"] = payload["description"]

    sess = superset_session()
    resp = sess.post(f"{SUPERSET_URL}/api/v1/chart/", json=chart_payload, timeout=20)
    resp.raise_for_status()
    chart_id = resp.json()["id"]
    invalidate_sql_cache("list_charts")
    return CreateChartResult(
        message=f"Chart '{payload['chart_name']}' created (id {chart_id}).",
        created=True,
        chart_id=chart_id,
        **chart_urls(chart_id),
    )


def create_chart(params: CreateChartParams) -> ChartPreviewResult | CreateChartResult:
    if params.confirm_token is None:
        return preview_chart(params)
    return commit_chart(params.confirm_token)


# --- update_chart ------------------------------------------------------------


class _UpdatePlan:
    """Everything both phases of update_chart need, computed once."""

    def __init__(
        self,
        sess: requests.Session,
        chart_id: int,
        current_result: dict[str, Any],
        dataset_id: int | None,
        effective_name: str,
        effective_viz_type: str,
        new_params: dict[str, Any],
        current_spec: ChartSpec,
    ) -> None:
        self.sess = sess
        self.chart_id = chart_id
        self.current_result = current_result
        self.dataset_id = dataset_id
        self.effective_name = effective_name
        self.effective_viz_type = effective_viz_type
        self.new_params = new_params
        self.current_spec = current_spec


def _plan_update(params: UpdateChartParams) -> _UpdatePlan:
    """Reads the chart and merges the caller's arguments over its current spec.

    Every field the caller leaves at None is inherited from the saved chart, which
    is what makes a partial update partial.
    """
    chart_id = parse_id(params.chart_id)
    sess = superset_session()
    current = sess.get(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", timeout=15)
    current.raise_for_status()
    current_result = current.json()["result"]
    dataset_id = current_result.get("datasource_id")
    effective_name = params.chart_name or current_result.get("slice_name", "")
    current_params = json.loads(current_result.get("params") or "{}")
    cur = extract_chart_spec(current_params)

    effective_viz_type = params.viz_type if params.viz_type is not None else cur.viz_type
    target_color_scheme = params.color_scheme
    if target_color_scheme is None:
        if params.color is not None:
            target_color_scheme = resolve_color_scheme(params.color)
        if target_color_scheme is None:
            target_color_scheme = cur.color_scheme

    target_x_title, target_y_title = _axis_titles(params)

    effective_lag = (
        params.comparison_period_lag
        if params.comparison_period_lag is not None
        else (params.compare_lag if params.compare_lag is not None else cur.comparison_period_lag or cur.compare_lag)
    )
    effective_suffix = (
        params.comparison_suffix
        if params.comparison_suffix is not None
        else (params.compare_suffix if params.compare_suffix is not None else cur.comparison_suffix or cur.compare_suffix)
    )

    new_spec = ChartSpec(
        viz_type=effective_viz_type,
        metrics=params.metrics if params.metrics is not None else cur.metrics,
        groupby=params.groupby if params.groupby is not None else cur.groupby,
        time_range=params.time_range if params.time_range is not None else cur.time_range,
        row_limit=params.row_limit if params.row_limit is not None else cur.row_limit,
        color_scheme=target_color_scheme,
        show_legend=params.show_legend if params.show_legend is not None else cur.show_legend,
        number_format=(
            params.number_format if params.number_format is not None else cur.number_format
        ),
        x_axis_sort=params.x_axis_sort if params.x_axis_sort is not None else cur.x_axis_sort,
        x_axis_sort_asc=(
            params.x_axis_sort_asc if params.x_axis_sort_asc is not None else cur.x_axis_sort_asc
        ),
        order_desc=params.order_desc if params.order_desc is not None else cur.order_desc,
        orientation=params.orientation if params.orientation is not None else cur.orientation,
        color=params.color if params.color is not None else cur.color,
        description=params.description if params.description is not None else cur.description,
        x_axis_title=target_x_title if target_x_title is not None else cur.x_axis_title,
        y_axis_title=target_y_title if target_y_title is not None else cur.y_axis_title,
        time_grain_sqla=params.time_grain_sqla if params.time_grain_sqla is not None else cur.time_grain_sqla,
        comparison_period_lag=effective_lag,
        compare_lag=effective_lag,
        comparison_suffix=effective_suffix,
        compare_suffix=effective_suffix,
        show_timestamp=params.show_timestamp if params.show_timestamp is not None else cur.show_timestamp,
        show_trend_line=params.show_trend_line if params.show_trend_line is not None else cur.show_trend_line,
        start_y_axis_at_zero=params.start_y_axis_at_zero if params.start_y_axis_at_zero is not None else cur.start_y_axis_at_zero,
        resample_rule=params.resample_rule if params.resample_rule is not None else cur.resample_rule,
        resample_fill_method=params.resample_fill_method if params.resample_fill_method is not None else cur.resample_fill_method,
        rolling_type=params.rolling_type if params.rolling_type is not None else cur.rolling_type,
        rolling_periods=params.rolling_periods if params.rolling_periods is not None else cur.rolling_periods,
        min_periods=params.min_periods if params.min_periods is not None else cur.min_periods,
    )

    return _UpdatePlan(
        sess=sess,
        chart_id=chart_id,
        current_result=current_result,
        dataset_id=dataset_id,
        effective_name=effective_name,
        effective_viz_type=effective_viz_type,
        new_params=build_chart_params(new_spec),
        current_spec=cur,
    )


def _update_chart_payload(params: UpdateChartParams) -> dict[str, Any]:
    """Confirm-token payload for update_chart: the caller's raw arguments.

    Stored but not replayed - `commit_chart_update` re-reads the chart and re-merges,
    so a chart edited in the Superset UI between the two turns is still respected.
    """
    effective_lag = params.comparison_period_lag if params.comparison_period_lag is not None else params.compare_lag
    effective_suffix = params.comparison_suffix if params.comparison_suffix is not None else params.compare_suffix
    return {
        "chart_id": parse_id(params.chart_id),
        "chart_name": params.chart_name,
        "viz_type": params.viz_type,
        "metrics": params.metrics,
        "groupby": params.groupby,
        "time_range": params.time_range,
        "row_limit": params.row_limit,
        "color_scheme": params.color_scheme,
        "show_legend": params.show_legend,
        "number_format": params.number_format,
        "x_axis_sort": params.x_axis_sort,
        "x_axis_sort_asc": params.x_axis_sort_asc,
        "order_desc": params.order_desc,
        "orientation": params.orientation,
        "color": params.color,
        "description": params.description,
        "x_axis_title": params.x_axis_title,
        "y_axis_title": params.y_axis_title,
        "time_grain_sqla": params.time_grain_sqla,
        "comparison_period_lag": effective_lag,
        "compare_lag": effective_lag,
        "comparison_suffix": effective_suffix,
        "compare_suffix": effective_suffix,
        "show_timestamp": params.show_timestamp,
        "show_trend_line": params.show_trend_line,
        "start_y_axis_at_zero": params.start_y_axis_at_zero,
        "resample_rule": params.resample_rule,
        "resample_fill_method": params.resample_fill_method,
        "rolling_type": params.rolling_type,
        "rolling_periods": params.rolling_periods,
        "min_periods": params.min_periods,
    }


def preview_chart_update(params: UpdateChartParams) -> UpdateChartPreviewResult:
    """Phase 1 of update_chart: renders the merged spec unsaved, issues a token."""
    plan = _plan_update(params)
    return UpdateChartPreviewResult(
        message=(
            f"Preview of the edit to '{plan.effective_name}'. Not saved yet - "
            f"confirm to apply it."
        ),
        updated=False,
        requires_confirmation=True,
        chart_id=plan.chart_id,
        chart_name=plan.effective_name,
        confirm_token=issue_create_token("update_chart", _update_chart_payload(params)),
        next_step=_UPDATE_NEXT_STEP,
        **explore_preview_url(plan.sess, plan.dataset_id, plan.effective_name, plan.new_params),
    )


def _sync_dashboard_label_colors(
    plan: _UpdatePlan, params: UpdateChartParams, resolved_color: str
) -> None:
    """Pushes the chart's new colour into every dashboard that shows it.

    A dashboard keeps its own label_colors map and it wins over the chart's, so
    without this the chart would change colour in Explore but not on the dashboard.
    Failures are logged, not raised: the chart itself is already saved.
    """
    for d in plan.current_result.get("dashboards") or []:
        dash_id = d.get("id")
        if not dash_id:
            continue
        try:
            dash_resp = plan.sess.get(f"{SUPERSET_URL}/api/v1/dashboard/{dash_id}", timeout=15)
            if dash_resp.ok:
                dash_res = dash_resp.json().get("result", {})
                meta = json.loads(dash_res.get("json_metadata") or "{}")
                label_cols = meta.setdefault("label_colors", {})
                map_cols = meta.setdefault("map_label_colors", {})
                target_metrics = (
                    params.metrics if params.metrics is not None else plan.current_spec.metrics
                )
                for m in target_metrics:
                    _, label = (
                        parse_metric_expression(m)
                        if isinstance(m, str)
                        else (str(m), str(m))
                    )
                    label_cols[label] = resolved_color
                    map_cols[label] = resolved_color
                meta["label_colors"] = label_cols
                meta["map_label_colors"] = map_cols
                plan.sess.put(
                    f"{SUPERSET_URL}/api/v1/dashboard/{dash_id}",
                    json={"json_metadata": json.dumps(meta)},
                    timeout=20,
                )
                invalidate_sql_cache(f"dashboard_info_{dash_id}")
        except Exception as e:
            logger.warning("Failed to sync dashboard label_colors: {}", e)


def commit_chart_update(params: UpdateChartParams) -> UpdateChartResult:
    """Phase 2 of update_chart: redeems the token and saves the chart."""
    plan = _plan_update(params)
    consume_create_token(params.confirm_token or "", "update_chart")

    payload: dict[str, Any] = {
        "viz_type": plan.effective_viz_type,
        "params": json.dumps(plan.new_params),
    }
    if params.chart_name is not None:
        payload["slice_name"] = params.chart_name
    if params.description is not None:
        payload["description"] = params.description

    resp = plan.sess.put(f"{SUPERSET_URL}/api/v1/chart/{plan.chart_id}", json=payload, timeout=20)
    resp.raise_for_status()

    resolved_color = resolve_color(params.color)
    if resolved_color:
        _sync_dashboard_label_colors(plan, params, resolved_color)

    invalidate_sql_cache(
        f"get_chart_{plan.chart_id}", f"chart_sql_{plan.chart_id}", "list_charts"
    )
    return UpdateChartResult(
        message=f"Chart {plan.chart_id} updated.",
        updated=True,
        chart_id=plan.chart_id,
        **chart_urls(plan.chart_id),
    )


def update_chart(params: UpdateChartParams) -> UpdateChartPreviewResult | UpdateChartResult:
    if params.confirm_token is None:
        return preview_chart_update(params)
    return commit_chart_update(params)


# --- chart reads -------------------------------------------------------------


def get_chart(params: GetChartParams) -> GetChartResult:
    chart_id = parse_id(params.chart_id)
    cache_key = f"get_chart_{chart_id}"
    cached = get_cached_model(cache_key, GetChartResult)
    if cached is not None:
        return cached

    sess = superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", {})
    spec = extract_chart_spec(json.loads(result.get("params") or "{}"))

    spec_fields = spec.model_dump()
    # The chart object's own description wins over the one embedded in params.
    spec_fields["description"] = result.get("description") or spec.description

    res = GetChartResult(
        message=f"Chart {chart_id}: {result.get('slice_name')}",
        chart_id=chart_id,
        chart_name=result.get("slice_name"),
        dataset_id=result.get("datasource_id"),
        dashboards=[
            DashboardRef(id=d.get("id"), title=d.get("dashboard_title"))
            for d in (result.get("dashboards") or [])
        ],
        **spec_fields,
        **chart_urls(chart_id),
    )
    set_cached_model(cache_key, res)
    return res


def get_chart_preview(params: GetChartPreviewParams) -> GetChartPreviewResult:
    chart_id = parse_id(params.chart_id)
    return GetChartPreviewResult(
        message=f"Embed URLs for chart {chart_id}.", **chart_urls(chart_id)
    )


def resolve_chart_sql(
    sess: requests.Session,
    datasource_id: Any,
    params: dict[str, Any],
    data_timeout: int = 20,
    dataset_timeout: int = 15,
) -> str | None:
    """The SQL a chart runs, asked of Superset and falling back to the dataset's own.

    Shared with get_dashboard_info, which needs the same SQL for every attached chart
    but on shorter timeouts, since it resolves one per chart in a loop.
    """
    sql_query = None
    try:
        payload = {
            "datasource": {"id": datasource_id, "type": "table"},
            "queries": [params],
            "result_type": "query",
        }
        sql_resp = sess.post(f"{SUPERSET_URL}/api/v1/chart/data", json=payload, timeout=data_timeout)
        if sql_resp.ok:
            queries = sql_resp.json().get("result", [])
            if queries and isinstance(queries, list) and len(queries) > 0:
                sql_query = queries[0].get("query")
    except Exception:
        pass

    if not sql_query and datasource_id:
        try:
            ds_resp = sess.get(
                f"{SUPERSET_URL}/api/v1/dataset/{datasource_id}", timeout=dataset_timeout
            )
            if ds_resp.ok:
                ds = ds_resp.json().get("result", {})
                sql_query = ds.get("sql") or f"SELECT * FROM {ds.get('table_name')}"
        except Exception:
            pass
    return sql_query


def get_chart_sql(params: GetChartSqlParams) -> GetChartSqlResult:
    chart_id = parse_id(params.chart_id)
    cache_key = f"chart_sql_{chart_id}"
    cached = get_cached_model(cache_key, GetChartSqlResult)
    if cached is not None:
        return cached

    sess = superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", timeout=15)
    resp.raise_for_status()
    chart = resp.json().get("result", {})
    sql_query = resolve_chart_sql(
        sess,
        chart.get("datasource_id"),
        json.loads(chart.get("params") or "{}"),
    )

    res = GetChartSqlResult(
        message=f"Generated SQL for chart {chart_id}.",
        chart_id=chart_id,
        sql=sql_query or "Query unavailable for this chart type",
    )
    set_cached_model(cache_key, res)
    return res


def list_charts(params: ListChartsParams) -> ListChartsResult:
    cache_key = "list_charts"
    cached = get_cached_model(cache_key, ListChartsResult)
    if cached is not None:
        return cached

    sess = superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/chart/?q=(page_size:1000)", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", [])
    res = ListChartsResult(
        message=f"{len(result)} charts in Superset.",
        count=len(result),
        charts=[
            ChartSummary(
                id=c.get("id"),
                slice_name=c.get("slice_name"),
                viz_type=c.get("viz_type"),
                datasource_id=c.get("datasource_id"),
                datasource_name=c.get("datasource_name_title"),
            )
            for c in result
        ],
    )
    set_cached_model(cache_key, res)
    return res
