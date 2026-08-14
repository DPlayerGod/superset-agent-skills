import json
import os
import re
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
def create_dataset(table_name: str, schema: str = "public") -> dict[str, Any]:
    """Registers a Postgres table as a Superset dataset (get-or-create)."""
    sess = _superset_session()
    database_id = _get_or_create_postgres_database(sess)

    existing = _find_dataset(sess, database_id, table_name, schema)
    if existing:
        return {"dataset_id": existing["id"], "already_existed": True}

    resp = sess.post(
        f"{SUPERSET_URL}/api/v1/dataset/",
        json={"database": database_id, "schema": schema, "table_name": table_name},
        timeout=20,
    )
    resp.raise_for_status()
    return {"dataset_id": resp.json()["id"], "already_existed": False}


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


@mcp.tool()
def create_chart(
    dataset_id: int,
    chart_name: str,
    viz_type: str,
    metrics: list[str],
    groupby: list[str] | None = None,
    time_range: str = "No filter",
    row_limit: int = 1000,
) -> dict[str, Any]:
    """Creates a Superset chart on an existing dataset.

    viz_type: one of "table", "echarts_timeseries_line", "echarts_timeseries_bar",
    "pie", "big_number_total".
    metrics: SQL aggregate expressions, e.g. ["SUM(project_allocated_hc)"].
    Note: "pie" and "big_number_total" only support a single metric - only
    metrics[0] is used. To compare several metrics, use "table" or
    "echarts_timeseries_bar" instead.
    """
    sess = _superset_session()
    params = _build_chart_params(viz_type, metrics, groupby or [], time_range, row_limit)
    resp = sess.post(
        f"{SUPERSET_URL}/api/v1/chart/",
        json={
            "slice_name": chart_name,
            "viz_type": viz_type,
            "datasource_id": dataset_id,
            "datasource_type": "table",
            "params": json.dumps(params),
        },
        timeout=20,
    )
    resp.raise_for_status()
    chart_id = resp.json()["id"]
    return {"chart_id": chart_id, **_chart_urls(chart_id)}


def _extract_chart_spec(params: dict[str, Any]) -> tuple[str, list[str], list[str], str, int]:
    """Reverses _build_chart_params so update_chart can inherit fields the caller omits."""
    viz_type = params.get("viz_type", "table")
    time_range = params.get("time_range", "No filter")
    row_limit = params.get("row_limit", 1000)

    def expr(metric: Any) -> str:
        return metric.get("sqlExpression", "") if isinstance(metric, dict) else str(metric)

    if viz_type in ("pie", "big_number_total"):
        # `or params.get("metrics")` recovers charts written before pie was mapped
        # to the singular `metric`, so a partial update does not blank them out.
        metric = params.get("metric") or next(iter(params.get("metrics") or []), None)
        metrics = [expr(metric)] if metric else []
        groupby: list[str] = [] if viz_type == "big_number_total" else list(params.get("groupby", []))
    elif viz_type in ("echarts_timeseries_line", "echarts_timeseries_bar"):
        x_axis = params.get("x_axis")
        metrics = [expr(m) for m in params.get("metrics", [])]
        groupby = ([x_axis] if x_axis else []) + list(params.get("groupby", []))
    else:
        metrics = [expr(m) for m in params.get("metrics", [])]
        groupby = list(params.get("groupby", []))
    return viz_type, metrics, groupby, time_range, row_limit


@mcp.tool()
def update_chart(
    chart_id: int,
    chart_name: str | None = None,
    viz_type: str | None = None,
    metrics: list[str] | None = None,
    groupby: list[str] | None = None,
    time_range: str | None = None,
    row_limit: int | None = None,
) -> dict[str, Any]:
    """Edits an existing Superset chart in place. Any argument left as None keeps
    its current value; pass only the fields you want to change.

    Same viz_type/metrics semantics as create_chart.
    """
    sess = _superset_session()
    current = sess.get(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", timeout=15)
    current.raise_for_status()
    current_result = current.json()["result"]
    current_params = json.loads(current_result.get("params") or "{}")
    cur_viz, cur_metrics, cur_groupby, cur_time_range, cur_row_limit = _extract_chart_spec(current_params)

    effective_viz_type = viz_type if viz_type is not None else cur_viz
    new_params = _build_chart_params(
        effective_viz_type,
        metrics if metrics is not None else cur_metrics,
        groupby if groupby is not None else cur_groupby,
        time_range if time_range is not None else cur_time_range,
        row_limit if row_limit is not None else cur_row_limit,
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
def create_dashboard(dashboard_title: str, chart_ids: list[int]) -> dict[str, Any]:
    """Creates a Superset dashboard and attaches the given charts to it."""
    sess = _superset_session()
    resp = sess.post(
        f"{SUPERSET_URL}/api/v1/dashboard/",
        json={"dashboard_title": dashboard_title},
        timeout=20,
    )
    resp.raise_for_status()
    dashboard_id = resp.json()["id"]

    for chart_id in chart_ids:
        attach = sess.put(
            f"{SUPERSET_URL}/api/v1/chart/{chart_id}",
            json={"dashboards": [dashboard_id]},
            timeout=20,
        )
        attach.raise_for_status()

    return {"dashboard_id": dashboard_id, **_dashboard_urls(dashboard_id), "chart_ids": chart_ids}


if __name__ == "__main__":
    mcp.run()
