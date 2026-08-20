"""Writes the mcp_servers.json the Claude Code CLI is pointed at, for this container.

Two shapes, picked by MCP_TRANSPORT:

  http (default)  a bare {"type": "http", "url": ...} pointing at the long-lived MCP
                  server the gateway starts once at boot. No credentials appear here
                  at all - the sidecar inherits them from the gateway's own env.
  stdio           the historical shape, rendered from mcp_servers.json.template, where
                  the CLI spawns mcp_server.py itself once per turn and therefore has
                  to be handed the env vars it should spawn it with.

Either way credentials stay in env vars rather than baked into the committed template,
so the MCP config never needs secrets checked into the repo.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from string import Template

TEMPLATE_PATH = Path(__file__).parent / "mcp_servers.json.template"
OUTPUT_PATH = Path("/app/mcp_servers.json")


def render() -> Path:
    if os.getenv("MCP_TRANSPORT", "http").lower() in ("http", "streamable-http"):
        host = os.getenv("MCP_HTTP_HOST", "127.0.0.1")
        port = os.getenv("MCP_HTTP_PORT", "8765")
        config = {
            "mcpServers": {
                "superset-postgres": {"type": "http", "url": f"http://{host}:{port}/mcp"}
            }
        }
        OUTPUT_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return OUTPUT_PATH

    rendered = Template(TEMPLATE_PATH.read_text(encoding="utf-8")).substitute(os.environ)
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    return OUTPUT_PATH


if __name__ == "__main__":
    render()
