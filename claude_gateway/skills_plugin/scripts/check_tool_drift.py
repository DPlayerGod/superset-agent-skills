#!/usr/bin/env python3
"""Checks the vdt-bi skills against the code they describe.

Ported from preset-io/agent-skills v0.4.3
(plugins/preset-mcp-skills/scripts/check-tool-inventory.py), which guards its skills
against drift in the Superset MCP service. The same failure mode applies here, plus
two that are specific to this gateway:

- These skills were adapted from a package written for Superset's *official* MCP
  service, whose 27 tools barely overlap with mcp_server.py's 7. Reintroducing an
  upstream tool name while editing would tell Claude to call something that does not
  exist, and nothing at runtime would say so - the turn would just fail.
- The gateway denies the Read tool (DISALLOWED_TOOLS in gateway_server.py), so a
  `## Retrieve` section pointing at references/*.md is unreachable by construction.

Run it by hand after editing a skill; it is deliberately not part of the Docker build.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PLUGIN_ROOT.parents[1]

MCP_SERVER = PROJECT_ROOT / "mcp_server.py"
GATEWAY_SERVER = PROJECT_ROOT / "claude_gateway" / "gateway_server.py"
MOCK_DATA_DOCS = PROJECT_ROOT / "docs" / "mock_data_docs.md"
SKILLS_DIR = PLUGIN_ROOT / "skills"

# Tools from the upstream Superset MCP service (preset-mcp-skills'
# references/tool-inventory.json). None of these exist in mcp_server.py, so naming one
# in a skill is always a mistake - except for the two whose names collide with a local
# tool, which are excluded here rather than flagged on every run.
UPSTREAM_ONLY_TOOLS = {
    "add_chart_to_existing_dashboard", "create_virtual_dataset", "execute_sql",
    "generate_bug_report", "generate_chart", "generate_dashboard", "generate_explore_link",
    "get_chart_data", "get_chart_info", "get_chart_preview", "get_chart_sql",
    "get_chart_type_schema", "get_dashboard_info", "get_database_info", "get_dataset_info",
    "get_instance_info", "get_schema", "health_check", "list_charts", "list_dashboards",
    "list_databases", "open_sql_lab_with_context", "query_dataset", "save_sql_query",
    "update_chart_preview",
}

# Only identifiers built from these stems are checked against the schema, so ordinary
# prose ("working days", "project scope") is not mistaken for a column reference.
COLUMN_LIKE = re.compile(r"\b(?:d_working|working|employee|project|organization|current_row)_[a-z0-9_]+\b")

FRONTMATTER_NAME = re.compile(r"^---\s*\nname:\s*(\S+)\s*$", re.MULTILINE)


def mcp_tools() -> set[str]:
    """Names of the @mcp.tool()-decorated functions in mcp_server.py."""
    tree = ast.parse(MCP_SERVER.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "tool"
    }


def allowed_tools() -> set[str]:
    """Tool names in gateway_server.py's ALLOWED_TOOLS comprehension."""
    tree = ast.parse(GATEWAY_SERVER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "ALLOWED_TOOLS" for t in node.targets):
            continue
        # ",".join(f"mcp__{...}__{tool}" for tool in ( ...names... ))
        for inner in ast.walk(node):
            if isinstance(inner, (ast.GeneratorExp, ast.ListComp)):
                iterable = inner.generators[0].iter
                if isinstance(iterable, (ast.Tuple, ast.List)):
                    return {
                        element.value
                        for element in iterable.elts
                        if isinstance(element, ast.Constant) and isinstance(element.value, str)
                    }
    return set()


def schema_columns() -> set[str]:
    """Column names from the attribute table in docs/mock_data_docs.md."""
    return set(re.findall(r"^\|\s*`([a-z0-9_]+)`\s*\|", MOCK_DATA_DOCS.read_text(encoding="utf-8"), re.MULTILINE))


def main() -> int:
    errors: list[str] = []

    tools = mcp_tools()
    if not tools:
        print("No @mcp.tool() functions found - has the decorator pattern in mcp_server.py changed?")
        return 1

    allowed = allowed_tools()
    if allowed != tools:
        errors.append(
            "gateway_server.py ALLOWED_TOOLS is out of sync with mcp_server.py: "
            f"missing from allowlist {sorted(tools - allowed) or '[]'}, "
            f"allowlisted but not defined {sorted(allowed - tools) or '[]'}"
        )

    columns = schema_columns()
    skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    if not skill_files:
        errors.append(f"No SKILL.md files under {SKILLS_DIR}")

    for path in skill_files:
        text = path.read_text(encoding="utf-8")
        label = path.relative_to(PLUGIN_ROOT).as_posix()

        match = FRONTMATTER_NAME.search(text)
        if not match:
            errors.append(f"{label}: no `name:` in frontmatter")
        elif match.group(1) != path.parent.name:
            errors.append(f"{label}: frontmatter name `{match.group(1)}` != directory `{path.parent.name}`")

        for name in sorted(UPSTREAM_ONLY_TOOLS & set(re.findall(r"\b[a-z_][a-z0-9_]*\b", text))):
            errors.append(f"{label}: references upstream Preset MCP tool `{name}`, which mcp_server.py does not define")

        for token in sorted(set(COLUMN_LIKE.findall(text)) - columns):
            errors.append(f"{label}: `{token}` is not a column in docs/mock_data_docs.md")

        if "## Retrieve" in text or "references/" in text:
            errors.append(f"{label}: uses progressive disclosure, but the gateway denies the Read tool")

    if errors:
        print(f"vdt-bi skill drift check failed ({len(errors)} problem(s)):")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"vdt-bi skill drift check passed: {len(skill_files)} skill(s), {len(tools)} MCP tool(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
