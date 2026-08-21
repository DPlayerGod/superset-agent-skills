"""Business logic for the Superset dataset tools.

`create_dataset` used to be one function with an `if sql is not None` running down
the middle. The two use-cases are separate functions here - a physical dataset over
a real table, and a virtual dataset over a query - with `create_dataset` left as the
dispatcher the MCP tool of the same name calls.
"""

from __future__ import annotations

from superset_mcp.config import SUPERSET_URL
from superset_mcp.models.dataset import (
    CreateDatasetParams,
    CreateDatasetResult,
    DatasetColumn,
    GenerateExploreLinkParams,
    GenerateExploreLinkResult,
    GetDatasetInfoParams,
    GetDatasetInfoResult,
)
from superset_mcp.services.cache import get_cached_model, invalidate_sql_cache, set_cached_model
from superset_mcp.services.ids import parse_id
from superset_mcp.services.sql_utils import is_readonly_sql
from superset_mcp.services.superset_client import (
    find_dataset,
    get_or_create_postgres_database,
    superset_session,
)
from superset_mcp.services.urls import explore_link


def _resolve_target_database(sess, explicit_db_id: int | None, table_name: str, sql: str | None) -> int:
    if explicit_db_id:
        return explicit_db_id
    from superset_mcp.services.superset_client import list_all_superset_datasets
    try:
        all_ds = list_all_superset_datasets(sess)
        text_to_match = f"{table_name} {sql or ''}".lower()
        for ds in all_ds:
            t = ds.get("table_name", "").lower()
            if t and t in text_to_match:
                db_id = ds.get("database", {}).get("id")
                if db_id:
                    return db_id
    except Exception:
        pass
    return get_or_create_postgres_database(sess)


def create_physical_dataset(params: CreateDatasetParams) -> CreateDatasetResult:
    """Get-or-create a dataset backed by a real table across any connected database."""
    sess = superset_session()
    database_id = _resolve_target_database(sess, params.database_id, params.table_name, None)
    schema = params.schema or "public"

    existing = find_dataset(sess, database_id, params.table_name, schema)
    if existing:
        return CreateDatasetResult(
            message=f"Dataset '{params.table_name}' already existed on database {database_id}.",
            dataset_id=existing["id"],
            already_existed=True,
        )

    resp = sess.post(
        f"{SUPERSET_URL}/api/v1/dataset/",
        json={"database": database_id, "schema": schema, "table_name": params.table_name},
        timeout=30,
    )
    resp.raise_for_status()
    invalidate_sql_cache(f"list_datasets_{schema}")
    return CreateDatasetResult(
        message=f"Physical dataset '{params.table_name}' created on database {database_id}.",
        dataset_id=resp.json()["id"],
        already_existed=False,
        virtual=False,
    )


def create_virtual_dataset(params: CreateDatasetParams) -> CreateDatasetResult:
    """Get-or-create a dataset whose source is a SELECT/CTE query on any connected database."""
    sql = (params.sql or "").strip().rstrip(";")
    if not is_readonly_sql(sql):
        raise ValueError("Only SELECT/CTE statements are allowed for a virtual dataset.")

    sess = superset_session()
    database_id = _resolve_target_database(sess, params.database_id, params.table_name, sql)
    schema = params.schema or "public"
    existing = find_dataset(sess, database_id, params.table_name, schema)

    if existing:
        detail = sess.get(f"{SUPERSET_URL}/api/v1/dataset/{existing['id']}", timeout=15)
        detail.raise_for_status()
        current_sql = (detail.json().get("result", {}).get("sql") or "").strip().rstrip(";")
        if current_sql == sql:
            return CreateDatasetResult(
                message=f"Virtual dataset '{params.table_name}' already had this SQL on database {database_id}.",
                dataset_id=existing["id"],
                already_existed=True,
            )
        update = sess.put(
            f"{SUPERSET_URL}/api/v1/dataset/{existing['id']}",
            json={"sql": sql},
            params={"override_columns": "true"},
            timeout=30,
        )
        update.raise_for_status()
        invalidate_sql_cache(f"dataset_info_{existing['id']}")
        return CreateDatasetResult(
            message=f"Virtual dataset '{params.table_name}' updated with the new SQL.",
            dataset_id=existing["id"],
            already_existed=True,
            sql_updated=True,
        )

    resp = sess.post(
        f"{SUPERSET_URL}/api/v1/dataset/",
        json={
            "database": database_id,
            "schema": schema,
            "table_name": params.table_name,
            "sql": sql,
        },
        timeout=30,
    )
    resp.raise_for_status()
    invalidate_sql_cache(f"list_datasets_{schema}")
    return CreateDatasetResult(
        message=f"Virtual dataset '{params.table_name}' created on database {database_id}.",
        dataset_id=resp.json()["id"],
        already_existed=False,
        virtual=True,
    )


def create_dataset(params: CreateDatasetParams) -> CreateDatasetResult:
    """Dispatches to the physical or virtual path depending on whether `sql` is set."""
    if params.sql is not None:
        return create_virtual_dataset(params)
    return create_physical_dataset(params)


def get_dataset_info(params: GetDatasetInfoParams) -> GetDatasetInfoResult:
    dataset_id = parse_id(params.dataset_id)
    cache_key = f"dataset_info_{dataset_id}"
    cached = get_cached_model(cache_key, GetDatasetInfoResult)
    if cached is not None:
        return cached

    sess = superset_session()
    resp = sess.get(f"{SUPERSET_URL}/api/v1/dataset/{dataset_id}", timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", {})
    res = GetDatasetInfoResult(
        message=f"Dataset {dataset_id}: {result.get('table_name')}",
        id=result.get("id"),
        table_name=result.get("table_name"),
        schema=result.get("schema"),
        sql=result.get("sql"),
        columns=[
            DatasetColumn(column_name=c.get("column_name"), type=c.get("type"))
            for c in (result.get("columns") or [])
        ],
    )
    set_cached_model(cache_key, res)
    return res


def generate_explore_link(params: GenerateExploreLinkParams) -> GenerateExploreLinkResult:
    dataset_id = parse_id(params.dataset_id)
    return GenerateExploreLinkResult(
        message=f"Explore link for dataset {dataset_id}.",
        dataset_id=dataset_id,
        explore_url=explore_link(dataset_id),
    )
