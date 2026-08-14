---
name: vdt-fte-sql
description: Write correct SQL over fact_employee_allocation for FTE, headcount, allocation and trend questions, and run it with run_sql_readonly. Use when the user asks for numbers - FTE, headcount, số nhân sự, phân bổ, tỷ lệ, top dự án, theo tháng/quý/năm, xu hướng, so sánh. Do not use for creating charts or dashboards.
---

# vdt-fte-sql

Read-path skill: turn a business question into one correct SQL statement and run it with `run_sql_readonly`.

## Always

- `run_sql_readonly` accepts SELECT/CTE only. DDL/DML is rejected by the server no matter how the request is phrased.
- Filter `current_row_indicator = 'Y'` in every statement that reports current figures.
- **FTE ≠ headcount.** `SUM(project_allocated_hc)` is FTE (fractional, daily-spread, safe to sum at any grain). `COUNT(DISTINCT employee_id)` is headcount (people). Pick the one the user actually asked for; when the wording is ambiguous ("bao nhiêu nhân sự"), return headcount and mention the FTE figure alongside it.
- `working_month_year` is text `MM/YYYY`, and `working_quarter` is text `Q1`..`Q4` **without a year**. Never `ORDER BY` them directly and never group by quarter alone — they sort and collapse wrongly across years. Order by `MIN(d_working_date_key)`, and pair quarter with `working_year`.
- Add `AND working_is_business_day = 1` when the question is about actual working days or a daily-level trend. Leave it off for monthly/quarterly FTE totals, where non-business days contribute 0 anyway.
- `row_limit` defaults to 200 and is enforced server-side. For an aggregate that can exceed it, aggregate harder or pass a bigger `row_limit` deliberately — do not silently return a truncated list.
- For any table other than `fact_employee_allocation`, call `describe_table` before writing SQL.

## Decision Rules

- "Tổng FTE", "phân bổ", allocation → `SUM(project_allocated_hc)`.
- "Bao nhiêu người", "số nhân sự", headcount → `COUNT(DISTINCT employee_id)`.
- "Theo tháng" → group by `working_month_year`; "theo quý" → `working_year, working_quarter`; "theo năm" → `working_year`; daily trend → `working_date`.
- "Top N" → `ORDER BY <metric> DESC LIMIT N`.
- Question implies a chart rather than a number → hand off to `vdt-charting`.

## Recipes

FTE by project, one month:

```sql
SELECT project_name, SUM(project_allocated_hc) AS fte
FROM fact_employee_allocation
WHERE current_row_indicator = 'Y' AND working_month_year = '01/2025'
GROUP BY project_name
ORDER BY fte DESC;
```

Monthly FTE trend, correctly ordered:

```sql
SELECT working_month_year, SUM(project_allocated_hc) AS fte
FROM fact_employee_allocation
WHERE current_row_indicator = 'Y'
GROUP BY working_month_year
ORDER BY MIN(d_working_date_key);
```

Headcount by organization (people, not FTE):

```sql
SELECT organization_name, COUNT(DISTINCT employee_id) AS headcount
FROM fact_employee_allocation
WHERE current_row_indicator = 'Y'
GROUP BY organization_name
ORDER BY headcount DESC;
```

Quarterly FTE — quarter must carry its year:

```sql
SELECT working_year, working_quarter, SUM(project_allocated_hc) AS fte
FROM fact_employee_allocation
WHERE current_row_indicator = 'Y'
GROUP BY working_year, working_quarter
ORDER BY working_year, working_quarter;
```

Employees spread across several projects:

```sql
SELECT employee_id, employee_full_name, COUNT(DISTINCT project_name) AS projects,
       SUM(project_allocated_hc) AS fte
FROM fact_employee_allocation
WHERE current_row_indicator = 'Y'
GROUP BY employee_id, employee_full_name
HAVING COUNT(DISTINCT project_name) > 1
ORDER BY projects DESC, fte DESC;
```

FTE mix by level within an organization:

```sql
SELECT employee_level, SUM(project_allocated_hc) AS fte
FROM fact_employee_allocation
WHERE current_row_indicator = 'Y' AND organization_name = 'Digital Transformation'
GROUP BY employee_level
ORDER BY fte DESC;
```

## Workflow Order

1. Decide FTE vs headcount, and the time grain.
2. Build one statement from the recipes above; adapt rather than invent column names.
3. Run `run_sql_readonly` once.
4. Answer from `rows`. Report figures as short `name: value` bullets, rounding FTE to 2 decimals.
5. Zero rows — or an aggregate that came back `0`/NULL — is a finding, not an answer. Go to `vdt-troubleshooting` before reporting it.
