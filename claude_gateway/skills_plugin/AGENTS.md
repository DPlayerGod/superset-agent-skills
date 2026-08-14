# vdt-bi Skills

Domain skills for the `super_dplayergod` Daily FTE assistant, loaded only by the
`skills` A/B variant via `--plugin-dir` (see `claude_gateway/gateway_server.py`).

All work goes through the `superset-postgres` MCP server (`mcp_server.py`) and its
seven tools. These skills are workflow and safety guidance; `mcp_server.py` is the
source of truth for tool names, arguments, and what the server will actually accept.

## Skill Routing

- `skills/vdt-bi/SKILL.md` — root routing, read/write boundary, no-fabrication rules.
- `skills/vdt-fte-sql/SKILL.md` — SQL over `fact_employee_allocation`: FTE vs headcount, time grains, ready recipes.
- `skills/vdt-charting/SKILL.md` — `create_dataset` → `create_chart` / `update_chart`, `viz_type` and `params` constraints.
- `skills/vdt-dashboards/SKILL.md` — `create_dashboard` assembly and its chart-reattachment behaviour.
- `skills/vdt-troubleshooting/SKILL.md` — empty results, SQL errors, truncation, blank charts, write failures.

## Constraints Specific To This Gateway

- **No `references/` files.** The gateway denies the `Read` tool
  (`DISALLOWED_TOOLS` in `gateway_server.py`), so progressive disclosure does not
  work here. Every skill must be self-contained in its `SKILL.md`.
- **Do not restate the system prompt.** `claude_gateway/role_prompt.md` and
  `docs/mock_data_docs.md` are concatenated into `system_prompt.md` and loaded for
  *both* A/B variants — the tool list, the `current_row_indicator = 'Y'` rule, the
  additivity of `project_allocated_hc`, and the answer formatting rules already
  live there. Content duplicated from them adds nothing the experiment can measure.
- **Descriptions carry Vietnamese triggers.** Users chat in Vietnamese; the
  `description:` frontmatter is what gets matched, so it must contain the words a
  user would actually type in both languages.

## Provenance

Structure and conventions are ported from
[`preset-io/agent-skills`](https://github.com/preset-io/agent-skills) v0.4.3
(package `preset-mcp-skills`, Apache-2.0) — the `## Always` / `## Decision Rules` /
`## Workflow Order` skill shape, the narrow-skill routing model, and the tool-drift
check under `scripts/`.

The skill *content* is not ported. That package targets Superset's official MCP
service (27 tools: `execute_sql`, `generate_chart`, `get_schema`, `query_dataset`, …),
which shares almost no tool surface with this project's seven-tool
`mcp_server.py`. Its instructions were rewritten against the local tools rather
than copied.

| Upstream skill | Local counterpart |
|---|---|
| `preset-mcp` | `vdt-bi` |
| `preset-mcp-sqllab` | `vdt-fte-sql` |
| `preset-mcp-visualization` | `vdt-charting` |
| `preset-mcp-dashboard` | `vdt-dashboards` |
| `preset-mcp-troubleshooting` | `vdt-troubleshooting` |
| `scripts/check-tool-inventory.py` | `scripts/check_tool_drift.py` |
