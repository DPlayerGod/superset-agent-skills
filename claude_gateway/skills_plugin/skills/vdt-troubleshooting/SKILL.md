---
name: vdt-troubleshooting
description: Diagnose empty query results, SQL errors, chart tool failures and oversized responses from the superset-postgres MCP tools. Use when a tool returns an error, when a query comes back with zero rows, or when a created chart renders blank - lỗi, không có dữ liệu, rỗng, không hiển thị.
---

# vdt-troubleshooting

Use when a tool call fails or a result looks wrong. The goal is one targeted diagnostic, not a sequence of guesses.

## Always

- Zero rows is a diagnosis, not an answer. Never report "không có dữ liệu" until at least one cause below has been ruled out.
- An aggregate over no matching rows returns **one row of `0`/NULL**, not zero rows. `COUNT(...) = 0` or a NULL `SUM` is the same signal and gets the same treatment — a filter almost certainly missed.
- Retry at most **once**, and only after the correction is known. Re-running the same failing call is never the fix.
- State what actually failed in the user's terms. Do not paste raw stack traces or tool JSON into the chat panel.
- Never fabricate a plausible-looking number to paper over a failed query.

## Decision Rules

**Query returned zero rows, or an aggregate came back 0/NULL** — check in this order:

1. Value filters. `project_name`, `organization_name` and `employee_level` are exact-match text, and the sample values in the schema documentation are illustrative — they are not guaranteed to exist in the loaded data. Before reporting an empty result, run `SELECT DISTINCT <column> FROM fact_employee_allocation WHERE current_row_indicator = 'Y'` and either match the user's wording to a real value or tell them which values do exist.
2. Time filters. `working_month_year` is `MM/YYYY` (`'01/2025'`, not `'2025-01'`); `working_year` and `working_quarter` are text (`'2025'`, `'Q1'`), not integers.
3. `working_is_business_day = 1` combined with a weekend-only date range yields nothing by construction.
4. Only then report that the data genuinely has no matching rows.

**SQL error from `run_sql_readonly`** — the validator rejects anything that is not SELECT/CTE, so any INSERT/UPDATE/DELETE/DDL is refused by design; say so rather than rephrasing it. For a column or type error, call `describe_table("fact_employee_allocation")`, fix the name against the real schema, retry once.

**Result truncated at `row_limit`** — aggregate to a coarser grain or add a filter. Raising `row_limit` to dump more rows into a small chat panel is the wrong fix.

**`create_chart` succeeded but the chart renders blank** — almost always one of the `vdt-charting` rules: a bare column name in `metrics` instead of an aggregate expression, an unsupported `viz_type`, several metrics on `pie` / `big_number_total`, or a `time_range` that excludes the data. Fix with `update_chart` on the existing `chart_id`; do not create a second chart.

**`create_dataset` / `create_chart` / `create_dashboard` returned an HTTP error** — these write to Superset's REST API as a fixed service account. Report the failure and what was being attempted. Do not retry a write more than once: a partial retry can leave duplicate charts behind.

## Workflow Order

1. Classify: empty result, SQL error, truncation, blank chart, or write failure.
2. Run the single diagnostic named above for that class.
3. Apply the correction and retry once.
4. If it fails again, tell the user what was tried and what the blocker is. Stop there.
