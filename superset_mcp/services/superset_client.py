"""Authenticated HTTP access to the Superset REST API.

All write operations authenticate to Superset as a fixed service account
(SUPERSET_ADMIN_USERNAME/PASSWORD, defaulting to the admin/admin account
bootstrapped by the superset container's entrypoint). They are NOT scoped to
whichever Superset user is chatting - every chart/dashboard created via these
tools is owned by that fixed account. This is an accepted V1 tradeoff, not
per-user identity forwarding.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from superset_mcp.config import (
    DATABASE_URL,
    SUPERSET_ADMIN_PASSWORD,
    SUPERSET_ADMIN_USERNAME,
    SUPERSET_URL,
)

_session_cache: dict[str, Any] = {"session": None, "expires_at": 0.0}


def superset_session() -> requests.Session:
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


def rison_filter(column: str, value: str) -> str:
    escaped = value.replace("'", "''")
    return f"(filters:!((col:{column},opr:eq,value:'{escaped}')))"


def get_or_create_postgres_database(sess: requests.Session) -> int:
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


def list_all_superset_datasets(sess: requests.Session) -> list[dict[str, Any]]:
    """Lists all active datasets registered across all databases in Superset."""
    try:
        resp = sess.get(
            f"{SUPERSET_URL}/api/v1/dataset/",
            params={"q": "(page_size:500)"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("result", [])
    except Exception:
        return []


def get_superset_dataset_by_name(sess: requests.Session, table_name: str) -> dict[str, Any] | None:
    """Finds dataset details by table_name across all connected databases."""
    datasets = list_all_superset_datasets(sess)
    for ds in datasets:
        if ds.get("table_name", "").lower() == table_name.lower():
            ds_id = ds["id"]
            try:
                detail_resp = sess.get(f"{SUPERSET_URL}/api/v1/dataset/{ds_id}", timeout=15)
                if detail_resp.status_code == 200:
                    return detail_resp.json().get("result", ds)
            except Exception:
                pass
            return ds
    return None


def execute_sqllab_query(
    sess: requests.Session,
    database_id: int,
    sql: str,
    schema: str | None = None,
    row_limit: int = 200,
) -> dict[str, Any]:
    """Executes SQL query via Superset's SQLLab endpoint against the specified database."""
    payload: dict[str, Any] = {
        "database_id": database_id,
        "sql": sql,
        "runAsync": False,
        "json": True,
        "queryLimit": row_limit,
    }
    if schema:
        payload["schema"] = schema
    resp = sess.post(f"{SUPERSET_URL}/api/v1/sqllab/execute/", json=payload, timeout=45)
    resp.raise_for_status()
    return resp.json()


def query_dataset_data(
    sess: requests.Session,
    dataset_id: int,
    columns: list[str] | None = None,
    metrics: list[Any] | None = None,
    groupby: list[str] | None = None,
    filters: list[dict[str, Any]] | None = None,
    orderby: list[Any] | None = None,
    row_limit: int = 200,
) -> dict[str, Any]:
    """Queries dataset data strictly through Superset's Dataset Semantic Layer (/api/v1/chart/data).

    This enforces all Row-Level Security (RLS) policies, calculated columns, and dataset permissions.
    """
    formatted_metrics = []
    for m in (metrics or []):
        if isinstance(m, str):
            if any(m.strip().upper().startswith(fn) for fn in ("SUM(", "COUNT(", "AVG(", "MIN(", "MAX(")):
                formatted_metrics.append({"expressionType": "SQL", "sqlExpression": m, "label": m})
            else:
                formatted_metrics.append(m)
        else:
            formatted_metrics.append(m)

    query_obj: dict[str, Any] = {
        "columns": columns or [],
        "metrics": formatted_metrics,
        "groupby": groupby or [],
        "filters": filters or [],
        "row_limit": row_limit,
    }
    if orderby:
        query_obj["orderby"] = orderby

    payload = {
        "datasource": {"id": dataset_id, "type": "table"},
        "result_format": "json",
        "result_type": "full",
        "queries": [query_obj],
    }

    resp = sess.post(f"{SUPERSET_URL}/api/v1/chart/data", json=payload, timeout=45)
    resp.raise_for_status()
    results = resp.json().get("result", [])
    if results and isinstance(results, list) and len(results) > 0:
        return results[0]
    return {}


def find_dataset(
    sess: requests.Session, database_id: int, table_name: str, schema: str
) -> dict[str, Any] | None:
    resp = sess.get(
        f"{SUPERSET_URL}/api/v1/dataset/",
        params={"q": rison_filter("table_name", table_name)},
        timeout=15,
    )
    resp.raise_for_status()
    for row in resp.json().get("result", []):
        if row.get("table_name") == table_name and row.get("database", {}).get("id") == database_id:
            return row
    return None

