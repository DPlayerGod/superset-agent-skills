"""Natural-language colour names (Vietnamese and English) to Superset values."""

from __future__ import annotations

import re
from typing import Any

_COLOR_MAP = {
    "do": "#E74C3C",
    "đỏ": "#E74C3C",
    "red": "#E74C3C",
    "xanh": "#1890FF",
    "xanh duong": "#1890FF",
    "xanh dương": "#1890FF",
    "xanh bien": "#1890FF",
    "xanh biển": "#1890FF",
    "blue": "#1890FF",
    "xanh la": "#52C41A",
    "xanh lá": "#52C41A",
    "xanh luc": "#52C41A",
    "xanh lục": "#52C41A",
    "green": "#52C41A",
    "cam": "#FA8C16",
    "orange": "#FA8C16",
    "tim": "#722ED1",
    "tím": "#722ED1",
    "purple": "#722ED1",
    "vang": "#FADB14",
    "vàng": "#FADB14",
    "yellow": "#FADB14",
    "hong": "#EB2F96",
    "hồng": "#EB2F96",
    "pink": "#EB2F96",
    "xam": "#8C8C8C",
    "xám": "#8C8C8C",
    "gray": "#8C8C8C",
    "grey": "#8C8C8C",
    "den": "#262626",
    "đen": "#262626",
    "black": "#262626",
    "trang": "#FFFFFF",
    "trắng": "#FFFFFF",
    "white": "#FFFFFF",
    "teal": "#13C2C2",
    "cyan": "#13C2C2",
}


_COLOR_SCHEME_MAP = {
    "do": "redScheme",
    "đỏ": "redScheme",
    "red": "redScheme",
    "xanh": "blueScheme",
    "xanh duong": "blueScheme",
    "xanh dương": "blueScheme",
    "xanh bien": "blueScheme",
    "xanh biển": "blueScheme",
    "blue": "blueScheme",
    "xanh la": "greenScheme",
    "xanh lá": "greenScheme",
    "xanh luc": "greenScheme",
    "xanh lục": "greenScheme",
    "green": "greenScheme",
    "cam": "orangeScheme",
    "orange": "orangeScheme",
    "tim": "purpleScheme",
    "tím": "purpleScheme",
    "purple": "purpleScheme",
    "vang": "yellowScheme",
    "vàng": "yellowScheme",
    "yellow": "yellowScheme",
}


def resolve_color_scheme(color_name: str | None) -> str | None:
    if not color_name:
        return None
    raw = color_name.strip().lower()
    return _COLOR_SCHEME_MAP.get(raw)


def hex_to_rgba(hex_color: str, alpha: float = 1.0) -> dict[str, Any]:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    if len(hex_color) == 6:
        r, g, b = tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
        return {"r": r, "g": g, "b": b, "a": alpha}
    return {"r": 24, "g": 144, "b": 255, "a": alpha}


def rgba_to_hex(color_picker: dict[str, Any] | str | None) -> str | None:
    if not color_picker:
        return None
    if isinstance(color_picker, str):
        return resolve_color(color_picker) or color_picker
    if isinstance(color_picker, dict):
        r = int(color_picker.get("r", 0))
        g = int(color_picker.get("g", 0))
        b = int(color_picker.get("b", 0))
        return f"#{r:02X}{g:02X}{b:02X}"
    return None


def resolve_color(color_name_or_hex: str | None) -> str | None:
    if not color_name_or_hex:
        return None
    raw = color_name_or_hex.strip().lower()
    if raw in _COLOR_MAP:
        return _COLOR_MAP[raw]
    if re.match(r"^#(?:[0-9a-fA-F]{3}){1,2}$", raw):
        return raw.upper()
    return None
