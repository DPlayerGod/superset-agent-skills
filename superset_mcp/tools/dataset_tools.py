"""MCP wrappers for the Superset dataset tools."""

from __future__ import annotations

from typing import Any

from superset_mcp.app import mcp
from superset_mcp.logic import dataset_logic
from superset_mcp.models.dataset import (
    CreateDatasetParams,
    GenerateExploreLinkParams,
    GetDatasetInfoParams,
)


@mcp.tool()
def create_dataset(
    table_name: str, schema: str = "public", sql: str | None = None
) -> dict[str, Any]:
    """Registers a Superset dataset (get-or-create). A chart can only be built on one.

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

    table_name: for a physical dataset, the exact Postgres table name. For a virtual
    dataset, a new descriptive name you choose, e.g. "top_projects_by_fte".
    schema: Postgres schema, defaults to "public".
    sql: a SELECT/WITH statement. Omit it entirely for a physical dataset.

    Returns dataset_id, which is what create_chart takes.
    """
    return dataset_logic.create_dataset(
        CreateDatasetParams(table_name=table_name, schema=schema, sql=sql)
    ).model_dump()


@mcp.tool()
def get_dataset_info(dataset_id: int | str) -> dict[str, Any]:
    """Reads one dataset: its name, schema, backing SQL and column list.

    Use it to check which columns a dataset exposes before building a chart on it,
    or to see the query behind a virtual dataset.

    dataset_id: the numeric id returned by create_dataset, e.g. 12.
    """
    return dataset_logic.get_dataset_info(
        GetDatasetInfoParams(dataset_id=dataset_id)
    ).model_dump()


@mcp.tool()
def generate_explore_link(dataset_id: int | str) -> dict[str, Any]:
    """Builds the Superset Explore URL for a dataset, so the user can chart it by hand.

    This creates nothing - it only returns a link to the chart builder, opened on
    that dataset.

    dataset_id: the numeric dataset id, e.g. 12.
    """
    return dataset_logic.generate_explore_link(
        GenerateExploreLinkParams(dataset_id=dataset_id)
    ).model_dump()
