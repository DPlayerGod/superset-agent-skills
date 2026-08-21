"""The single FastMCP instance every tool module registers against.

Kept in its own module so `services.tokens` can read the live session off it
without importing the tool modules (which import the services back).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("superset-postgres-mcp")
