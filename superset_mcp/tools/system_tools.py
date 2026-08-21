"""MCP wrappers for the health, instance and database tools."""

from __future__ import annotations

from typing import Any

from superset_mcp.app import mcp
from superset_mcp.logic import system_logic
from superset_mcp.models.system import (
    GetDatabaseInfoParams,
    GetInstanceInfoParams,
    HealthCheckParams,
    ListDatabasesParams,
    OpenSqlLabWithContextParams,
)


@mcp.tool()
def health_check() -> dict[str, Any]:
    """Checks that Postgres and Superset are both reachable from this server.

    Call it when a tool has just failed and you need to tell the user whether the
    problem is the request or the infrastructure. It reports a dependency being down
    as data (status "error"), it does not fail the call.
    """
    return system_logic.health_check(HealthCheckParams()).model_dump()


@mcp.tool()
def get_instance_info() -> dict[str, Any]:
    """Returns metadata about this Superset instance: its public URL and backend.

    Useful when the user asks where their charts live or wants the address of the
    Superset they are talking to.
    """
    return system_logic.get_instance_info(GetInstanceInfoParams()).model_dump()


@mcp.tool()
def list_databases() -> dict[str, Any]:
    """Lists the database connections configured inside Superset.

    Rarely needed: every tool here already targets the workspace Postgres. Use it
    when the user asks what Superset is connected to.
    """
    return system_logic.list_databases(ListDatabasesParams()).model_dump()


@mcp.tool()
def get_database_info(database_id: int | str) -> dict[str, Any]:
    """Reads one Superset database connection: name, backend, SQL Lab exposure.

    database_id: numeric id from list_databases, e.g. 1.
    """
    return system_logic.get_database_info(
        GetDatabaseInfoParams(database_id=database_id)
    ).model_dump()


@mcp.tool()
def open_sql_lab_with_context(sql: str) -> dict[str, Any]:
    """Builds a SQL Lab URL with a query already typed into the editor.

    Use it to hand the user a query to run or edit themselves. It executes nothing -
    to get results yourself, use execute_sql.

    sql: the query text to preload, e.g.
    "SELECT * FROM fact_employee_allocation LIMIT 100".
    """
    return system_logic.open_sql_lab_with_context(
        OpenSqlLabWithContextParams(sql=sql)
    ).model_dump()
