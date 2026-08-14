---
name: vdt-dashboards
description: Assemble Superset dashboards from charts with create_dashboard, including its chart-reattachment behaviour. Use when the user asks for a dashboard, bảng điều khiển, trang tổng hợp, or wants several charts grouped onto one page. Do not use for a single chart.
---

# vdt-dashboards

Write-path skill for dashboards. `create_dashboard(dashboard_title, chart_ids)` creates the dashboard, then attaches each chart to it.

## Always

- Charts must exist first. Build each one via `vdt-charting`, collect the returned `chart_id` values, then call `create_dashboard` **once** with the full list.
- `create_dashboard` attaches by sending `{"dashboards": [new_id]}` to each chart — that **replaces** the chart's dashboard membership. A chart already living on another dashboard is moved, not copied.
- There is no tool to add a chart to an existing dashboard. If the user asks for that, say so plainly and offer to build a new dashboard containing the charts they want.
- Never invent a `chart_id`. Only ids returned by `create_chart` / `update_chart` in this conversation are valid.
- Hand back the `url` from the response.

## Decision Rules

- "Dashboard cho ..." with no charts built yet → create the charts first, then one `create_dashboard`.
- User names charts already created this session → reuse those `chart_id`s directly.
- User wants to add to an existing dashboard → not supported; explain and propose a new dashboard.
- Only one chart requested → stay in `vdt-charting`; a dashboard adds nothing.

## Composition guidance

A useful FTE dashboard is usually three to five charts, not one of everything:

- One `big_number_total` for the headline (total FTE or headcount).
- One `echarts_timeseries_line` for the trend over `working_month_year`.
- One `echarts_timeseries_bar` or `pie` for the breakdown by `project_name` or `organization_name`.
- Optionally one `table` for the detail rows.

## Workflow Order

1. Decide the chart list before building anything, and confirm it with the user if they asked for something vague ("dashboard nhân sự").
2. Create each chart, keeping its `chart_id`.
3. Call `create_dashboard(dashboard_title, chart_ids)` once.
4. Reply with the dashboard `url` and a one-line list of what is on it.
