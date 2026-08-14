# vdt-bi skills

Each skill is one directory holding a `SKILL.md`:

```
skills/<skill-name>/SKILL.md
```

with YAML frontmatter carrying `name` (must equal the directory name) and
`description` — the description is what Claude matches against to decide whether to
load the skill, so write it as "use when …" plus the words a user would actually
type. Users here chat in Vietnamese, so descriptions carry triggers in both
languages.

## What is here

| Skill | Covers |
|---|---|
| `vdt-bi` | Root routing, read/write boundary, no-fabrication rules. |
| `vdt-fte-sql` | SQL over `fact_employee_allocation`: FTE vs headcount, time grains, ready recipes. |
| `vdt-charting` | `create_dataset` → `create_chart` / `update_chart`, `viz_type` and `params` constraints. |
| `vdt-dashboards` | `create_dashboard` assembly and its chart-reattachment behaviour. |
| `vdt-troubleshooting` | Empty results, SQL errors, truncation, blank charts, write failures. |

See [../AGENTS.md](../AGENTS.md) for routing and for where this structure was ported
from.

## Two rules when editing

1. **Self-contained only.** No `references/*.md`, no `## Retrieve` section. The
   gateway denies the `Read` tool (`DISALLOWED_TOOLS` in
   `claude_gateway/gateway_server.py`), so a progressive-disclosure link is
   unreachable at runtime.
2. **Do not restate the system prompt.** `role_prompt.md` + `docs/mock_data_docs.md`
   are loaded for *both* A/B variants. Anything copied from them shows up in
   `baseline` too and cancels out of the comparison.

This plugin (`vdt-bi`) is loaded **only** for the `skills` variant, via
`--plugin-dir` in `claude_gateway/gateway_server.py`. The `baseline` variant runs
without it, so what is in here is exactly what the A/B comparison measures.

## After editing

Check the skills against the code they describe, then rebuild — the directory is
COPYed at build time, not mounted:

```
python3 claude_gateway/skills_plugin/scripts/check_tool_drift.py
docker compose up -d --build claude_gateway
docker compose exec -T claude_gateway claude plugin validate /app/claude_gateway/skills_plugin
```

`check_tool_drift.py` catches the failures that are otherwise silent at runtime: a
skill naming a tool `mcp_server.py` does not define, a misspelled column, a
frontmatter `name` that no longer matches its directory, an `ALLOWED_TOOLS`
allowlist that drifted from the MCP server, and any reintroduced `references/` link.

This README is not a skill (only `SKILL.md` files are), so it is ignored at load
time and can stay here.
