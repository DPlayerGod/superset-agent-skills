---
name: vdt-bi
description: Route Daily FTE / resource-allocation requests in the Superset AI panel to the right superset-postgres MCP tool and the right vdt skill. Use when the user asks anything about fact_employee_allocation - FTE, headcount, nhân sự, phân bổ, dự án, báo cáo, biểu đồ, chart, dashboard. Do not use for work outside this Superset stack.
---

# vdt-bi

Root routing skill for the `super_dplayergod` BI assistant. Pick the narrowest skill below, then follow it.

## Always

- Every number comes from a tool call. Never estimate, never carry a figure over from an earlier turn without re-querying when the question changed.
- Every URL comes from a tool response (`url` / `embed_url`). Never assemble a Superset URL by hand.
- Read tools (`list_datasets`, `describe_table`, `run_sql_readonly`) are free to use whenever they help answer.
- Write tools (`create_dataset`, `create_chart`, `update_chart`, `create_dashboard`) run **only** on an explicit request to build or change something. A question phrased as "how many / bao nhiêu / cho tôi xem số liệu" is a read, not a chart request.
- One question, one pass: resolve the schema, run the query, answer. Do not chain speculative tool calls hoping one lands.

## Decision Rules

- Numbers, totals, trends, comparisons, "bao nhiêu", "top", "theo tháng/quý/năm" → `vdt-fte-sql`.
- "Vẽ / tạo / dựng / build / chart / biểu đồ", or changing an existing chart ("đổi", "sửa", "rename") → `vdt-charting`.
- "Dashboard", "bảng điều khiển", or grouping several charts into one page → `vdt-dashboards`.
- A tool returned an error, or a query came back with zero rows → `vdt-troubleshooting`.
- Question about a table other than `fact_employee_allocation`: `list_datasets` then `describe_table` first; never guess a schema.

## Workflow Order

1. Classify the request as read, build, or fix.
2. Load the matching skill above.
3. Call tools; base the answer only on what came back.
4. If a chart or dashboard was created, end with the URL the tool returned.
