"""Superset + Postgres MCP server, split into two layers.

    superset_mcp/models    Pydantic Params/Result schemas (the tool contract)
    superset_mcp/services  infrastructure: cache, Superset HTTP client, urls, tokens
    superset_mcp/logic     business logic: pure `f(Params) -> Result`, no MCP import
    superset_mcp/tools     thin @mcp.tool() wrappers: primitives in, .model_dump() out

Anything in `logic` can be unit-tested by constructing a Params model and calling
the function - no MCP server, no decorator, no transport.
"""

from __future__ import annotations

import warnings

# Several results carry a business field literally named `schema` (list_datasets,
# describe_table, get_dataset_info). Pydantic warns because BaseModel still has a
# deprecated `.schema()` method, but renaming the field would change the JSON the
# assistant already reads, so the field name stays and the warning goes.
warnings.filterwarnings(
    "ignore",
    message=r'Field name "schema" .*shadows an attribute',
    category=UserWarning,
)

from superset_mcp.app import mcp  # noqa: E402

__all__ = ["mcp", "register_tools"]


def register_tools() -> None:
    """Imports every tool module, which is what runs the @mcp.tool() decorators."""
    from superset_mcp.tools import (  # noqa: F401
        chart_tools,
        dashboard_tools,
        dataset_tools,
        sql_tools,
        system_tools,
    )
