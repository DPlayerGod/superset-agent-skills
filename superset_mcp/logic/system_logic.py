"""Business logic for the health, instance and database tools."""

from __future__ import annotations

from superset_mcp.config import SUPERSET_PUBLIC_URL, SUPERSET_URL
from superset_mcp.models.system import (
    DatabaseSummary,
    GetDatabaseInfoParams,
    GetDatabaseInfoResult,
    GetInstanceInfoParams,
    GetInstanceInfoResult,
    HealthCheckParams,
    HealthCheckResult,
    HealthDetails,
    ListDatabasesParams,
    ListDatabasesResult,
    OpenSqlLabWithContextParams,
    OpenSqlLabWithContextResult,
)
from superset_mcp.services.cache import get_cached_model, set_cached_model
from superset_mcp.services.ids import parse_id
from superset_mcp.services.superset_client import superset_session
from superset_mcp.services.urls import sql_lab_url


def health_check(params: HealthCheckParams) -> HealthCheckResult:
    """Probes Superset API and connected databases via Superset API."""
    status = "ok"
    details = HealthDetails()
    errors: list[str] = []

    try:
        sess = superset_session()
        resp = sess.get(f"{SUPERSET_URL}/api/v1/security/csrf_token/", timeout=10)
        resp.raise_for_status()
        details.superset = "ok"
    except Exception as e:
        status = "error"
        details.superset = f"failed: {str(e)}"
        errors.append(details.superset)

    try:
        sess = superset_session()
        db_resp = sess.get(f"{SUPERSET_URL}/api/v1/database/?q=(page_size:10)", timeout=10)
        db_resp.raise_for_status()
        details.database = "ok"
    except Exception as e:
        status = "error"
        details.database = f"failed: {str(e)}"
        errors.append(details.database)

    return HealthCheckResult(
        success=status == "ok",
        message="Database and Superset are reachable." if status == "ok" else "One or more dependencies are down.",
        errors=errors,
        status=status,
        details=details,
    )



def get_instance_info(params: GetInstanceInfoParams) -> GetInstanceInfoResult:
    return GetInstanceInfoResult(
        message="Superset instance metadata.",
        superset_url=SUPERSET_PUBLIC_URL,
        database_backend="postgresql",
        description="Superset instance serving postgres analytics workspace.",
    )


def list_databases(params: ListDatabasesParams) -> ListDatabasesResult:
    cache_key = "list_databases"
    cached = get_cached_model(cache_key, ListDatabasesResult)
    if cached is not None:
        return cached

    sess = superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/database/?q=(page_size:100)", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", [])
    res = ListDatabasesResult(
        message=f"{len(result)} databases configured in Superset.",
        count=len(result),
        databases=[
            DatabaseSummary(
                id=d.get("id"),
                database_name=d.get("database_name"),
                backend=d.get("backend"),
            )
            for d in result
        ],
    )
    set_cached_model(cache_key, res)
    return res


def get_database_info(params: GetDatabaseInfoParams) -> GetDatabaseInfoResult:
    database_id = parse_id(params.database_id)
    cache_key = f"database_info_{database_id}"
    cached = get_cached_model(cache_key, GetDatabaseInfoResult)
    if cached is not None:
        return cached

    sess = superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/database/{database_id}", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", {})
    res = GetDatabaseInfoResult(
        message=f"Database {database_id}: {result.get('database_name')}",
        id=result.get("id"),
        database_name=result.get("database_name"),
        backend=result.get("backend"),
        expose_in_sqllab=result.get("expose_in_sqllab"),
    )
    set_cached_model(cache_key, res)
    return res


def open_sql_lab_with_context(
    params: OpenSqlLabWithContextParams,
) -> OpenSqlLabWithContextResult:
    return OpenSqlLabWithContextResult(
        message="SQL Lab URL with the query preloaded.",
        sql=params.sql,
        sql_lab_url=sql_lab_url(params.sql),
    )
