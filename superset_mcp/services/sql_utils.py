"""Read-only SQL guarding and row serialization."""

from __future__ import annotations

import re
from typing import Any

from superset_mcp.config import MAX_LIMIT


def is_readonly_sql(sql: str) -> bool:
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


def enforce_limit(sql: str, row_limit: int) -> str:
    safe_limit = max(1, min(row_limit, MAX_LIMIT))
    if re.search(r"\blimit\s+\d+", sql, flags=re.IGNORECASE):
        return sql
    return f"{sql.rstrip(';')} LIMIT {safe_limit};"


def rows_to_json(rows: list[tuple[Any, ...]], columns: list[str]) -> list[dict[str, Any]]:
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
