import json
import os
import re
import secrets
import time
from typing import Any

import requests
from sqlalchemy import create_engine, text
from mcp.server.fastmcp import FastMCP

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://super_user:super_pass@localhost:5432/super_db",
)
MAX_LIMIT = int(os.getenv("MCP_MAX_ROWS", "500"))

SUPERSET_URL = os.getenv("SUPERSET_URL", "http://superset:8088").rstrip("/")
# Everything the browser has to load (links, preview iframes) is built from this
# instead of SUPERSET_URL, whose Docker-internal hostname does not resolve there.
SUPERSET_PUBLIC_URL = os.getenv("SUPERSET_PUBLIC_URL", SUPERSET_URL).rstrip("/")
SUPERSET_ADMIN_USERNAME = os.getenv("SUPERSET_ADMIN_USERNAME", "admin")
SUPERSET_ADMIN_PASSWORD = os.getenv("SUPERSET_ADMIN_PASSWORD", "admin")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
mcp = FastMCP("superset-postgres-mcp")


def _is_readonly_sql(sql: str) -> bool:
    compact = re.sub(r"\s+", " ", sql.strip()).lower()
    if not compact:
        return False
    forbidden = [
        "insert ",
        "update ",
        "delete ",
        "drop ",
        "alter ",
        "truncate ",
        "create ",
        "grant ",
        "revoke ",
        "comment ",
        "copy ",
        "call ",
        "do ",
    ]
    if any(token in compact for token in forbidden):
        return False
    return compact.startswith("select") or compact.startswith("with")


def _enforce_limit(sql: str, row_limit: int) -> str:
    safe_limit = max(1, min(row_limit, MAX_LIMIT))
    if re.search(r"\blimit\s+\d+", sql, flags=re.IGNORECASE):
        return sql
    return f"{sql.rstrip(';')} LIMIT {safe_limit};"


def _rows_to_json(rows: list[tuple[Any, ...]], columns: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for idx, value in enumerate(row):
            if hasattr(value, "isoformat"):
                item[columns[idx]] = value.isoformat()
            else:
                item[columns[idx]] = value
        out.append(item)
    return out


@mcp.tool()
def list_datasets(schema: str = "public") -> dict[str, Any]:
    query = text(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema = :schema
          AND table_type = 'BASE TABLE'
        ORDER BY table_name;
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {"schema": schema}).fetchall()

    return {
        "schema": schema,
        "count": len(rows),
        "tables": [
            {"table_schema": r[0], "table_name": r[1]}
            for r in rows
        ],
    }


@mcp.tool()
def describe_table(table_name: str, schema: str = "public") -> dict[str, Any]:
    query = text(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = :schema
          AND table_name = :table_name
        ORDER BY ordinal_position;
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {"schema": schema, "table_name": table_name}).fetchall()

    return {
        "schema": schema,
        "table": table_name,
        "columns": [
            {"name": r[0], "type": r[1], "nullable": r[2]}
            for r in rows
        ],
    }


@mcp.tool()
def run_sql_readonly(sql: str, row_limit: int = 200) -> dict[str, Any]:
    if not _is_readonly_sql(sql):
        raise ValueError("Only SELECT/CTE read-only SQL is allowed")

    safe_sql = _enforce_limit(sql, row_limit)
    with engine.connect() as conn:
        result = conn.execute(text(safe_sql))
        rows = result.fetchall()
        columns = list(result.keys())

    return {
        "sql": safe_sql,
        "row_count": len(rows),
        "columns": columns,
        "rows": _rows_to_json(rows, columns),
    }


# --- Superset REST API write tools ---------------------------------------
#
# All write operations below authenticate to Superset as a fixed service
# account (SUPERSET_ADMIN_USERNAME/PASSWORD, defaulting to the admin/admin
# account bootstrapped by the superset container's entrypoint). They are NOT
# scoped to whichever Superset user is chatting - every chart/dashboard
# created via these tools is owned by that fixed account. This is an accepted
# V1 tradeoff, not per-user identity forwarding.

_session_cache: dict[str, Any] = {"session": None, "expires_at": 0.0}


def _superset_session() -> requests.Session:
    now = time.time()
    cached = _session_cache["session"]
    if cached is not None and now < _session_cache["expires_at"]:
        return cached

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            sess = requests.Session()
            login = sess.post(
                f"{SUPERSET_URL}/api/v1/security/login",
                json={
                    "username": SUPERSET_ADMIN_USERNAME,
                    "password": SUPERSET_ADMIN_PASSWORD,
                    "provider": "db",
                    "refresh": True,
                },
                timeout=15,
            )
            login.raise_for_status()
            access_token = login.json()["access_token"]
            sess.headers["Authorization"] = f"Bearer {access_token}"

            csrf = sess.get(f"{SUPERSET_URL}/api/v1/security/csrf_token/", timeout=15)
            csrf.raise_for_status()
            sess.headers["X-CSRFToken"] = csrf.json()["result"]
            sess.headers["Referer"] = SUPERSET_URL

            _session_cache["session"] = sess
            _session_cache["expires_at"] = now + 600
            return sess
        except requests.RequestException as error:
            last_error = error
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Unable to authenticate to Superset at {SUPERSET_URL}: {last_error}")


def _rison_filter(column: str, value: str) -> str:
    escaped = value.replace("'", "''")
    return f"(filters:!((col:{column},opr:eq,value:'{escaped}')))"


def _get_or_create_postgres_database(sess: requests.Session) -> int:
    resp = sess.get(f"{SUPERSET_URL}/api/v1/database/", params={"q": "(page_size:100)"}, timeout=15)
    resp.raise_for_status()
    for row in resp.json().get("result", []):
        if row.get("backend") == "postgresql":
            return row["id"]

    create = sess.post(
        f"{SUPERSET_URL}/api/v1/database/",
        json={
            "database_name": "super_db (postgres)",
            "sqlalchemy_uri": DATABASE_URL.replace("localhost", "postgres"),
            "engine": "postgresql",
        },
        timeout=20,
    )
    create.raise_for_status()
    return create.json()["id"]


def _find_dataset(sess: requests.Session, database_id: int, table_name: str, schema: str) -> dict[str, Any] | None:
    resp = sess.get(
        f"{SUPERSET_URL}/api/v1/dataset/",
        params={"q": _rison_filter("table_name", table_name)},
        timeout=15,
    )
    resp.raise_for_status()
    for row in resp.json().get("result", []):
        if row.get("table_name") == table_name and row.get("database", {}).get("id") == database_id:
            return row
    return None


@mcp.tool()
def create_dataset(table_name: str, schema: str = "public", sql: str | None = None) -> dict[str, Any]:
    """Registers a Superset dataset (get-or-create).

    Without `sql`, table_name must be a real Postgres table and the dataset is a
    plain physical one.

    With `sql`, this creates a VIRTUAL dataset: table_name becomes its name (pick a
    new descriptive one, not an existing table's) and the query becomes its source.
    Use it whenever a chart needs shaping the chart params cannot express - window
    functions, per-group ranking, HAVING, joins, derived columns. For example
    "bottom 10 people by allocation within each project" is a virtual dataset over
    ROW_NUMBER() OVER (PARTITION BY project_name ORDER BY SUM(...) ASC), then a
    normal create_chart on top of it.

    The SQL is held to the same SELECT/CTE-only rule as run_sql_readonly. Re-calling
    with the same table_name and different sql updates the stored query rather than
    creating a duplicate, so iterating on the query is safe.
    """
    sess = _superset_session()
    database_id = _get_or_create_postgres_database(sess)

    if sql is not None:
        sql = sql.strip().rstrip(";")
        if not _is_readonly_sql(sql):
            raise ValueError("Only SELECT/CTE statements are allowed for a virtual dataset.")

    existing = _find_dataset(sess, database_id, table_name, schema)
    if existing:
        if sql is None:
            return {"dataset_id": existing["id"], "already_existed": True}
        # The list endpoint does not return `sql`, and comparing against a missing
        # field would re-PUT (and re-sync columns) on every single call.
        detail = sess.get(f"{SUPERSET_URL}/api/v1/dataset/{existing['id']}", timeout=15)
        detail.raise_for_status()
        current_sql = (detail.json().get("result", {}).get("sql") or "").strip().rstrip(";")
        if current_sql == sql:
            return {"dataset_id": existing["id"], "already_existed": True}
        update = sess.put(
            f"{SUPERSET_URL}/api/v1/dataset/{existing['id']}",
            json={"sql": sql},
            params={"override_columns": "true"},
            timeout=30,
        )
        update.raise_for_status()
        return {"dataset_id": existing["id"], "already_existed": True, "sql_updated": True}

    payload: dict[str, Any] = {"database": database_id, "schema": schema, "table_name": table_name}
    if sql is not None:
        payload["sql"] = sql
    resp = sess.post(f"{SUPERSET_URL}/api/v1/dataset/", json=payload, timeout=30)
    resp.raise_for_status()
    return {"dataset_id": resp.json()["id"], "already_existed": False, "virtual": sql is not None}


def _adhoc_metric(expression: str) -> dict[str, Any]:
    return {
        "expressionType": "SQL",
        "sqlExpression": expression,
        "label": expression,
    }


def _build_chart_params(
    viz_type: str,
    metrics: list[str],
    groupby: list[str],
    time_range: str,
    row_limit: int,
    color_scheme: str | None = None,
    show_legend: bool | None = None,
    number_format: str | None = None,
) -> dict[str, Any]:
    adhoc_metrics = [_adhoc_metric(metric) for metric in metrics]
    params: dict[str, Any] = {
        "viz_type": viz_type,
        "metrics": adhoc_metrics,
        "groupby": list(groupby),
        "adhoc_filters": [],
        "row_limit": row_limit,
        "time_range": time_range,
    }
    if viz_type in ("echarts_timeseries_line", "echarts_timeseries_bar"):
        params["x_axis"] = groupby[0] if groupby else None
        params["groupby"] = list(groupby[1:])
    elif viz_type == "pie":
        # Pie's control panel is [["groupby"], ["metric"]] - it reads a single
        # `metric` and ignores `metrics` entirely, so a plural list renders blank.
        params["metric"] = adhoc_metrics[0] if adhoc_metrics else None
        params.pop("metrics", None)
    elif viz_type == "big_number_total":
        params["metric"] = adhoc_metrics[0] if adhoc_metrics else None
        params.pop("metrics", None)
        params.pop("groupby", None)
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
    if color_scheme is not None:
        if viz_type == "heatmap_v2":
            params["linear_color_scheme"] = color_scheme
        elif viz_type not in ("table", "big_number_total"):
            params["color_scheme"] = color_scheme
    if show_legend is not None and viz_type in (
        "echarts_timeseries_line",
        "echarts_timeseries_bar",
        "pie",
        "sunburst_v2",
    ):
        params["show_legend"] = show_legend
    if number_format is not None:
        if viz_type in ("echarts_timeseries_line", "echarts_timeseries_bar"):
            params["y_axis_format"] = number_format
        elif viz_type != "table":
            params["number_format"] = number_format
    return params


# `standalone` values verified against this exact Superset 6.1.0 build rather than
# assumed: the explore view treats the parameter as a boolean
# (superset/utils/core.py: any value other than "0"/"false" counts as standalone),
# while the dashboard frontend reads it as the numeric DashboardStandaloneMode enum
# (None=0, HideNav=1, HideNavAndTitle=2, Report=3 - read out of the shipped JS
# bundle). Chart previews use 1; dashboard previews use 2 so the title bar does not
# eat vertical space in the small chat iframe.
_EXPLORE_STANDALONE = "1"
_DASHBOARD_STANDALONE = "2"


def _chart_urls(chart_id: int) -> dict[str, Any]:
    base = f"{SUPERSET_PUBLIC_URL}/explore/?slice_id={chart_id}"
    return {"type": "chart", "url": base, "embed_url": f"{base}&standalone={_EXPLORE_STANDALONE}"}


def _dashboard_urls(dashboard_id: int) -> dict[str, Any]:
    base = f"{SUPERSET_PUBLIC_URL}/superset/dashboard/{dashboard_id}/"
    return {
        "type": "dashboard",
        "url": base,
        "embed_url": f"{base}?standalone={_DASHBOARD_STANDALONE}",
    }


def _explore_preview_url(
    sess: requests.Session, dataset_id: int, chart_name: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Renders a chart from unsaved form_data via Superset's own ExploreFormData
    API - the same mechanism Superset's Explore UI uses for its URL while you are
    still building a chart, before you hit Save. This stores the form_data
    server-side under a short-lived key so the Explore page can fetch it back;
    it does NOT create a chart object, so nothing here touches Superset's chart
    table. (A raw `form_data=<json>` query param on `/explore/` is not read by
    this build's frontend - only `form_data_key` is - so the key round-trip
    below is required, not optional.)
    """
    form_data = {"datasource": f"{dataset_id}__table", "slice_name": chart_name, **params}
    resp = sess.post(
        f"{SUPERSET_URL}/api/v1/explore/form_data",
        json={"datasource_id": dataset_id, "datasource_type": "table", "form_data": json.dumps(form_data)},
        timeout=15,
    )
    resp.raise_for_status()
    key = resp.json()["key"]
    base = f"{SUPERSET_PUBLIC_URL}/explore/?form_data_key={key}"
    return {"type": "chart", "url": base, "embed_url": f"{base}&standalone={_EXPLORE_STANDALONE}"}


@mcp.tool()
def create_chart(
    dataset_id: int,
    chart_name: str,
    viz_type: str,
    metrics: list[str],
    groupby: list[str] | None = None,
    time_range: str = "No filter",
    row_limit: int = 1000,
    color_scheme: str | None = None,
    show_legend: bool | None = None,
    number_format: str | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Creates a Superset chart on an existing dataset. Two calls, with the user's
    answer in between - nothing is saved to Superset until they confirm.

    Call it WITHOUT confirm_token first: no chart is created, and you get back a
    live preview (rendered from unsaved form_data, exactly like Superset's own
    Explore screen before you hit Save) plus a confirm_token. Show the user the
    preview and ask them to confirm. Only after they say yes, call again with the
    SAME arguments plus that confirm_token - a token issued in the current turn is
    refused, so the two calls cannot be chained inside one answer.

    viz_type: one of "table", "echarts_timeseries_line", "echarts_timeseries_bar",
    "pie", "big_number_total", "sunburst_v2", "heatmap_v2".
    "sunburst_v2": one metric, groupby is the ring hierarchy (outermost ring last),
    e.g. ["organization_name", "project_name"].
    "heatmap_v2": one metric, groupby[0] is the x-axis and groupby[1] the y-axis;
    it takes exactly two dimensions.
    metrics: SQL aggregate expressions, e.g. ["SUM(project_allocated_hc)"].
    Note: "pie" and "big_number_total" only support a single metric - only
    metrics[0] is used. To compare several metrics, use "table" or
    "echarts_timeseries_bar" instead.
    groupby: for "echarts_timeseries_line"/"echarts_timeseries_bar", groupby[0]
    becomes the x-axis and the remaining entries become the series split, so the
    order carries meaning: for "FTE per project per month", pass
    ["working_month_year", "project_name"]. Reversing it plots projects on the
    x-axis and silently drops the monthly breakdown the question asked for.

    Style controls - all optional, ignored (left at Superset's default) for viz_types
    that do not support them:
    color_scheme: a Superset palette id (e.g. "supersetColors", "d3Category10",
    "googleCategory10c"). Applies to "echarts_timeseries_line/bar", "pie",
    "sunburst_v2". For "heatmap_v2" this instead sets the continuous color scale
    (Superset calls that control "linear_color_scheme" internally, but pass the same
    argument here - a categorical palette id there will just fall back to Superset's
    default gradient rather than error). Not supported by "table"/"big_number_total".
    show_legend: only applies to "echarts_timeseries_line/bar", "pie", "sunburst_v2".
    number_format: a D3 format string for the metric value, e.g. ",.1f" (1 decimal,
    thousands separator) or ".0%" (whole percent). Not supported by "table" (which
    formats per-column instead).
    """
    params = _build_chart_params(
        viz_type, metrics, groupby or [], time_range, row_limit, color_scheme, show_legend, number_format
    )
    sess = _superset_session()

    if confirm_token is None:
        return {
            "created": False,
            "requires_confirmation": True,
            "chart_name": chart_name,
            "viz_type": viz_type,
            "metrics": metrics,
            "groupby": groupby or [],
            "confirm_token": _issue_create_token(
                "create_chart",
                {
                    "dataset_id": dataset_id,
                    "chart_name": chart_name,
                    "viz_type": viz_type,
                    "metrics": metrics,
                    "groupby": groupby or [],
                    "time_range": time_range,
                    "row_limit": row_limit,
                    "color_scheme": color_scheme,
                    "show_legend": show_legend,
                    "number_format": number_format,
                },
            ),
            "next_step": (
                "Show this preview to the user and ask them to confirm before it is "
                "saved. Only call create_chart again, with confirm_token, once they "
                "answer yes."
            ),
            **_explore_preview_url(sess, dataset_id, chart_name, params),
        }

    payload = _consume_create_token(confirm_token, "create_chart")
    saved_params = _build_chart_params(
        payload["viz_type"],
        payload["metrics"],
        payload["groupby"],
        payload["time_range"],
        payload["row_limit"],
        payload.get("color_scheme"),
        payload.get("show_legend"),
        payload.get("number_format"),
    )
    resp = sess.post(
        f"{SUPERSET_URL}/api/v1/chart/",
        json={
            "slice_name": payload["chart_name"],
            "viz_type": payload["viz_type"],
            "datasource_id": payload["dataset_id"],
            "datasource_type": "table",
            "params": json.dumps(saved_params),
        },
        timeout=20,
    )
    resp.raise_for_status()
    chart_id = resp.json()["id"]
    return {"created": True, "chart_id": chart_id, **_chart_urls(chart_id)}


def _extract_chart_spec(
    params: dict[str, Any],
) -> tuple[str, list[str], list[str], str, int, str | None, bool | None, str | None]:
    """Reverses _build_chart_params so update_chart can inherit fields the caller omits."""
    viz_type = params.get("viz_type", "table")
    time_range = params.get("time_range", "No filter")
    row_limit = params.get("row_limit", 1000)
    color_scheme = params.get("linear_color_scheme") if viz_type == "heatmap_v2" else params.get("color_scheme")
    show_legend = params.get("show_legend")
    number_format = (
        params.get("y_axis_format")
        if viz_type in ("echarts_timeseries_line", "echarts_timeseries_bar")
        else params.get("number_format")
    )

    def expr(metric: Any) -> str:
        return metric.get("sqlExpression", "") if isinstance(metric, dict) else str(metric)

    if viz_type in ("pie", "big_number_total", "sunburst_v2"):
        # `or params.get("metrics")` recovers charts written before pie was mapped
        # to the singular `metric`, so a partial update does not blank them out.
        metric = params.get("metric") or next(iter(params.get("metrics") or []), None)
        metrics = [expr(metric)] if metric else []
        groupby: list[str] = [] if viz_type == "big_number_total" else list(params.get("groupby", []))
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
    return viz_type, metrics, groupby, time_range, row_limit, color_scheme, show_legend, number_format


@mcp.tool()
def update_chart(
    chart_id: int,
    chart_name: str | None = None,
    viz_type: str | None = None,
    metrics: list[str] | None = None,
    groupby: list[str] | None = None,
    time_range: str | None = None,
    row_limit: int | None = None,
    color_scheme: str | None = None,
    show_legend: bool | None = None,
    number_format: str | None = None,
) -> dict[str, Any]:
    """Edits an existing Superset chart in place. Any argument left as None keeps
    its current value; pass only the fields you want to change.

    Same viz_type/metrics semantics as create_chart, including the color_scheme/
    show_legend/number_format style controls.
    """
    sess = _superset_session()
    current = sess.get(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", timeout=15)
    current.raise_for_status()
    current_result = current.json()["result"]
    current_params = json.loads(current_result.get("params") or "{}")
    (
        cur_viz,
        cur_metrics,
        cur_groupby,
        cur_time_range,
        cur_row_limit,
        cur_color_scheme,
        cur_show_legend,
        cur_number_format,
    ) = _extract_chart_spec(current_params)

    effective_viz_type = viz_type if viz_type is not None else cur_viz
    new_params = _build_chart_params(
        effective_viz_type,
        metrics if metrics is not None else cur_metrics,
        groupby if groupby is not None else cur_groupby,
        time_range if time_range is not None else cur_time_range,
        row_limit if row_limit is not None else cur_row_limit,
        color_scheme if color_scheme is not None else cur_color_scheme,
        show_legend if show_legend is not None else cur_show_legend,
        number_format if number_format is not None else cur_number_format,
    )

    payload: dict[str, Any] = {
        "viz_type": effective_viz_type,
        "params": json.dumps(new_params),
    }
    if chart_name is not None:
        payload["slice_name"] = chart_name

    resp = sess.put(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", json=payload, timeout=20)
    resp.raise_for_status()
    return {"chart_id": chart_id, **_chart_urls(chart_id)}


@mcp.tool()
def create_dashboard(dashboard_title: str, chart_ids: list[int], confirm_token: str | None = None) -> dict[str, Any]:
    """Creates a Superset dashboard and attaches the given charts to it. Two calls,
    with the user's answer in between - nothing is saved to Superset until they
    confirm.

    Call it WITHOUT confirm_token first: no dashboard is created, and you get back
    a preview of which charts will be attached - and which existing dashboard each
    would be moved off of - plus a confirm_token. Attaching sends
    {"dashboards": [new_id]} per chart, which REPLACES that chart's dashboard
    membership rather than adding to it - a chart already on another dashboard is
    moved off it, not copied. Show this to the user and only pass chart_ids they
    are willing to move. Only after they say yes, call again with the SAME
    arguments plus that confirm_token - a token issued in the current turn is
    refused, so the two calls cannot be chained inside one answer.

    There is no tool to add a chart to an existing dashboard; building a new
    dashboard with the full chart list is the only supported path.
    """
    sess = _superset_session()

    if confirm_token is None:
        charts_preview = []
        for chart_id in chart_ids:
            current = sess.get(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", timeout=15)
            current.raise_for_status()
            result = current.json().get("result", {})
            charts_preview.append(
                {
                    "chart_id": chart_id,
                    "chart_name": result.get("slice_name"),
                    "moved_off": [d.get("dashboard_title") for d in (result.get("dashboards") or [])],
                }
            )
        return {
            "created": False,
            "requires_confirmation": True,
            "preview": {"dashboard_title": dashboard_title, "charts": charts_preview},
            "confirm_token": _issue_create_token(
                "create_dashboard", {"dashboard_title": dashboard_title, "chart_ids": chart_ids}
            ),
            "next_step": (
                "Show the user the dashboard title, the charts that will be attached, "
                "and any dashboard they would be moved off of. Only call "
                "create_dashboard again, with confirm_token, once they answer yes."
            ),
        }

    payload = _consume_create_token(confirm_token, "create_dashboard")
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

    return {
        "created": True,
        "dashboard_id": dashboard_id,
        **_dashboard_urls(dashboard_id),
        "chart_ids": payload["chart_ids"],
    }


# --- Two-phase confirm tokens ------------------------------------------------
#
# A delete or create tool reachable from a free-text chat panel needs the user in
# the loop, not just a `confirm=True` argument the model can set on its own. So
# the first call only previews (and, for create, touches nothing in Superset) and
# hands back a token, and the real action needs that token.
#
# What makes the confirmation real is where the token is allowed to come from.
# This MCP server is a stdio subprocess of one `claude -p` invocation, i.e. one
# chat turn: verified on this build - zero mcp_server processes are alive between
# turns. A token therefore records the nonce of the process that issued it, and
# consuming it from that same nonce is refused. Redeeming a token always means a
# later turn, and a later turn always means the user sent a message in between.
_PROCESS_NONCE = secrets.token_hex(8)
_DELETE_TOKEN_PATH = os.getenv("MCP_DELETE_TOKEN_PATH", "/tmp/mcp_delete_tokens.json")
_DELETE_TOKEN_TTL = float(os.getenv("MCP_DELETE_TOKEN_TTL", "900"))
_CREATE_TOKEN_PATH = os.getenv("MCP_CREATE_TOKEN_PATH", "/tmp/mcp_create_tokens.json")
_CREATE_TOKEN_TTL = float(os.getenv("MCP_CREATE_TOKEN_TTL", "900"))


def _load_tokens(path: str) -> dict[str, dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as handle:
            tokens = json.load(handle)
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {t: rec for t, rec in tokens.items() if rec.get("expires_at", 0) > now}


def _save_tokens(path: str, tokens: dict[str, dict[str, Any]]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(tokens, handle)
    os.replace(tmp, path)


def _issue_delete_token(kind: str, obj_id: int) -> str:
    tokens = _load_tokens(_DELETE_TOKEN_PATH)
    token = secrets.token_hex(8)
    tokens[token] = {
        "kind": kind,
        "id": obj_id,
        "nonce": _PROCESS_NONCE,
        "expires_at": time.time() + _DELETE_TOKEN_TTL,
    }
    _save_tokens(_DELETE_TOKEN_PATH, tokens)
    return token


def _consume_delete_token(token: str, kind: str, obj_id: int) -> None:
    tokens = _load_tokens(_DELETE_TOKEN_PATH)
    record = tokens.get(token)
    if record is None:
        raise ValueError(
            "confirm_token is unknown or has expired. Call this tool again without a "
            "token to get a fresh preview, show it to the user, and wait for their answer."
        )
    if record.get("kind") != kind or record.get("id") != obj_id:
        raise ValueError(
            f"confirm_token was issued for {record.get('kind')} {record.get('id')}, "
            f"not {kind} {obj_id}. Tokens are not interchangeable."
        )
    if record.get("nonce") == _PROCESS_NONCE:
        raise ValueError(
            "This token was issued in the current turn, so the user has not seen the "
            "preview yet. Show them what is about to be deleted, ask them to confirm, "
            "and delete only after they answer."
        )
    tokens.pop(token, None)
    _save_tokens(_DELETE_TOKEN_PATH, tokens)


def _issue_create_token(kind: str, payload: dict[str, Any]) -> str:
    tokens = _load_tokens(_CREATE_TOKEN_PATH)
    token = secrets.token_hex(8)
    tokens[token] = {
        "kind": kind,
        "payload": payload,
        "nonce": _PROCESS_NONCE,
        "expires_at": time.time() + _CREATE_TOKEN_TTL,
    }
    _save_tokens(_CREATE_TOKEN_PATH, tokens)
    return token


def _consume_create_token(token: str, kind: str) -> dict[str, Any]:
    """Returns the exact payload that was previewed, so what gets saved always
    matches what the user was shown - not whatever the model passes on the second
    call.
    """
    tokens = _load_tokens(_CREATE_TOKEN_PATH)
    record = tokens.get(token)
    if record is None:
        raise ValueError(
            "confirm_token is unknown or has expired. Call this tool again without a "
            "token to get a fresh preview, show it to the user, and wait for their answer."
        )
    if record.get("kind") != kind:
        raise ValueError(
            f"confirm_token was issued for {record.get('kind')}, not {kind}. Tokens are not interchangeable."
        )
    if record.get("nonce") == _PROCESS_NONCE:
        raise ValueError(
            "This token was issued in the current turn, so the user has not seen the "
            "preview yet. Show them the preview, ask them to confirm, and save only "
            "after they answer."
        )
    tokens.pop(token, None)
    _save_tokens(_CREATE_TOKEN_PATH, tokens)
    return record["payload"]


@mcp.tool()
def delete_chart(chart_id: int, confirm_token: str | None = None) -> dict[str, Any]:
    """Deletes a chart. Two calls, with the user's answer in between.

    Call it WITHOUT confirm_token first: nothing is deleted, and you get back what
    the chart is plus a confirm_token. Show the user the chart name and the
    dashboards it would disappear from, and ask them to confirm.

    Only after they say yes, call again with that confirm_token. A token issued in
    the current turn is refused, so the two calls cannot be chained inside one
    answer. Deleting is permanent - Superset has no undo for it.
    """
    sess = _superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", {})
    name = result.get("slice_name")
    dashboards = [
        {"id": d.get("id"), "title": d.get("dashboard_title")}
        for d in (result.get("dashboards") or [])
    ]

    if confirm_token is None:
        return {
            "deleted": False,
            "requires_confirmation": True,
            "preview": {
                "chart_id": chart_id,
                "chart_name": name,
                "viz_type": result.get("viz_type"),
                "dataset_id": result.get("datasource_id"),
                "on_dashboards": dashboards,
                **_chart_urls(chart_id),
            },
            "confirm_token": _issue_delete_token("chart", chart_id),
            "next_step": (
                "Show this chart's name and dashboards to the user, ask them to confirm "
                "the deletion, and only call delete_chart again with confirm_token once "
                "they have answered yes."
            ),
        }

    _consume_delete_token(confirm_token, "chart", chart_id)
    delete = sess.delete(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", timeout=20)
    delete.raise_for_status()
    return {"deleted": True, "chart_id": chart_id, "chart_name": name, "was_on_dashboards": dashboards}


@mcp.tool()
def delete_dashboard(dashboard_id: int, confirm_token: str | None = None) -> dict[str, Any]:
    """Deletes a dashboard. Two calls, with the user's answer in between.

    Same protocol as delete_chart: the first call previews and returns a
    confirm_token, the second call deletes. The charts on the dashboard are NOT
    deleted - they stay and simply lose this dashboard from their membership -
    so say that when asking the user, since it is what they usually want to know.
    """
    sess = _superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/dashboard/{dashboard_id}", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", {})
    title = result.get("dashboard_title")

    charts = sess.get(f"{SUPERSET_URL}/api/v1/dashboard/{dashboard_id}/charts", timeout=15)
    chart_names = [c.get("slice_name") for c in charts.json().get("result", [])] if charts.ok else []

    if confirm_token is None:
        return {
            "deleted": False,
            "requires_confirmation": True,
            "preview": {
                "dashboard_id": dashboard_id,
                "dashboard_title": title,
                "charts_on_it": chart_names,
                "charts_are_kept": True,
                **_dashboard_urls(dashboard_id),
            },
            "confirm_token": _issue_delete_token("dashboard", dashboard_id),
            "next_step": (
                "Show the user the dashboard title and the charts on it, tell them the "
                "charts themselves are kept, ask them to confirm, and only then call "
                "delete_dashboard again with confirm_token."
            ),
        }

    _consume_delete_token(confirm_token, "dashboard", dashboard_id)
    delete = sess.delete(f"{SUPERSET_URL}/api/v1/dashboard/{dashboard_id}", timeout=20)
    delete.raise_for_status()
    return {"deleted": True, "dashboard_id": dashboard_id, "dashboard_title": title, "charts_kept": chart_names}


@mcp.tool()
def get_chart(chart_id: int) -> dict[str, Any]:
    """Reads an existing chart's configuration.

    Returns the same shape create_chart/update_chart accept - viz_type, metrics,
    groupby, time_range, row_limit, color_scheme, show_legend, number_format - plus
    the dataset it sits on, so a misconfigured chart can be diagnosed before deciding
    what to change. Call this first when the user says a chart looks wrong, empty, or
    is showing the wrong breakdown; guessing at the current config and passing a full
    argument set to update_chart is how an edit resets fields nobody asked to touch.
    """
    sess = _superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", {})
    params = json.loads(result.get("params") or "{}")
    viz_type, metrics, groupby, time_range, row_limit, color_scheme, show_legend, number_format = (
        _extract_chart_spec(params)
    )
    return {
        "chart_id": chart_id,
        "chart_name": result.get("slice_name"),
        "dataset_id": result.get("datasource_id"),
        "viz_type": viz_type,
        "metrics": metrics,
        "groupby": groupby,
        "time_range": time_range,
        "row_limit": row_limit,
        "color_scheme": color_scheme,
        "show_legend": show_legend,
        "number_format": number_format,
        "dashboards": [
            {"id": d.get("id"), "title": d.get("dashboard_title")}
            for d in (result.get("dashboards") or [])
        ],
        **_chart_urls(chart_id),
    }


@mcp.tool()
def add_charts_to_dashboard(dashboard_id: int, chart_ids: list[int]) -> dict[str, Any]:
    """Adds charts to an EXISTING dashboard, keeping their current memberships.

    Unlike create_dashboard - which sends {"dashboards": [new_id]} and therefore
    moves a chart off whatever dashboard it was on - this reads each chart's
    current dashboard list and appends to it, so a chart can live on several
    dashboards at once. Charts already on the target are left alone and reported
    under `already_present`.
    """
    sess = _superset_session()
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

    return {
        "dashboard_id": dashboard_id,
        "dashboard_title": title,
        "added": added,
        "already_present": already_present,
        **_dashboard_urls(dashboard_id),
    }


if __name__ == "__main__":
    mcp.run()
