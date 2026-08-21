"""Id coercion shared by every tool that accepts `int | str`."""

from __future__ import annotations


def parse_id(val: int | str) -> int:
    if isinstance(val, int):
        return val
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return 1
