# Skills go here

Each skill is one directory holding a `SKILL.md`:

```
skills/<skill-name>/SKILL.md
```

with YAML frontmatter carrying `name` and `description` — the description is what
Claude matches against to decide whether to load the skill, so write it as
"use when …" plus the words a user would actually type.

```markdown
---
name: fte-reporting
description: Use when the user asks for an FTE / headcount report over
  fact_employee_allocation. Triggers on "FTE", "headcount", "báo cáo".
---

<the guidance itself>
```

This plugin (`vdt-bi`) is loaded **only** for the `skills` variant, via
`--plugin-dir` in `claude_gateway/gateway_server.py`. The `baseline` variant runs
without it, so anything added here is exactly what the A/B comparison measures.

After adding or editing a skill, rebuild so the image picks it up
(`docker compose up -d --build claude_gateway`) — the directory is COPYed at build
time, not mounted. Verify it loaded with:

```
docker compose exec -T claude_gateway claude plugin validate /app/claude_gateway/skills_plugin
```

This README is not a skill (only `SKILL.md` files are), so it is ignored at load
time and can stay here.
