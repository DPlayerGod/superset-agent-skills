# Minimal Rollout Guide: Claude Code Gateway + MCP + Superset

## 1) Start infrastructure

Set your Anthropic API key before starting (never commit it):

export ANTHROPIC_API_KEY=sk-ant-...

Or create a `.env` file in the project root (do not commit it) with:

ANTHROPIC_API_KEY=sk-ant-...

Then, from project root:

docker compose up -d --build

Services:
- PostgreSQL: localhost:5432
- Superset: http://localhost:8088
- Claude Gateway: internal Docker network only (`claude_gateway:8090`, not exposed to the host)

There is no standalone MCP service to start separately anymore — the MCP server
(`mcp_server.py`) now runs as a stdio subprocess launched by the Claude Code CLI
itself, inside the `claude_gateway` container.

## 2) Verify data and gateway

Open Superset at `http://localhost:8088`, sign in (`admin` / `admin`), then open a
Dashboard or SQL Lab. The **AI Agent** button appears in the lower-right corner —
this UI is unchanged from before. It is served by Superset and its calls are
authenticated with the same Superset login session.

`docker compose logs -f claude_gateway` shows each headless `claude -p` invocation
and the MCP tool calls it makes, useful while verifying a question end-to-end.

## 3) How the chat panel is wired to Claude

There is nothing to configure manually. Behind the scenes:

- Superset's browser-side panel (`superset/static/vdt-ai-chat.js`) POSTs to
  Superset's own backend at `/api/v1/vdt-ai-chat/query`.
- Superset's backend (`superset/superset_config.py`) forwards the authenticated
  request to `http://claude_gateway:8090/api/v1/agent/query`.
- `claude_gateway/gateway_server.py` runs `claude -p` (Claude Code CLI, headless
  mode) with an MCP config (`claude_gateway/mcp_servers.json.template`, rendered
  at container start) pointing at `mcp_server.py`, which exposes 6 tools:
  - Read-only: `list_datasets`, `describe_table`, `run_sql_readonly`
  - Write (Superset REST API): `create_dataset`, `create_chart`, `create_dashboard`
- The gateway resumes the same Claude conversation across turns using the
  `session_id` the browser already generates per page load, so multi-turn
  context is preserved.

## 4) Connect Superset workflow

Flow:
- User asks a question in the Superset AI Agent panel.
- Superset proxies the authenticated request to the internal Claude Gateway.
- Claude decides which MCP tools to call (read data, and/or create a
  dataset/chart/dashboard), then answers in the panel.
- If Claude created a chart or dashboard, the answer includes a clickable
  Superset URL to it.

## 5) Notes

- Only read-only SELECT/CTE SQL is allowed via `run_sql_readonly`; DDL and DML are
  rejected by `mcp_server.py`'s validator regardless of what the model asks for.
- The write tools (`create_dataset`/`create_chart`/`create_dashboard`) authenticate
  to Superset's REST API as a **fixed service account** (`SUPERSET_ADMIN_USERNAME`/
  `SUPERSET_ADMIN_PASSWORD`, defaulting to the bootstrapped `admin`/`admin`
  account) — charts/dashboards created by the agent are owned by that account, not
  by whichever Superset user is chatting. This is an accepted tradeoff for this
  rollout, not per-user identity forwarding.
- `create_chart`'s `params` payload is a best-effort mapping for a curated set of
  `viz_type`s (`table`, `echarts_timeseries_line`, `echarts_timeseries_bar`,
  `pie`, `big_number_total`). If a created chart renders oddly in Superset, compare
  its `params` against one built manually through the UI (via browser devtools on
  the `POST /api/v1/chart/` call) and adjust `_build_chart_params` in
  `mcp_server.py`.
