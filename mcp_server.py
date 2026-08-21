"""Entry point for the superset-postgres MCP server.

The tools themselves live in the `superset_mcp` package, split into a business-logic
layer (`superset_mcp/logic`, pure `f(Params) -> Result` functions that need no MCP
server to test) and a thin MCP wrapper layer (`superset_mcp/tools`). This file only
registers them and starts a transport.
"""

import os
import sys
from pathlib import Path

# The CLI spawns this file by absolute path (`python3 -u /app/mcp_server.py`), which
# puts /app on sys.path already - but not when it is imported from elsewhere, so be
# explicit about where the package lives.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from superset_mcp import mcp, register_tools  # noqa: E402

register_tools()


if __name__ == "__main__":
    # stdio (the default) is what the Claude Code CLI uses when it spawns this file
    # itself - and it spawns a fresh copy for every single turn, so python start-up,
    # the SQLAlchemy import and the Superset login are all paid again on each
    # question. "http" instead serves one long-lived process that the gateway starts
    # once at boot and every turn then shares, leaving a turn only the HTTP
    # handshake to pay. See _start_mcp_sidecar in claude_gateway/gateway_server.py.
    if os.getenv("MCP_TRANSPORT", "stdio").lower() in ("http", "streamable-http"):
        mcp.settings.host = os.getenv("MCP_HTTP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.getenv("MCP_HTTP_PORT", "8765"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
