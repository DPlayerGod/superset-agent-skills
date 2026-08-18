# Minimal Rollout Guide: Claude Code Gateway + MCP + Superset

## 1) Start infrastructure

The gateway authenticates as a Claude Pro/Max account rather than an API key, so
usage bills against that account's subscription quota instead of pay-per-token API
usage. Generate a long-lived OAuth token once, on any machine with the Claude Code
CLI logged into the account you want billed:

    claude login         # if not already logged in
    claude setup-token   # prints a token

Set that token before starting (never commit it):

export CLAUDE_CODE_OAUTH_TOKEN=...

Or create a `.env` file in the project root (do not commit it) with:

CLAUDE_CODE_OAUTH_TOKEN=...

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

There is nothing to configure manually. Behind the scenes (see
[architecture.md](architecture.md) for the full component/flow breakdown):

- Superset's browser-side panel (`superset/static/vdt-ai-chat.js`) POSTs to
  Superset's own backend at `/api/v1/vdt-ai-chat/query`.
- Superset's backend (`superset/superset_config.py`) forwards the authenticated
  request to `http://claude_gateway:8090/api/v1/agent/query`.
- `claude_gateway/gateway_server.py` runs `claude -p` (Claude Code CLI, headless
  mode) with an MCP config (`claude_gateway/mcp_servers.json.template`, rendered
  at container start) pointing at `mcp_server.py`, which exposes 7 tools:
  - Read-only: `list_datasets`, `describe_table`, `run_sql_readonly`
  - Write (Superset REST API): `create_dataset`, `create_chart`, `update_chart`,
    `create_dashboard`
- The gateway resumes the same Claude conversation across turns using the
  `session_id` the browser already generates per page load, so multi-turn
  context is preserved.

## 4) Connect Superset workflow

Flow:
- User asks a question in the Superset AI Agent panel.
- Superset proxies the authenticated request to the internal Claude Gateway.
- Claude decides which MCP tools to call (read data, and/or create a
  dataset/chart/dashboard), then answers in the panel.
- If Claude created a chart or dashboard, the panel renders it inline as a live
  iframe under the answer, plus a link that opens it in Superset.

## 4b) What the chat panel can do

- **Live preview.** Every chart/dashboard the agent creates or updates in a turn
  is embedded under the answer, in creation order. Each preview has a
  **Preview** tab (Superset's `standalone` view - just the chart) and an
  **Explore** tab (the full editable page); switching to Explore maximizes the
  panel, since the full page does not fit the compact width.
- **Multiple conversations.** `+` starts a new one, `☰` lists the saved ones
  (up to 20, newest first) and switches between them, `⤢` maximizes the panel.
  Each conversation keeps its own Claude session id, so a page reload continues
  the same conversation rather than quietly starting a new one - which is what
  happened before, when the session id was regenerated on every page load.
- **Timing.** Each answer carries the gateway-measured end-to-end time and the
  number of MCP tool calls that turn took (`⏱ 3.2s · 2 tool`).
- **Baseline vs Agent Skills.** New conversations are started as either
  `baseline` or `skills` (see §6). To compare the two, open one conversation of
  each variant and ask the same question.
- **Sending.** `Enter` sends, `Shift+Enter` inserts a newline. An empty
  conversation offers a few starter questions as clickable chips.

## 5) Notes

- Only read-only SELECT/CTE SQL is allowed via `run_sql_readonly`; DDL and DML are
  rejected by `mcp_server.py`'s validator regardless of what the model asks for.
- The write tools (`create_dataset`/`create_chart`/`update_chart`/`create_dashboard`) authenticate
  to Superset's REST API as a **fixed service account** (`SUPERSET_ADMIN_USERNAME`/
  `SUPERSET_ADMIN_PASSWORD`, defaulting to the bootstrapped `admin`/`admin`
  account) — charts/dashboards created by the agent are owned by that account, not
  by whichever Superset user is chatting. This is an accepted tradeoff for this
  rollout, not per-user identity forwarding.
- `SUPERSET_PUBLIC_URL` (default `http://localhost:8088`) is the base URL used for
  anything the **browser** loads - preview iframes and "open in Superset" links.
  It exists because `SUPERSET_URL` (`http://superset:8088`) is a Docker-internal
  hostname: correct for the gateway's REST calls, unreachable from a browser.
  Anyone deploying somewhere other than localhost must override it.
- Previews are plain same-origin iframes: the panel is served by Superset, so the
  viewer's existing session cookie authenticates them, and no Celery/Redis
  thumbnail worker is needed (`THUMBNAILS` is off in this stack). Verified on this
  build: the embedded routes answer `200` with `X-Frame-Options: SAMEORIGIN` and no
  `frame-ancestors` in the CSP, so same-origin framing is allowed as-is.
- `vdt-ai-chat.js`/`.css` are served with `Cache-Control: no-cache` (set in
  `superset_config.py`), not Superset's default one-year `max-age`. The filenames
  never change, so a year-long cached copy would keep running old panel code
  through any number of rebuilds - the symptom being "I rebuilt but the chat box
  looks exactly the same". `no-cache` still caches; it just revalidates, and the
  ETag makes the normal response a 304. A browser that cached a copy *before* this
  change needs one hard reload (Ctrl+Shift+R) to let go of it.
- The superset container does **not** reinstall `psycopg2-binary` at startup
  anymore; it is baked into the image at build time. The old entrypoint step made
  every restart depend on reaching pypi.org, and Superset failed to start at all
  when the container had no outbound internet.
- `create_chart`'s `params` payload is a best-effort mapping for a curated set of
  `viz_type`s (`table`, `echarts_timeseries_line`, `echarts_timeseries_bar`,
  `pie`, `big_number_total`). If a created chart renders oddly in Superset, compare
  its `params` against one built manually through the UI (via browser devtools on
  the `POST /api/v1/chart/` call) and adjust `_build_chart_params` in
  `mcp_server.py`.

## 6) Variants and Plugin Marketplace

Each request carries a `variant` (`baseline` or `skills`), defaulting to `baseline`,
and the response echoes it back in the `timing` object. Since 2026-08-18, both
variants run the same exact CLI argv — `variant` is **semantic only**, used for UI
labeling (thread title, badge in message history) and logging/metrics.

| Variant    | Intent |
|------------|----|
| `baseline` | Default; no special labeling. |
| `skills`   | Conversation marked as "using skills mode" — a historical label from when this controlled plugin loading. |

The old custom skill plugin (`claude_gateway/skills_plugin`) was removed on
2026-08-18 after A/B measurement showed its unique value had already been migrated
to shared code paths (`mcp_server.py` docstrings, `role_prompt.md`). See
[ab_results.md](ab_results.md) for the full experiment writeup.

**Dev-time plugin marketplace:** A project-wide marketplace is registered in
`.claude/settings.json`, sourcing plugins from
[preset-io/agent-skills](https://github.com/preset-io/agent-skills) on GitHub. This
marketplace is available to Claude Code when working in this repo directory, for
reference and exploration — none of its plugins are auto-enabled (3 plugins exist;
2 are Preset Cloud–specific, 1 targets Superset's official MCP service — not this
project's custom `mcp_server.py`). A developer can run `/plugin install <name>@preset-agent-skills`
locally if curious.

A conversation's variant badge is fixed when the thread is created and cannot be
switched mid-thread: the gateway resumes it with `--resume`, and changing labels
mid-transcript is confusing. The variant field persists in browser storage
(`localStorage`) so reloads preserve it.
