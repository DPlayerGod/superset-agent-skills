---
name: vdt-charting
description: Create and edit Superset charts through create_dataset, create_chart and update_chart, including the viz_type and params rules this MCP server enforces. Use when the user says vẽ, tạo, dựng biểu đồ, chart, đồ thị, trực quan hoá, or asks to đổi/sửa/rename an existing chart. Do not use for plain number questions.
---

# vdt-charting

Write-path skill for charts. The `create_chart` / `update_chart` tools map a small set of arguments onto Superset's `params` JSON, and that mapping has rules the tool schema does not state.

## Always

- A chart needs a `dataset_id`. Call `create_dataset(table_name="fact_employee_allocation")` first — it is get-or-create, so calling it again on an existing dataset is safe and cheap.
- `metrics` are **SQL aggregate expressions as strings**, not column names: `["SUM(project_allocated_hc)"]`, `["COUNT(DISTINCT employee_id)"]`. A bare `"project_allocated_hc"` renders an unaggregated, wrong chart.
- `viz_type` accepts exactly five values: `table`, `echarts_timeseries_line`, `echarts_timeseries_bar`, `pie`, `big_number_total`. Anything else is not mapped and will render blank.
- `pie` and `big_number_total` use **one metric only** — `metrics[0]` is kept and the rest are dropped. To compare several metrics use `table` or `echarts_timeseries_bar`.
- `big_number_total` ignores `groupby` entirely. Do not pass dimensions to it.
- For `echarts_timeseries_line` / `echarts_timeseries_bar`, **`groupby[0]` becomes the x-axis** and the remainder become the series split. Order matters: `groupby=["working_month_year", "project_name"]` gives months across the x-axis split by project; reversing it plots projects on the x-axis.
- `time_range` defaults to `"No filter"`. Leave it there unless the user named a period — the temporal column is `working_date`, and a mismatched range silently empties the chart.
- Report success from the tool response, and always hand back the returned `url`.

## Decision Rules

- Trend over time ("theo tháng", "xu hướng", "qua các quý") → `echarts_timeseries_line`, `groupby[0]` = the time column.
- Comparison across categories ("theo dự án", "theo phòng ban", "so sánh") → `echarts_timeseries_bar`, or `table` when the user wants exact figures.
- Share of a whole ("tỷ trọng", "cơ cấu", "phần trăm", "biểu đồ tròn") → `pie`, single metric.
- One headline figure ("tổng FTE hiện tại") → `big_number_total`, single metric, no groupby.
- Detailed listing ("bảng", "chi tiết", several metrics side by side) → `table`.
- The user wants to change a chart that already exists → `update_chart`, never a second `create_chart`.

## Editing an existing chart

`update_chart` inherits every argument left unset from the chart's current `params`. Pass **only** what changes:

- Rename → `update_chart(chart_id=..., chart_name="...")`.
- Switch to pie → `update_chart(chart_id=..., viz_type="pie")`; the server keeps the first existing metric and drops the rest on its own.
- Change the measure → `update_chart(chart_id=..., metrics=["COUNT(DISTINCT employee_id)"])`.

Passing the full argument set on an edit re-specifies fields the user did not ask to touch and is how an edit accidentally resets a chart. Keep the call minimal.

## Workflow Order

1. If unsure the figures are meaningful, check them first with `vdt-fte-sql` — a chart built on a wrong metric costs a full rebuild.
2. `create_dataset` to get `dataset_id`.
3. Pick `viz_type` from the decision rules, then build `metrics` and `groupby` under the constraints above.
4. `create_chart` (new) or `update_chart` (existing, changed fields only).
5. Reply with a one-line description plus the returned `url`.
