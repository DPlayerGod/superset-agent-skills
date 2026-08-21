"""Business logic for the Superset dashboard tools."""

from __future__ import annotations

import json

import requests

from superset_mcp.config import SUPERSET_URL
from superset_mcp.logic.chart_logic import resolve_chart_sql
from superset_mcp.models.chart import GetChartSqlResult
from superset_mcp.models.dashboard import (
    AddChartsToDashboardParams,
    AddChartsToDashboardResult,
    ChartAttachPreview,
    CreateDashboardParams,
    CreateDashboardPreviewResult,
    CreateDashboardResult,
    DashboardChartInfo,
    DashboardPreview,
    DashboardSummary,
    GetDashboardInfoParams,
    GetDashboardInfoResult,
    ListDashboardsParams,
    ListDashboardsResult,
    UpdateDashboardParams,
    UpdateDashboardResult,
)
from superset_mcp.services.cache import (
    clear_sql_cache,
    get_cached_model,
    invalidate_sql_cache,
    set_cached_model,
)
from superset_mcp.services.chart_params import extract_chart_spec
from superset_mcp.services.colors import resolve_color, resolve_color_scheme
from superset_mcp.services.ids import parse_id
from superset_mcp.services.superset_client import superset_session
from superset_mcp.services.tokens import consume_create_token, issue_create_token
from superset_mcp.services.urls import dashboard_urls

_CREATE_NEXT_STEP = (
    "Show the user the dashboard title, the charts that will be attached, "
    "and any dashboard they would be moved off of. Only call "
    "create_dashboard again, with confirm_token, once they answer yes."
)


# --- create_dashboard --------------------------------------------------------


def preview_dashboard(params: CreateDashboardParams) -> CreateDashboardPreviewResult:
    """Phase 1 of create_dashboard: nothing is created, a token is issued.

    The preview names, per chart, the dashboards it would be moved OFF of, because
    attaching replaces a chart's dashboard membership instead of adding to it.
    """
    chart_ids = [parse_id(c) for c in params.chart_ids]
    sess = superset_session()

    charts_preview: list[ChartAttachPreview] = []
    for chart_id in chart_ids:
        current = sess.get(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", timeout=15)
        current.raise_for_status()
        result = current.json().get("result", {})
        charts_preview.append(
            ChartAttachPreview(
                chart_id=chart_id,
                chart_name=result.get("slice_name"),
                moved_off=[d.get("dashboard_title") for d in (result.get("dashboards") or [])],
            )
        )

    return CreateDashboardPreviewResult(
        message=(
            f"Preview of dashboard '{params.dashboard_title}' with "
            f"{len(charts_preview)} charts. Not saved yet - confirm to create it."
        ),
        created=False,
        requires_confirmation=True,
        preview=DashboardPreview(
            dashboard_title=params.dashboard_title, charts=charts_preview
        ),
        confirm_token=issue_create_token(
            "create_dashboard",
            {"dashboard_title": params.dashboard_title, "chart_ids": chart_ids},
        ),
        next_step=_CREATE_NEXT_STEP,
    )


def commit_dashboard(confirm_token: str) -> CreateDashboardResult:
    """Phase 2 of create_dashboard: creates the dashboard and attaches the charts."""
    payload = consume_create_token(confirm_token, "create_dashboard")
    sess = superset_session()
    resp = sess.post(
        f"{SUPERSET_URL}/api/v1/dashboard/",
        json={"dashboard_title": payload["dashboard_title"]},
        timeout=20,
    )
    resp.raise_for_status()
    dashboard_id = resp.json()["id"]

    for chart_id in payload["chart_ids"]:
        attach = sess.put(
            f"{SUPERSET_URL}/api/v1/chart/{chart_id}",
            json={"dashboards": [dashboard_id]},
            timeout=20,
        )
        attach.raise_for_status()
        invalidate_sql_cache(f"get_chart_{chart_id}")

    invalidate_sql_cache("list_dashboards")
    return CreateDashboardResult(
        message=f"Dashboard '{payload['dashboard_title']}' created (id {dashboard_id}).",
        created=True,
        dashboard_id=dashboard_id,
        chart_ids=payload["chart_ids"],
        **dashboard_urls(dashboard_id),
    )


def create_dashboard(
    params: CreateDashboardParams,
) -> CreateDashboardPreviewResult | CreateDashboardResult:
    if params.confirm_token is None:
        return preview_dashboard(params)
    return commit_dashboard(params.confirm_token)


# --- add_charts_to_dashboard -------------------------------------------------


def add_charts_to_dashboard(params: AddChartsToDashboardParams) -> AddChartsToDashboardResult:
    """Attaches charts to an existing dashboard, keeping their current memberships."""
    clear_sql_cache()
    dashboard_id = parse_id(params.dashboard_id)
    chart_ids = [parse_id(c) for c in params.chart_ids]
    sess = superset_session()
    check = sess.get(f"{SUPERSET_URL}/api/v1/dashboard/{dashboard_id}", timeout=15)
    check.raise_for_status()
    title = check.json().get("result", {}).get("dashboard_title")

    added: list[int] = []
    already_present: list[int] = []
    for chart_id in chart_ids:
        current = sess.get(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", timeout=15)
        current.raise_for_status()
        existing_ids = [d["id"] for d in (current.json().get("result", {}).get("dashboards") or [])]
        if dashboard_id in existing_ids:
            already_present.append(chart_id)
            continue
        attach = sess.put(
            f"{SUPERSET_URL}/api/v1/chart/{chart_id}",
            json={"dashboards": existing_ids + [dashboard_id]},
            timeout=20,
        )
        attach.raise_for_status()
        added.append(chart_id)

    return AddChartsToDashboardResult(
        message=f"{len(added)} charts added, {len(already_present)} already present.",
        dashboard_id=dashboard_id,
        dashboard_title=title,
        added=added,
        already_present=already_present,
        **dashboard_urls(dashboard_id),
    )


# --- dashboard reads ---------------------------------------------------------


def _resolve_dashboard_id(sess: requests.Session, dashboard_id: int | str) -> int | None:
    """Accepts an integer id, a UUID (dashboard or embedded), or a slug.

    Resolves via Superset REST API. Falls back to the lowest dashboard id when nothing matches.
    """
    if isinstance(dashboard_id, int):
        return dashboard_id
    if isinstance(dashboard_id, str) and dashboard_id.isdigit():
        return int(dashboard_id)

    try:
        resp = sess.get(f"{SUPERSET_URL}/api/v1/dashboard/?q=(page_size:100)", timeout=10)
        if resp.status_code == 200:
            dashboards = resp.json().get("result", [])
            for d in dashboards:
                if str(d.get("id")) == str(dashboard_id) or str(d.get("slug")) == str(dashboard_id):
                    return d.get("id")
            if dashboards:
                return dashboards[0].get("id")
    except Exception:
        pass

    return 1


def _dashboard_charts(sess: requests.Session, dashboard_detail: dict) -> list[DashboardChartInfo]:
    """Every chart on the dashboard, with its spec and its generated SQL, fetched via Superset API."""
    charts_list: list[DashboardChartInfo] = []
    slices = dashboard_detail.get("slices") or []

    for s in slices:
        try:
            chart_id = s.get("id")
            if not chart_id:
                continue

            # Fetch chart details from Superset REST API
            chart_resp = sess.get(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", timeout=10)
            if chart_resp.status_code != 200:
                continue

            chart_data = chart_resp.json().get("result", {})
            params_raw = chart_data.get("params") or "{}"
            params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
            spec = extract_chart_spec(params)
            datasource_id = chart_data.get("datasource_id") or s.get("datasource_id")

            # Fetch/generate SQL for chart, reusing get_chart_sql's cache entry.
            sql_cache_key = f"chart_sql_{chart_id}"
            cached_sql = get_cached_model(sql_cache_key, GetChartSqlResult)
            if cached_sql is not None and cached_sql.sql:
                chart_sql = cached_sql.sql
            else:
                chart_sql = resolve_chart_sql(
                    sess,
                    datasource_id,
                    params,
                    data_timeout=10,
                    dataset_timeout=10,
                )
                chart_sql = chart_sql or "Query unavailable"
                set_cached_model(
                    sql_cache_key,
                    GetChartSqlResult(
                        message=f"Generated SQL for chart {chart_id}.",
                        chart_id=chart_id,
                        sql=chart_sql,
                    ),
                )

            charts_list.append(
                DashboardChartInfo(
                    id=chart_id,
                    slice_name=chart_data.get("slice_name") or s.get("slice_name"),
                    viz_type=chart_data.get("viz_type") or s.get("viz_type"),
                    datasource_id=datasource_id,
                    metrics=spec.metrics,
                    groupby=spec.groupby,
                    time_range=spec.time_range,
                    row_limit=spec.row_limit,
                    sql=chart_sql,
                )
            )
        except Exception:
            pass

    return charts_list



def get_dashboard_info(params: GetDashboardInfoParams) -> GetDashboardInfoResult:
    cache_key = f"dashboard_info_{params.dashboard_id}"
    cached = get_cached_model(cache_key, GetDashboardInfoResult)
    if cached is not None:
        return cached

    sess = superset_session()
    target_id = _resolve_dashboard_id(sess, params.dashboard_id)

    resp = sess.get(f"{SUPERSET_URL}/api/v1/dashboard/{target_id}", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", {})

    charts_list = _dashboard_charts(sess, result)

    res = GetDashboardInfoResult(
        message=(
            f"Dashboard {result.get('id')}: {result.get('dashboard_title')} "
            f"({len(charts_list)} charts)."
        ),
        id=result.get("id"),
        dashboard_title=result.get("dashboard_title"),
        slug=result.get("slug"),
        published=result.get("published"),
        charts_count=len(charts_list),
        charts=charts_list,
        position_json=result.get("position_json"),
        css=result.get("css"),
    )
    set_cached_model(cache_key, res)
    if target_id and str(target_id) != str(params.dashboard_id):
        set_cached_model(f"dashboard_info_{target_id}", res)
    return res


def list_dashboards(params: ListDashboardsParams) -> ListDashboardsResult:
    cache_key = "list_dashboards"
    cached = get_cached_model(cache_key, ListDashboardsResult)
    if cached is not None:
        return cached

    sess = superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/dashboard/?q=(page_size:1000)", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", [])
    res = ListDashboardsResult(
        message=f"{len(result)} dashboards in Superset.",
        count=len(result),
        dashboards=[
            DashboardSummary(
                id=d.get("id"),
                dashboard_title=d.get("dashboard_title"),
                slug=d.get("slug"),
                published=d.get("published"),
            )
            for d in result
        ],
    )
    set_cached_model(cache_key, res)
    return res


def update_dashboard(params: UpdateDashboardParams) -> UpdateDashboardResult:
    """Updates dashboard properties such as title, color scheme, or label colors.

    Can clear label_colors override map so individual charts control their own colors.
    """
    sess = superset_session()
    target_id = _resolve_dashboard_id(sess, params.dashboard_id)
    resp = sess.get(f"{SUPERSET_URL}/api/v1/dashboard/{target_id}", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", {})

    meta = json.loads(result.get("json_metadata") or "{}")
    payload: dict[str, Any] = {}

    if params.dashboard_title is not None:
        payload["dashboard_title"] = params.dashboard_title

    metadata_changed = False
    if params.clear_label_colors:
        meta["label_colors"] = {}
        meta["map_label_colors"] = {}
        metadata_changed = True
    elif params.label_colors is not None:
        label_cols = meta.setdefault("label_colors", {})
        map_cols = meta.setdefault("map_label_colors", {})
        for k, v in params.label_colors.items():
            resolved = resolve_color(v) or v
            label_cols[k] = resolved
            map_cols[k] = resolved
        meta["label_colors"] = label_cols
        meta["map_label_colors"] = map_cols
        metadata_changed = True

    if params.color_scheme is not None:
        resolved_scheme = resolve_color_scheme(params.color_scheme) or params.color_scheme
        meta["color_scheme"] = resolved_scheme
        metadata_changed = True

    if metadata_changed:
        payload["json_metadata"] = json.dumps(meta)

    if payload:
        put_resp = sess.put(f"{SUPERSET_URL}/api/v1/dashboard/{target_id}", json=payload, timeout=20)
        put_resp.raise_for_status()

    invalidate_sql_cache(
        f"dashboard_info_{params.dashboard_id}",
        f"dashboard_info_{target_id}",
        "list_dashboards",
    )
    return UpdateDashboardResult(
        message=f"Dashboard {target_id} updated successfully.",
        dashboard_id=target_id,
        dashboard_title=params.dashboard_title or result.get("dashboard_title"),
        updated=True,
        **dashboard_urls(target_id),
    )

