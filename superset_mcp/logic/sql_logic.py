"""Business logic for the Superset dynamic read tools (Multi-Database & Multi-Dataset).

All discovery, inspection, and execution tools strictly operate through Superset's Dataset & REST API layer.
Direct database engine fallback has been completely removed to enforce dataset access control.
"""

from __future__ import annotations

import re
from typing import Any

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger("superset_mcp")

from superset_mcp.models.sql import (
    ColumnInfo,
    DescribeTableParams,
    DescribeTableResult,
    ExecuteSqlParams,
    ExecuteSqlResult,
    ListDatasetsParams,
    ListDatasetsResult,
    TableRef,
)
from superset_mcp.services.cache import get_cached_model, set_cached_model
from superset_mcp.services.ids import parse_id
from superset_mcp.services.sql_utils import enforce_limit, is_readonly_sql
from superset_mcp.services.superset_client import (
    get_superset_dataset_by_name,
    list_all_superset_datasets,
    query_dataset_data,
    superset_session,
)


def list_datasets(params: ListDatasetsParams) -> ListDatasetsResult:
    """Dynamically lists all datasets registered in Superset across all connected databases."""
    cache_key = f"list_datasets_dynamic_{params.schema or 'all'}"
    cached = get_cached_model(cache_key, ListDatasetsResult)
    if cached is not None:
        return cached

    tables: list[TableRef] = []
    try:
        sess = superset_session()
        superset_datasets = list_all_superset_datasets(sess)
        for ds in superset_datasets:
            t_name = ds.get("table_name")
            t_schema = ds.get("schema") or "public"
            db_info = ds.get("database") or {}
            db_id = db_info.get("id")
            db_name = db_info.get("database_name")
            desc = ds.get("description") or ds.get("verbose_map")

            if params.schema and t_schema.lower() != params.schema.lower():
                continue

            if t_name:
                tables.append(
                    TableRef(
                        table_schema=t_schema,
                        table_name=t_name,
                        database_id=db_id,
                        database_name=db_name,
                        description=str(desc) if desc else None,
                    )
                )
    except Exception as e:
        logger.error(f"Failed to fetch datasets via Superset API: {e}")

    res = ListDatasetsResult(
        message=f"{len(tables)} datasets registered in Superset." if tables else "No registered datasets found in Superset.",
        schema=params.schema,
        count=len(tables),
        tables=tables,
    )
    set_cached_model(cache_key, res)
    return res


def describe_table(params: DescribeTableParams) -> DescribeTableResult:
    """Dynamically returns column structure and types for any registered dataset in Superset."""
    cache_key = f"describe_table_dynamic_{params.schema or 'any'}_{params.table_name}"
    cached = get_cached_model(cache_key, DescribeTableResult)
    if cached is not None:
        return cached

    columns: list[ColumnInfo] = []
    db_id: int | None = params.database_id
    db_name: str | None = None
    target_schema: str | None = params.schema

    try:
        sess = superset_session()
        ds = get_superset_dataset_by_name(sess, params.table_name)
        if ds:
            target_schema = ds.get("schema") or target_schema or "public"
            db_info = ds.get("database") or {}
            db_id = db_info.get("id") or db_id
            db_name = db_info.get("database_name")
            raw_columns = ds.get("columns") or []
            for col in raw_columns:
                c_name = col.get("column_name")
                c_type = col.get("type") or "VARCHAR"
                c_desc = col.get("description")
                if c_name:
                    columns.append(
                        ColumnInfo(
                            name=c_name,
                            type=str(c_type),
                            nullable="YES",
                            description=c_desc,
                        )
                    )
    except Exception as e:
        logger.error(f"Failed to describe dataset via Superset API: {e}")

    if not columns:
        res = DescribeTableResult(
            message=f"Dataset '{params.table_name}' is not registered in Superset. Please register it as a dataset first using create_dataset.",
            schema=target_schema,
            table=params.table_name,
            database_id=db_id,
            database_name=db_name,
            columns=[],
        )
    else:
        res = DescribeTableResult(
            message=f"{len(columns)} columns on {target_schema}.{params.table_name} (Database ID: {db_id or 1}).",
            schema=target_schema,
            table=params.table_name,
            database_id=db_id,
            database_name=db_name,
            columns=columns,
        )

    set_cached_model(cache_key, res)
    return res


def _resolve_dataset_for_sql(sess, sql: str) -> dict[str, Any] | None:
    """Finds the registered Superset dataset corresponding to the tables referenced in SQL."""
    try:
        datasets = list_all_superset_datasets(sess)
        sql_lower = sql.lower()
        for ds in datasets:
            t_name = ds.get("table_name", "").lower()
            if t_name and re.search(rf"\b{re.escape(t_name)}\b", sql_lower):
                return ds
    except Exception as e:
        logger.warning(f"Error resolving dataset for SQL: {e}")
    return None


def _parse_sql_to_chart_data_query(sql: str) -> dict[str, Any]:
    """Parses a SELECT SQL query into structured components for Superset /api/v1/chart/data."""
    clean_sql = sql.strip().rstrip(";")

    # Extract LIMIT
    limit_match = re.search(r"\blimit\s+(\d+)", clean_sql, re.IGNORECASE)
    limit = int(limit_match.group(1)) if limit_match else 200

    # Extract ORDER BY
    orderby_list: list[list[Any]] = []
    order_match = re.search(r"\border\s+by\s+(.*?)(?:\blimit\b|$)", clean_sql, re.IGNORECASE | re.DOTALL)
    if order_match:
        raw_order = order_match.group(1).strip()
        for item in raw_order.split(","):
            item_clean = item.strip()
            if not item_clean:
                continue
            is_desc = item_clean.upper().endswith("DESC")
            col_or_expr = re.sub(r"\s+(?:ASC|DESC)\b", "", item_clean, flags=re.IGNORECASE).strip()
            if any(col_or_expr.upper().startswith(fn) for fn in ("SUM(", "COUNT(", "AVG(", "MIN(", "MAX(")):
                orderby_list.append([{"expressionType": "SQL", "sqlExpression": col_or_expr, "label": col_or_expr}, not is_desc])
            else:
                orderby_list.append([col_or_expr, not is_desc])

    # Extract GROUP BY
    groupby_list: list[str] = []
    group_match = re.search(r"\bgroup\s+by\s+(.*?)(?:\border\s+by\b|\blimit\b|$)", clean_sql, re.IGNORECASE | re.DOTALL)
    if group_match:
        raw_group = group_match.group(1).strip()
        for item in raw_group.split(","):
            item_clean = item.strip()
            if item_clean:
                groupby_list.append(item_clean)

    # Extract SELECT columns and metrics
    select_match = re.search(r"^\s*select\s+(.*?)\s+from\b", clean_sql, re.IGNORECASE | re.DOTALL)
    columns_list: list[str] = []
    metrics_list: list[Any] = []
    if select_match:
        raw_select = select_match.group(1).strip()
        for item in raw_select.split(","):
            item_clean = item.strip()
            if not item_clean:
                continue
            expr_no_alias = re.sub(r"\s+(?:AS|as)\s+.*$", "", item_clean).strip()
            if any(expr_no_alias.upper().startswith(fn) for fn in ("SUM(", "COUNT(", "AVG(", "MIN(", "MAX(")):
                metrics_list.append({"expressionType": "SQL", "sqlExpression": expr_no_alias, "label": expr_no_alias})
            else:
                if expr_no_alias != "*":
                    columns_list.append(expr_no_alias)

    return {
        "columns": columns_list if not groupby_list else [],
        "groupby": groupby_list or columns_list,
        "metrics": metrics_list,
        "orderby": orderby_list,
        "row_limit": limit,
    }


def execute_sql(params: ExecuteSqlParams) -> ExecuteSqlResult:
    """Runs data query strictly through Superset Dataset Semantic Layer (/api/v1/chart/data).

    Enforces 100% Superset Dataset security, Row-Level Security (RLS), and metric definitions.
    """
    sess = superset_session()
    target_dataset_id: int | None = None
    db_id: int = 1

    # 1. Resolve dataset
    if params.dataset_id is not None:
        target_dataset_id = parse_id(params.dataset_id)
    elif params.sql or params.query:
        actual_sql = params.sql or params.query
        if not is_readonly_sql(actual_sql):
            raise ValueError("Only SELECT/CTE read-only SQL is allowed")
        safe_sql = enforce_limit(actual_sql, params.row_limit)
        ds = _resolve_dataset_for_sql(sess, safe_sql)
        if not ds:
            raise ValueError(
                "Truy vấn bị từ chối: Bảng được truy vấn chưa được đăng ký làm Dataset trong Superset. "
                "Tất cả các truy vấn phải thông qua Superset Dataset Semantic Layer. "
                "Vui lòng đăng ký dataset bằng tool 'create_dataset' trước khi truy vấn."
            )
        target_dataset_id = ds["id"]
        db_id = ds.get("database", {}).get("id", 1)
    else:
        raise ValueError("Either 'dataset_id' or 'sql' parameter is required")

    # 2. Build Semantic Layer query payload
    if params.columns or params.metrics or params.groupby:
        query_cols = params.columns or []
        query_metrics = params.metrics or []
        query_groupby = params.groupby or []
        query_orderby = params.orderby or []
        row_limit = params.row_limit
    else:
        actual_sql = params.sql or params.query or ""
        parsed = _parse_sql_to_chart_data_query(actual_sql)
        query_cols = parsed["columns"]
        query_metrics = parsed["metrics"]
        query_groupby = parsed["groupby"]
        query_orderby = parsed["orderby"]
        row_limit = min(params.row_limit, parsed["row_limit"])

    cache_key = f"dataset_semantic_query_{target_dataset_id}_{query_cols}_{query_metrics}_{query_groupby}_{row_limit}"
    cached = get_cached_model(cache_key, ExecuteSqlResult)
    if cached is not None:
        try:
            logger.opt(colors=True).info("<yellow>[RAM CACHE HIT]</yellow> dataset_id=<cyan>{}</cyan>", target_dataset_id)
        except Exception:
            logger.info("[RAM CACHE HIT] dataset_id={}", target_dataset_id)
        return cached

    try:
        logger.opt(colors=True).info(
            "<magenta>[QUERYING DATASET SEMANTIC LAYER]</magenta> dataset_id=<yellow>{}</yellow>, metrics=<cyan>{}</cyan>, groupby=<cyan>{}</cyan>",
            target_dataset_id,
            query_metrics,
            query_groupby,
        )
    except Exception:
        logger.info("[QUERYING DATASET SEMANTIC LAYER] dataset_id={}, metrics={}, groupby={}", target_dataset_id, query_metrics, query_groupby)

    # 3. Execute through Superset Dataset Semantic Layer API (/api/v1/chart/data)
    result_data = query_dataset_data(
        sess=sess,
        dataset_id=target_dataset_id,
        columns=query_cols,
        metrics=query_metrics,
        groupby=query_groupby,
        filters=params.filters,
        orderby=query_orderby,
        row_limit=row_limit,
    )

    raw_rows = result_data.get("data", [])
    col_names = result_data.get("colnames", [])
    compiled_sql = result_data.get("query", params.sql or "")

    res = ExecuteSqlResult(
        message=f"{len(raw_rows)} rows returned via Superset Dataset Semantic Layer (Dataset ID: {target_dataset_id}).",
        sql=compiled_sql,
        dataset_id=target_dataset_id,
        database_id=db_id,
        row_count=len(raw_rows),
        columns=col_names,
        rows=raw_rows,
    )
    set_cached_model(cache_key, res)
    return res


