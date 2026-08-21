"""MCP wrappers for the dynamic multi-database read tools."""

from __future__ import annotations

from typing import Any

from superset_mcp.app import mcp
from superset_mcp.logic import sql_logic
from superset_mcp.models.sql import DescribeTableParams, ExecuteSqlParams, ListDatasetsParams


@mcp.tool()
def list_datasets(schema: str | None = None) -> dict[str, Any]:
    """Lists all active business datasets available in Superset across all connected databases (PostgreSQL, MariaDB, etc.).

    Call this tool whenever you need to discover what datasets and tables exist in the system before answering a question or writing SQL queries.

    schema: Optional schema filter. Leave empty/None to list all datasets across all databases.
    """
    return sql_logic.list_datasets(ListDatasetsParams(schema=schema)).model_dump()


@mcp.tool()
def describe_table(table_name: str, schema: str | None = None, database_id: int | None = None) -> dict[str, Any]:
    """Returns a table or dataset's column structure, data types, and database information.

    Call this before writing SQL against any table to inspect the exact column names and types.

    table_name: exact table/dataset name, e.g. "fact_sales_orders" or "fact_employee_allocation".
    schema: Optional schema name.
    database_id: Optional database ID if known.
    """
    return sql_logic.describe_table(
        DescribeTableParams(table_name=table_name, schema=schema, database_id=database_id)
    ).model_dump()


@mcp.tool()
def execute_sql(
    sql: str | None = None,
    query: str | None = None,
    dataset_id: int | str | None = None,
    columns: list[str] | None = None,
    metrics: list[str] | None = None,
    groupby: list[str] | None = None,
    database_id: int | None = None,
    schema: str | None = None,
    row_limit: int = 200,
) -> dict[str, Any]:
    """Queries data strictly through Superset's Dataset Semantic Layer (/api/v1/chart/data).

    Enforces 100% Superset Dataset security, Row-Level Security (RLS), and metric definitions.

    sql: a single SELECT or WITH statement targeting a registered Superset dataset.
    query: alias for `sql`.
    dataset_id: optional numeric dataset ID.
    columns: optional list of column dimensions to retrieve.
    metrics: optional list of aggregate metrics, e.g. ["SUM(net_revenue_vnd)"].
    groupby: optional list of grouping dimensions.
    row_limit: maximum rows to return, default 200.
    """
    return sql_logic.execute_sql(
        ExecuteSqlParams(
            sql=sql,
            query=query,
            dataset_id=dataset_id,
            columns=columns,
            metrics=metrics,
            groupby=groupby,
            database_id=database_id,
            schema=schema,
            row_limit=row_limit,
        )
    ).model_dump()


@mcp.tool()
def run_sql_readonly(
    sql: str | None = None,
    query: str | None = None,
    dataset_id: int | str | None = None,
    columns: list[str] | None = None,
    metrics: list[str] | None = None,
    groupby: list[str] | None = None,
    database_id: int | None = None,
    schema: str | None = None,
    row_limit: int = 200,
) -> dict[str, Any]:
    """Runs data query on Superset Dataset Semantic Layer - an exact alias for execute_sql."""
    return sql_logic.execute_sql(
        ExecuteSqlParams(
            sql=sql,
            query=query,
            dataset_id=dataset_id,
            columns=columns,
            metrics=metrics,
            groupby=groupby,
            database_id=database_id,
            schema=schema,
            row_limit=row_limit,
        )
    ).model_dump()

