"""Renders mcp_servers.json.template into mcp_servers.json using this container's env vars.

Keeping credentials in env vars (rather than baked into the committed template) means
the MCP config never needs secrets checked into the repo.
"""

from __future__ import annotations

import os
from pathlib import Path
from string import Template

TEMPLATE_PATH = Path(__file__).parent / "mcp_servers.json.template"
OUTPUT_PATH = Path("/app/mcp_servers.json")


def render() -> Path:
    rendered = Template(TEMPLATE_PATH.read_text(encoding="utf-8")).substitute(os.environ)
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    return OUTPUT_PATH


if __name__ == "__main__":
    render()
