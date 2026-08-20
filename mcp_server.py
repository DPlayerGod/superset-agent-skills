import json
import os
import re
import secrets
import time
import weakref
from typing import Any

import requests
from loguru import logger
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


# --- In-Memory SQL & Schema Caching (Solution 2) ---------------------------
_SQL_CACHE_TTL = float(os.getenv("MCP_SQL_CACHE_TTL", "300"))  # Default 5 minutes (300 seconds)
_sql_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_last_db_check_ts: float = 0
_last_db_changed_on: str | None = None


def _check_db_modified_and_invalidate() -> None:
    global _last_db_check_ts, _last_db_changed_on
    now = time.time()
    if now - _last_db_check_ts < 2.0:
        return
    _last_db_check_ts = now
    try:
        with engine.connect() as conn:
            query = text("""
                SELECT GREATEST(
                    COALESCE((SELECT MAX(changed_on) FROM slices), '1970-01-01'::timestamp),
                    COALESCE((SELECT MAX(changed_on) FROM dashboards), '1970-01-01'::timestamp)
                )
            """)
            val = conn.execute(query).scalar()
            current_str = str(val) if val else None
            if _last_db_changed_on is not None and current_str != _last_db_changed_on:
                logger.info("[AUTO INVALIDATE CACHE] Manual edit detected on Superset UI (changed_on: {})", current_str)
                _sql_cache.clear()
            _last_db_changed_on = current_str
    except Exception:
        pass


def _get_sql_cache(key: str) -> dict[str, Any] | None:
    _check_db_modified_and_invalidate()
    now = time.time()
    if key in _sql_cache:
        ts, cached_data = _sql_cache[key]
        if now - ts < _SQL_CACHE_TTL:
            res = dict(cached_data)
            res["cached"] = True
            logger.info("[RAM CACHE HIT] key='{}'", key[:120])
            return res
        else:
            _sql_cache.pop(key, None)
    return None


def _set_sql_cache(key: str, data: dict[str, Any]) -> None:
    if len(_sql_cache) > 500:
        _sql_cache.clear()
    _sql_cache[key] = (time.time(), data)


def _invalidate_sql_cache(*keys: str) -> None:
    """Drops specific cached reads a write tool just made stale.

    Without this, e.g. update_chart could save a change and then get_chart on the
    same chart_id would still return the pre-update config for up to
    _SQL_CACHE_TTL seconds - the assistant reporting its own edit as unchanged.
    """
    for key in keys:
        _sql_cache.pop(key, None)


@mcp.tool()
def list_datasets(schema: str = "public") -> dict[str, Any]:
    """Lists the physical tables in a Postgres schema (defaults to "public")."""
    cache_key = f"list_datasets_{schema}"
    cached = _get_sql_cache(cache_key)
    if cached is not None:
        return cached

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

    res = {
        "schema": schema,
        "count": len(rows),
        "tables": [
            {"table_schema": r[0], "table_name": r[1]}
            for r in rows
        ],
        "cached": False,
    }
    _set_sql_cache(cache_key, res)
    return res


@mcp.tool()
def describe_table(table_name: str, schema: str = "public") -> dict[str, Any]:
    """Returns a table's columns with their Postgres data type and nullability."""
    cache_key = f"describe_table_{schema}_{table_name}"
    cached = _get_sql_cache(cache_key)
    if cached is not None:
        return cached

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

    res = {
        "schema": schema,
        "table": table_name,
        "columns": [
            {"name": r[0], "type": r[1], "nullable": r[2]}
            for r in rows
        ],
        "cached": False,
    }
    _set_sql_cache(cache_key, res)
    return res


@mcp.tool()
def execute_sql(sql: str | None = None, query: str | None = None, row_limit: int = 200) -> dict[str, Any]:
    """Runs read-only SQL against Postgres and returns the rows."""
    actual_sql = sql or query
    if not actual_sql:
        raise ValueError("Either 'sql' or 'query' parameter is required")

    if not _is_readonly_sql(actual_sql):
        raise ValueError("Only SELECT/CTE read-only SQL is allowed")

    safe_sql = _enforce_limit(actual_sql, row_limit)
    normalized_sql = re.sub(r"\s+", " ", safe_sql.strip().lower())
    cache_key = f"sql_{normalized_sql}_{row_limit}"
    cached = _get_sql_cache(cache_key)
    if cached is not None:
        logger.info("[RAM CACHE HIT] sql='{}'", normalized_sql[:100])
        return cached

    logger.info("[FRESH DB QUERY] sql='{}'", normalized_sql[:100])
    with engine.connect() as conn:
        result = conn.execute(text(safe_sql))
        rows = result.fetchall()
        columns = list(result.keys())

    res = {
        "sql": safe_sql,
        "row_count": len(rows),
        "columns": columns,
        "rows": _rows_to_json(rows, columns),
        "cached": False,
    }
    _set_sql_cache(cache_key, res)
    return res


@mcp.tool()
def run_sql_readonly(sql: str | None = None, query: str | None = None, row_limit: int = 200) -> dict[str, Any]:
    """Execute read-only SELECT/CTE SQL statement on database (alias for execute_sql)."""
    return execute_sql(sql=sql, query=query, row_limit=row_limit)


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

    The SQL is held to the same SELECT/CTE-only rule as execute_sql. Re-calling
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
        _invalidate_sql_cache(f"dataset_info_{existing['id']}")
        return {"dataset_id": existing["id"], "already_existed": True, "sql_updated": True}

    payload: dict[str, Any] = {"database": database_id, "schema": schema, "table_name": table_name}
    if sql is not None:
        payload["sql"] = sql
    resp = sess.post(f"{SUPERSET_URL}/api/v1/dataset/", json=payload, timeout=30)
    resp.raise_for_status()
    _invalidate_sql_cache(f"list_datasets_{schema}")
    return {"dataset_id": resp.json()["id"], "already_existed": False, "virtual": sql is not None}


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


def _adhoc_metric(expression: str) -> dict[str, Any]:
    if not _AGGREGATE_RE.search(expression):
        raise ValueError(
            f"metric {expression!r} has no aggregate function, so the chart query would "
            f"select it next to the groupby columns without grouping by it and the "
            f"database would reject it. Wrap it in one, e.g. \"SUM({expression.strip()})\". "
            f"This holds even when the dataset SQL already aggregated that column: the "
            f"chart re-groups by its own dimensions, so the metric must aggregate again."
        )
    return {
        "expressionType": "SQL",
        "sqlExpression": expression,
        "label": expression,
    }


# The one metric Superset auto-creates on every dataset. Kept as a bare string -
# how the API refers to a saved metric - instead of being rejected as a missing
# aggregate, so update_chart can still edit charts built in the Superset UI.
_SAVED_METRICS = {"count"}

_COLOR_MAP = {
    "do": "#E74C3C",
    "đỏ": "#E74C3C",
    "red": "#E74C3C",
    "xanh": "#1890FF",
    "xanh duong": "#1890FF",
    "xanh dương": "#1890FF",
    "xanh bien": "#1890FF",
    "xanh biển": "#1890FF",
    "blue": "#1890FF",
    "xanh la": "#52C41A",
    "xanh lá": "#52C41A",
    "xanh luc": "#52C41A",
    "xanh lục": "#52C41A",
    "green": "#52C41A",
    "cam": "#FA8C16",
    "orange": "#FA8C16",
    "tim": "#722ED1",
    "tím": "#722ED1",
    "purple": "#722ED1",
    "vang": "#FADB14",
    "vàng": "#FADB14",
    "yellow": "#FADB14",
    "hong": "#EB2F96",
    "hồng": "#EB2F96",
    "pink": "#EB2F96",
    "xam": "#8C8C8C",
    "xám": "#8C8C8C",
    "gray": "#8C8C8C",
    "grey": "#8C8C8C",
    "den": "#262626",
    "đen": "#262626",
    "black": "#262626",
    "trang": "#FFFFFF",
    "trắng": "#FFFFFF",
    "white": "#FFFFFF",
    "teal": "#13C2C2",
    "cyan": "#13C2C2",
}


_COLOR_SCHEME_MAP = {
    "do": "redScheme",
    "đỏ": "redScheme",
    "red": "redScheme",
    "xanh": "blueScheme",
    "xanh duong": "blueScheme",
    "xanh dương": "blueScheme",
    "xanh bien": "blueScheme",
    "xanh biển": "blueScheme",
    "blue": "blueScheme",
    "xanh la": "greenScheme",
    "xanh lá": "greenScheme",
    "xanh luc": "greenScheme",
    "xanh lục": "greenScheme",
    "green": "greenScheme",
    "cam": "orangeScheme",
    "orange": "orangeScheme",
    "tim": "purpleScheme",
    "tím": "purpleScheme",
    "purple": "purpleScheme",
    "vang": "yellowScheme",
    "vàng": "yellowScheme",
    "yellow": "yellowScheme",
}


def _resolve_color_scheme(color_name: str | None) -> str | None:
    if not color_name:
        return None
    raw = color_name.strip().lower()
    return _COLOR_SCHEME_MAP.get(raw)


def _hex_to_rgba(hex_color: str, alpha: float = 1.0) -> dict[str, Any]:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    if len(hex_color) == 6:
        r, g, b = tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
        return {"r": r, "g": g, "b": b, "a": alpha}
    return {"r": 24, "g": 144, "b": 255, "a": alpha}


def _resolve_color(color_name_or_hex: str | None) -> str | None:
    if not color_name_or_hex:
        return None
    raw = color_name_or_hex.strip().lower()
    if raw in _COLOR_MAP:
        return _COLOR_MAP[raw]
    if re.match(r"^#(?:[0-9a-fA-F]{3}){1,2}$", raw):
        return raw.upper()
    return None


def _metric_param(expression: str) -> dict[str, Any] | str:
    if expression.strip().lower() in _SAVED_METRICS:
        return expression.strip().lower()
    return _adhoc_metric(expression)


def _build_chart_params(
    viz_type: str,
    metrics: list[str],
    groupby: list[str],
    time_range: str,
    row_limit: int,
    color_scheme: str | None = None,
    show_legend: bool | None = None,
    number_format: str | None = None,
    x_axis_sort: str | None = None,
    x_axis_sort_asc: bool | None = None,
    order_desc: bool | None = None,
    orientation: str | None = None,
    color: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    adhoc_metrics = [_metric_param(metric) for metric in metrics]
    params: dict[str, Any] = {
        "viz_type": viz_type,
        "metrics": adhoc_metrics,
        "groupby": list(groupby),
        "adhoc_filters": [],
        "row_limit": row_limit,
        "time_range": time_range,
    }
    if description is not None:
        params["description"] = description

    effective_scheme = color_scheme or _resolve_color_scheme(color)
    if effective_scheme:
        params["color_scheme"] = effective_scheme

    resolved_color = _resolve_color(color)
    if resolved_color:
        params["color_picker"] = _hex_to_rgba(resolved_color)
        label_colors: dict[str, str] = {}
        for m in adhoc_metrics:
            if isinstance(m, dict) and "label" in m:
                label_colors[m["label"]] = resolved_color
            elif isinstance(m, str):
                label_colors[m] = resolved_color
        for raw_m in metrics:
            label_colors[raw_m] = resolved_color
        if label_colors:
            params["label_colors"] = label_colors

    if viz_type in ("echarts_timeseries_line", "echarts_timeseries_bar"):
        params["x_axis"] = groupby[0] if groupby else None
        params["groupby"] = list(groupby[1:])
        if orientation is not None:
            params["orientation"] = orientation
        if order_desc is not None:
            params["order_desc"] = order_desc
        if x_axis_sort is not None:
            params["x_axis_sort"] = x_axis_sort
        if x_axis_sort_asc is not None:
            params["x_axis_sort_asc"] = x_axis_sort_asc
            if order_desc is None:
                params["order_desc"] = not x_axis_sort_asc
        elif order_desc is not None:
            params["x_axis_sort_asc"] = not order_desc
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


def _parse_id(val: int | str) -> int:
    if isinstance(val, int):
        return val
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return 1


def _chart_urls(chart_id: int | str) -> dict[str, Any]:
    c_id = _parse_id(chart_id)
    base = f"{SUPERSET_PUBLIC_URL}/explore/?slice_id={c_id}"
    return {"type": "chart", "url": base, "embed_url": f"{base}&standalone={_EXPLORE_STANDALONE}"}


def _dashboard_urls(dashboard_id: int | str) -> dict[str, Any]:
    d_id = _parse_id(dashboard_id)
    base = f"{SUPERSET_PUBLIC_URL}/superset/dashboard/{d_id}/"
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
    metrics: SQL aggregate expressions, e.g. ["SUM(project_allocated_hc)"]. Always
    an aggregate - a bare column name is rejected, because the chart query selects
    the metric alongside the groupby columns and the database then demands it be
    aggregated or grouped. This holds for a virtual dataset whose own SQL already
    aggregated the column too: the chart re-groups by its own dimensions, so pass
    "SUM(monthly_fte)", not "monthly_fte".
    Note: "pie" and "big_number_total" only support a single metric - only
    metrics[0] is used. To compare several metrics, use "table" or
    "echarts_timeseries_bar" instead.
    groupby: for "echarts_timeseries_line"/"echarts_timeseries_bar", groupby[0]
    becomes the x-axis and the remaining entries become the series split, so the
    order carries meaning: for "FTE per project per month", pass
    ["working_month_year", "project_name"]. Reversing it plots projects on the
    x-axis and silently drops the monthly breakdown the question asked for.

    Sorting & orientation (for echarts_timeseries_bar):
    x_axis_sort: metric or column to sort x-axis categories by (e.g. "SUM(project_allocated_hc)").
    x_axis_sort_asc: True for ascending sort, False for descending.
    order_desc: True for descending sort, False for ascending.
    orientation: "vertical" (default column chart) or "horizontal" (horizontal bar chart).

    Colors & Subtitle:
    color: Natural color name ("đỏ", "xanh dương", "xanh lá", "cam", "tím", "vàng", "hồng", "red", "blue", "green") or hex "#E74C3C".
    description: Subtitle / description / explanation markdown for the chart.

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
    dataset_id = _parse_id(dataset_id)
    params = _build_chart_params(
        viz_type,
        metrics,
        groupby or [],
        time_range,
        row_limit,
        color_scheme,
        show_legend,
        number_format,
        x_axis_sort,
        x_axis_sort_asc,
        order_desc,
        orientation,
        color,
        description,
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
                    "x_axis_sort": x_axis_sort,
                    "x_axis_sort_asc": x_axis_sort_asc,
                    "order_desc": order_desc,
                    "orientation": orientation,
                    "color": color,
                    "description": description,
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
        payload.get("x_axis_sort"),
        payload.get("x_axis_sort_asc"),
        payload.get("order_desc"),
        payload.get("orientation"),
        payload.get("color"),
        payload.get("description"),
    )
    chart_payload: dict[str, Any] = {
        "slice_name": payload["chart_name"],
        "viz_type": payload["viz_type"],
        "datasource_id": payload["dataset_id"],
        "datasource_type": "table",
        "params": json.dumps(saved_params),
    }
    if payload.get("description"):
        chart_payload["description"] = payload["description"]

    resp = sess.post(
        f"{SUPERSET_URL}/api/v1/chart/",
        json=chart_payload,
        timeout=20,
    )
    resp.raise_for_status()
    chart_id = resp.json()["id"]
    _invalidate_sql_cache("list_charts")
    return {"created": True, "chart_id": chart_id, **_chart_urls(chart_id)}


def _extract_chart_spec(
    params: dict[str, Any],
) -> tuple[
    str,
    list[str],
    list[str],
    str,
    int,
    str | None,
    bool | None,
    str | None,
    str | None,
    bool | None,
    bool | None,
    str | None,
    str | None,
    str | None,
]:
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
    x_axis_sort = params.get("x_axis_sort")
    x_axis_sort_asc = params.get("x_axis_sort_asc")
    order_desc = params.get("order_desc")
    orientation = params.get("orientation")
    description = params.get("description")
    
    # Try extracting color from color_picker or label_colors
    color = None
    if isinstance(params.get("label_colors"), dict) and params["label_colors"]:
        color = next(iter(params["label_colors"].values()), None)

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
    return (
        viz_type,
        metrics,
        groupby,
        time_range,
        row_limit,
        color_scheme,
        show_legend,
        number_format,
        x_axis_sort,
        x_axis_sort_asc,
        order_desc,
        orientation,
        color,
        description,
    )


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
) -> dict[str, Any]:
    """Edits an existing Superset chart in place. Any argument left as None keeps
    its current value; pass only the fields you want to change.

    Same viz_type/metrics semantics as create_chart, including sorting (x_axis_sort,
    x_axis_sort_asc, order_desc), orientation ("vertical"/"horizontal"), color ("đỏ", "xanh",
    "cam", "tím", "vàng", "#HEX"), subtitle (description), and style controls.
    """
    chart_id = _parse_id(chart_id)
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
        cur_x_axis_sort,
        cur_x_axis_sort_asc,
        cur_order_desc,
        cur_orientation,
        cur_color,
        cur_description,
    ) = _extract_chart_spec(current_params)

    effective_viz_type = viz_type if viz_type is not None else cur_viz
    target_color_scheme = color_scheme
    if target_color_scheme is None:
        if color is not None:
            target_color_scheme = _resolve_color_scheme(color)
        if target_color_scheme is None:
            target_color_scheme = cur_color_scheme

    new_params = _build_chart_params(
        effective_viz_type,
        metrics if metrics is not None else cur_metrics,
        groupby if groupby is not None else cur_groupby,
        time_range if time_range is not None else cur_time_range,
        row_limit if row_limit is not None else cur_row_limit,
        target_color_scheme,
        show_legend if show_legend is not None else cur_show_legend,
        number_format if number_format is not None else cur_number_format,
        x_axis_sort if x_axis_sort is not None else cur_x_axis_sort,
        x_axis_sort_asc if x_axis_sort_asc is not None else cur_x_axis_sort_asc,
        order_desc if order_desc is not None else cur_order_desc,
        orientation if orientation is not None else cur_orientation,
        color if color is not None else cur_color,
        description if description is not None else cur_description,
    )

    payload: dict[str, Any] = {
        "viz_type": effective_viz_type,
        "params": json.dumps(new_params),
    }
    if chart_name is not None:
        payload["slice_name"] = chart_name
    if description is not None:
        payload["description"] = description

    resp = sess.put(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", json=payload, timeout=20)
    resp.raise_for_status()

    resolved_color = _resolve_color(color)
    if resolved_color:
        dashboards = current_result.get("dashboards") or []
        for d in dashboards:
            dash_id = d.get("id")
            if not dash_id:
                continue
            try:
                dash_resp = sess.get(f"{SUPERSET_URL}/api/v1/dashboard/{dash_id}", timeout=15)
                if dash_resp.ok:
                    dash_res = dash_resp.json().get("result", {})
                    meta = json.loads(dash_res.get("json_metadata") or "{}")
                    label_cols = meta.setdefault("label_colors", {})
                    map_cols = meta.setdefault("map_label_colors", {})
                    target_metrics = metrics if metrics is not None else cur_metrics
                    for m in target_metrics:
                        label_cols[m] = resolved_color
                        map_cols[m] = resolved_color
                    meta["label_colors"] = label_cols
                    meta["map_label_colors"] = map_cols
                    sess.put(
                        f"{SUPERSET_URL}/api/v1/dashboard/{dash_id}",
                        json={"json_metadata": json.dumps(meta)},
                        timeout=20,
                    )
                    _invalidate_sql_cache(f"dashboard_info_{dash_id}")
            except Exception as e:
                logger.warning("Failed to sync dashboard label_colors: {}", e)

    _invalidate_sql_cache(f"get_chart_{chart_id}", f"chart_sql_{chart_id}", "list_charts")
    return {"chart_id": chart_id, **_chart_urls(chart_id)}


@mcp.tool()
def create_dashboard(dashboard_title: str, chart_ids: list[int | str], confirm_token: str | None = None) -> dict[str, Any]:
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

    To put a chart on a dashboard that already exists, use add_charts_to_dashboard
    instead: it keeps the chart's current memberships.
    """
    chart_ids = [_parse_id(c) for c in chart_ids]
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
        _invalidate_sql_cache(f"get_chart_{chart_id}")

    _invalidate_sql_cache("list_dashboards")
    return {
        "created": True,
        "dashboard_id": dashboard_id,
        **_dashboard_urls(dashboard_id),
        "chart_ids": payload["chart_ids"],
    }


# --- Two-phase confirm tokens ------------------------------------------------
_PROCESS_NONCE = secrets.token_hex(8)

# What makes the two-phase confirmation real is that a token cannot be redeemed in the
# turn that issued it: redeeming always means a later turn, and a later turn always
# means the user sent a message in between. That check needs something that identifies
# "this turn". It used to be _PROCESS_NONCE, which worked only because this server was
# a stdio subprocess of a single `claude -p` - one process was one turn. The server is
# now shared by every turn (see _start_mcp_sidecar in claude_gateway/gateway_server.py),
# so the process nonce is now constant and would refuse *every* confirmation forever.
# Each `claude -p` still opens its own MCP session, so the session takes over the role
# the process used to play - and it does so under stdio too, where one session is still
# one process is still one turn.
_session_nonces: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _turn_nonce() -> str:
    try:
        session = mcp.get_context().session
    except Exception:
        return _PROCESS_NONCE
    try:
        return _session_nonces.setdefault(session, secrets.token_hex(8))
    except TypeError:
        # Not weak-referenceable: fall back rather than lose the guard entirely.
        return _PROCESS_NONCE
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


def _issue_create_token(kind: str, payload: dict[str, Any]) -> str:
    tokens = _load_tokens(_CREATE_TOKEN_PATH)
    token = secrets.token_hex(8)
    tokens[token] = {
        "kind": kind,
        "payload": payload,
        "nonce": _turn_nonce(),
        "expires_at": time.time() + _CREATE_TOKEN_TTL,
    }
    _save_tokens(_CREATE_TOKEN_PATH, tokens)
    return token


def _consume_create_token(token: str, kind: str) -> dict[str, Any]:
    tokens = _load_tokens(_CREATE_TOKEN_PATH)
    record = tokens.get(token)
    if not record:
        raise ValueError("Invalid or expired confirm_token. Start over without a token.")
    if record["kind"] != kind:
        raise ValueError(f"Token is for {record['kind']}, not {kind}.")
    if record["nonce"] == _turn_nonce():
        raise ValueError(
            "confirm_token was issued in THIS turn. You must show the preview to the "
            "user and ask them to confirm; only call again once they say yes in a "
            "later turn."
        )
    tokens.pop(token, None)
    _save_tokens(_CREATE_TOKEN_PATH, tokens)
    return record["payload"]


@mcp.tool()
def get_chart(chart_id: int | str) -> dict[str, Any]:
    """Reads an existing chart's configuration."""
    chart_id = _parse_id(chart_id)
    cache_key = f"get_chart_{chart_id}"
    cached = _get_sql_cache(cache_key)
    if cached is not None:
        return cached

    sess = _superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", {})
    params = json.loads(result.get("params") or "{}")
    (
        viz_type,
        metrics,
        groupby,
        time_range,
        row_limit,
        color_scheme,
        show_legend,
        number_format,
        x_axis_sort,
        x_axis_sort_asc,
        order_desc,
        orientation,
        color,
        description,
    ) = _extract_chart_spec(params)
    res = {
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
        "x_axis_sort": x_axis_sort,
        "x_axis_sort_asc": x_axis_sort_asc,
        "order_desc": order_desc,
        "orientation": orientation,
        "color": color,
        "description": result.get("description") or description,
        "dashboards": [
            {"id": d.get("id"), "title": d.get("dashboard_title")}
            for d in (result.get("dashboards") or [])
        ],
        "cached": False,
        **_chart_urls(chart_id),
    }
    _set_sql_cache(cache_key, res)
    return res


@mcp.tool()
def add_charts_to_dashboard(dashboard_id: int | str, chart_ids: list[int | str]) -> dict[str, Any]:
    """Adds charts to an EXISTING dashboard, keeping their current memberships."""
    _sql_cache.clear()
    dashboard_id = _parse_id(dashboard_id)
    chart_ids = [_parse_id(c) for c in chart_ids]
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


@mcp.tool()
def health_check() -> dict[str, Any]:
    """Returns the health status of the database and Superset connections."""
    status = "ok"
    details = {}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        details["database"] = "ok"
    except Exception as e:
        status = "error"
        details["database"] = f"failed: {str(e)}"

    try:
        sess = _superset_session()
        resp = sess.get(f"{SUPERSET_URL}/api/v1/security/csrf_token/", timeout=10)
        resp.raise_for_status()
        details["superset"] = "ok"
    except Exception as e:
        status = "error"
        details["superset"] = f"failed: {str(e)}"

    return {"status": status, "details": details}


@mcp.tool()
def get_instance_info() -> dict[str, Any]:
    """Returns metadata about the Superset instance (domain, version, etc.)."""
    return {
        "superset_url": SUPERSET_PUBLIC_URL,
        "database_backend": "postgresql",
        "description": "Superset instance serving postgres analytics workspace.",
    }


@mcp.tool()
def list_charts() -> dict[str, Any]:
    """Lists all charts currently available in Apache Superset."""
    cache_key = "list_charts"
    cached = _get_sql_cache(cache_key)
    if cached is not None:
        return cached

    sess = _superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/chart/?q=(page_size:1000)", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", [])
    res = {
        "count": len(result),
        "charts": [
            {
                "id": c.get("id"),
                "slice_name": c.get("slice_name"),
                "viz_type": c.get("viz_type"),
                "datasource_id": c.get("datasource_id"),
                "datasource_name": c.get("datasource_name_title"),
            }
            for c in result
        ],
        "cached": False,
    }
    _set_sql_cache(cache_key, res)
    return res


@mcp.tool()
def list_dashboards() -> dict[str, Any]:
    """Lists all dashboards currently available in Apache Superset."""
    cache_key = "list_dashboards"
    cached = _get_sql_cache(cache_key)
    if cached is not None:
        return cached

    sess = _superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/dashboard/?q=(page_size:1000)", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", [])
    res = {
        "count": len(result),
        "dashboards": [
            {
                "id": d.get("id"),
                "dashboard_title": d.get("dashboard_title"),
                "slug": d.get("slug"),
                "published": d.get("published"),
            }
            for d in result
        ],
        "cached": False,
    }
    _set_sql_cache(cache_key, res)
    return res


@mcp.tool()
def list_databases() -> dict[str, Any]:
    """Lists configured databases in Apache Superset."""
    cache_key = "list_databases"
    cached = _get_sql_cache(cache_key)
    if cached is not None:
        return cached

    sess = _superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/database/?q=(page_size:100)", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", [])
    res = {
        "count": len(result),
        "databases": [
            {
                "id": d.get("id"),
                "database_name": d.get("database_name"),
                "backend": d.get("backend"),
            }
            for d in result
        ],
        "cached": False,
    }
    _set_sql_cache(cache_key, res)
    return res


@mcp.tool()
def get_dashboard_info(dashboard_id: int | str) -> dict[str, Any]:
    """Gets complete details for a specific dashboard by its ID (integer ID or UUID string).

    Returns dashboard title, slug, published status, total charts_count, and full
    metadata for ALL attached charts (including viz_type, metrics, groupby, time_range,
    row_limit, and pre-generated SQL queries). Do NOT call get_chart or get_chart_sql
    after calling get_dashboard_info, because all chart specs and SQL queries are already
    included in the 'charts' array returned by this tool.
    """
    cache_key = f"dashboard_info_{dashboard_id}"
    cached = _get_sql_cache(cache_key)
    if cached is not None:
        return cached

    target_id: int | None = None

    if isinstance(dashboard_id, int):
        target_id = dashboard_id
    elif isinstance(dashboard_id, str):
        if dashboard_id.isdigit():
            target_id = int(dashboard_id)
        else:
            try:
                with engine.connect() as conn:
                    row = conn.execute(
                        text("""
                            SELECT d.id FROM dashboards d
                            LEFT JOIN embedded_dashboards ed ON ed.dashboard_id = d.id
                            WHERE CAST(d.uuid AS VARCHAR) = :val OR ed.uuid = :val OR d.slug = :val
                            LIMIT 1
                        """),
                        {"val": str(dashboard_id)},
                    ).fetchone()
                    if row:
                        target_id = row[0]
            except Exception:
                pass

    if target_id is None:
        try:
            with engine.connect() as conn:
                row = conn.execute(text("SELECT id FROM dashboards ORDER BY id ASC LIMIT 1")).fetchone()
                if row:
                    target_id = row[0]
        except Exception:
            target_id = 1

    sess = _superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/dashboard/{target_id}", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", {})

    charts_list: list[dict[str, Any]] = []
    try:
        with engine.connect() as conn:
            query = text("""
                SELECT c.id, c.slice_name, c.viz_type, c.datasource_id, c.params
                FROM slices c
                JOIN dashboard_slices ds ON c.id = ds.slice_id
                WHERE ds.dashboard_id = :db_id
                ORDER BY c.id ASC
            """)
            rows = conn.execute(query, {"db_id": target_id}).mappings().all()
            for r in rows:
                params = json.loads(r["params"] or "{}") if r.get("params") else {}
                viz_type, metrics, groupby, time_range, row_limit, *style_and_sort = _extract_chart_spec(params)
                
                # Fetch/generate SQL for chart
                chart_sql = None
                sql_cache_key = f"chart_sql_{r['id']}"
                cached_sql = _get_sql_cache(sql_cache_key)
                if cached_sql and isinstance(cached_sql.get("sql"), str):
                    chart_sql = cached_sql["sql"]
                else:
                    try:
                        payload = {
                            "datasource": {"id": r["datasource_id"], "type": "table"},
                            "queries": [params],
                            "result_type": "query",
                        }
                        sql_resp = sess.post(f"{SUPERSET_URL}/api/v1/chart/data", json=payload, timeout=10)
                        if sql_resp.ok:
                            queries = sql_resp.json().get("result", [])
                            if queries and isinstance(queries, list) and len(queries) > 0:
                                chart_sql = queries[0].get("query")
                    except Exception:
                        pass
                    if not chart_sql and r.get("datasource_id"):
                        try:
                            ds_resp = sess.get(f"{SUPERSET_URL}/api/v1/dataset/{r['datasource_id']}", timeout=10)
                            if ds_resp.ok:
                                ds = ds_resp.json().get("result", {})
                                chart_sql = ds.get("sql") or f"SELECT * FROM {ds.get('table_name')}"
                        except Exception:
                            pass
                    chart_sql = chart_sql or "Query unavailable"
                    _set_sql_cache(sql_cache_key, {"chart_id": r["id"], "sql": chart_sql, "cached": False})

                charts_list.append({
                    "id": r["id"],
                    "slice_name": r["slice_name"],
                    "viz_type": r["viz_type"],
                    "datasource_id": r["datasource_id"],
                    "metrics": metrics,
                    "groupby": groupby,
                    "time_range": time_range,
                    "row_limit": row_limit,
                    "sql": chart_sql,
                })
    except Exception:
        pass

    res = {
        "id": result.get("id"),
        "dashboard_title": result.get("dashboard_title"),
        "slug": result.get("slug"),
        "published": result.get("published"),
        "charts_count": len(charts_list),
        "charts": charts_list,
        "position_json": result.get("position_json"),
        "css": result.get("css"),
        "cached": False,
    }
    _set_sql_cache(cache_key, res)
    if target_id and str(target_id) != str(dashboard_id):
        _set_sql_cache(f"dashboard_info_{target_id}", res)
    return res


@mcp.tool()
def get_dataset_info(dataset_id: int | str) -> dict[str, Any]:
    """Gets details for a specific dataset by its ID."""
    dataset_id = _parse_id(dataset_id)
    cache_key = f"dataset_info_{dataset_id}"
    cached = _get_sql_cache(cache_key)
    if cached is not None:
        return cached

    sess = _superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/dataset/{dataset_id}", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", {})
    res = {
        "id": result.get("id"),
        "table_name": result.get("table_name"),
        "schema": result.get("schema"),
        "sql": result.get("sql"),
        "columns": [
            {"column_name": c.get("column_name"), "type": c.get("type")}
            for c in (result.get("columns") or [])
        ],
        "cached": False,
    }
    _set_sql_cache(cache_key, res)
    return res


@mcp.tool()
def get_database_info(database_id: int | str) -> dict[str, Any]:
    """Gets details for a specific database by its ID."""
    database_id = _parse_id(database_id)
    cache_key = f"database_info_{database_id}"
    cached = _get_sql_cache(cache_key)
    if cached is not None:
        return cached

    sess = _superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/database/{database_id}", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", {})
    res = {
        "id": result.get("id"),
        "database_name": result.get("database_name"),
        "backend": result.get("backend"),
        "expose_in_sqllab": result.get("expose_in_sqllab"),
        "cached": False,
    }
    _set_sql_cache(cache_key, res)
    return res


@mcp.tool()
def get_chart_preview(chart_id: int | str) -> dict[str, Any]:
    """Returns embed/preview URL metadata for a specific chart."""
    chart_id = _parse_id(chart_id)
    return _chart_urls(chart_id)


@mcp.tool()
def get_chart_sql(chart_id: int | str) -> dict[str, Any]:
    """Retrieves the generated SQL query for a specific chart."""
    chart_id = _parse_id(chart_id)
    cache_key = f"chart_sql_{chart_id}"
    cached = _get_sql_cache(cache_key)
    if cached is not None:
        return cached

    sess = _superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/chart/{chart_id}", timeout=15)
    resp.raise_for_status()
    chart = resp.json().get("result", {})
    datasource_id = chart.get("datasource_id")
    params = json.loads(chart.get("params") or "{}")

    sql_query = None
    try:
        payload = {
            "datasource": {"id": datasource_id, "type": "table"},
            "queries": [params],
            "result_type": "query",
        }
        sql_resp = sess.post(f"{SUPERSET_URL}/api/v1/chart/data", json=payload, timeout=20)
        if sql_resp.ok:
            queries = sql_resp.json().get("result", [])
            if queries and isinstance(queries, list) and len(queries) > 0:
                sql_query = queries[0].get("query")
    except Exception:
        pass

    if not sql_query and datasource_id:
        try:
            ds_resp = sess.get(f"{SUPERSET_URL}/api/v1/dataset/{datasource_id}", timeout=15)
            if ds_resp.ok:
                ds = ds_resp.json().get("result", {})
                sql_query = ds.get("sql") or f"SELECT * FROM {ds.get('table_name')}"
        except Exception:
            pass

    res = {"chart_id": chart_id, "sql": sql_query or "Query unavailable for this chart type", "cached": False}
    _set_sql_cache(cache_key, res)
    return res


@mcp.tool()
def generate_explore_link(dataset_id: int | str) -> dict[str, Any]:
    """Generates the Explore URL for a specific dataset."""
    dataset_id = _parse_id(dataset_id)
    return {
        "dataset_id": dataset_id,
        "explore_url": f"{SUPERSET_PUBLIC_URL}/explore/?dataset_id={dataset_id}&dataset_type=physical",
    }


@mcp.tool()
def open_sql_lab_with_context(sql: str) -> dict[str, Any]:
    """Generates a URL to open SQL Lab with the specified query text preloaded."""
    import urllib.parse
    encoded_sql = urllib.parse.quote(sql)
    return {
        "sql": sql,
        "sql_lab_url": f"{SUPERSET_PUBLIC_URL}/sqllab/?sql={encoded_sql}",
    }


if __name__ == "__main__":
    # stdio (the default) is what the Claude Code CLI uses when it spawns this file
    # itself - and it spawns a fresh copy for every single turn, so python start-up,
    # the SQLAlchemy import and the Superset login are all paid again on each
    # question. "http" instead serves one long-lived process that the gateway starts
    # once at boot and every turn then shares, leaving a turn only the HTTP
    # handshake to pay. See _start_mcp_sidecar in claude_gateway/gateway_server.py.
    if os.getenv("MCP_TRANSPORT", "stdio").lower() in ("http", "streamable-http"):
        mcp.settings.host = os.getenv("MCP_HTTP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.getenv("MCP_HTTP_PORT", "8765"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
