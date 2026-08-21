"""Environment-derived configuration shared by every layer."""

from __future__ import annotations

import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://super_user:super_pass@localhost:5432/super_db",
)
MAX_LIMIT = int(os.getenv("MCP_MAX_ROWS", "500"))

SUPERSET_URL = os.getenv("SUPERSET_URL", "http://superset:8088").rstrip("/")
# Everything the browser has to load (links, preview iframes) is built from this
# instead of SUPERSET_URL, whose Docker-internal hostname does not resolve there.
SUPERSET_PUBLIC_URL = os.getenv("SUPERSET_PUBLIC_URL", SUPERSET_URL).rstrip("/")
SUPERSET_ADMIN_USERNAME = os.getenv("SUPERSET_ADMIN_USERNAME", "admin")
SUPERSET_ADMIN_PASSWORD = os.getenv("SUPERSET_ADMIN_PASSWORD", "admin")

SQL_CACHE_TTL = float(os.getenv("MCP_SQL_CACHE_TTL", "300"))  # Default 5 minutes (300 seconds)

CREATE_TOKEN_PATH = os.getenv("MCP_CREATE_TOKEN_PATH", "/tmp/mcp_create_tokens.json")
CREATE_TOKEN_TTL = float(os.getenv("MCP_CREATE_TOKEN_TTL", "900"))

# `standalone` values verified against this exact Superset 6.1.0 build rather than
# assumed: the explore view treats the parameter as a boolean
# (superset/utils/core.py: any value other than "0"/"false" counts as standalone),
# while the dashboard frontend reads it as the numeric DashboardStandaloneMode enum
# (None=0, HideNav=1, HideNavAndTitle=2, Report=3 - read out of the shipped JS
# bundle). Chart previews use 1; dashboard previews use 2 so the title bar does not
# eat vertical space in the small chat iframe.
EXPLORE_STANDALONE = "1"
DASHBOARD_STANDALONE = "2"

