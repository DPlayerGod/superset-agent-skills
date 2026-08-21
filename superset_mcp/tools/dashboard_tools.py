"""MCP wrappers for the Superset dashboard tools."""

from __future__ import annotations

from typing import Any

from superset_mcp.app import mcp
from superset_mcp.logic import dashboard_logic
from superset_mcp.models.dashboard import (
    AddChartsToDashboardParams,
    CreateDashboardParams,
    GetDashboardInfoParams,
    ListDashboardsParams,
    UpdateDashboardParams,
)


@mcp.tool()
def update_dashboard(
    dashboard_id: int | str,
    dashboard_title: str | None = None,
    color_scheme: str | None = None,
    label_colors: dict[str, str] | None = None,
    clear_label_colors: bool = False,
) -> dict[str, Any]:
    """Updates a dashboard's properties, color palette (color_scheme), or label_colors map.

    dashboard_id: numeric id (or slug/UUID) of the target dashboard.
    dashboard_title: optional new title for the dashboard.
    color_scheme: optional palette name (e.g. 'supersetColors', 'd3Category10', 'googleCategory20c').
    label_colors: optional dict mapping metric/series labels to hex colors, e.g. {'HC Org': '#E74C3C'}.
    clear_label_colors: set to True to remove all color overrides from the dashboard metadata, allowing charts to render with their own individual colors.
    """
    return dashboard_logic.update_dashboard(
        UpdateDashboardParams(
            dashboard_id=dashboard_id,
            dashboard_title=dashboard_title,
            color_scheme=color_scheme,
            label_colors=label_colors,
            clear_label_colors=clear_label_colors,
        )
    ).model_dump()



@mcp.tool()
def create_dashboard(
    dashboard_title: str, chart_ids: list[int | str], confirm_token: str | None = None
) -> dict[str, Any]:
    """Creates a Superset dashboard and attaches the given charts to it. Two calls,
    with the user's answer in between - nothing is saved to Superset until they
    confirm.

    Call it WITHOUT confirm_token first: no dashboard is created, and you get back
    a preview of which charts will be attached - and which existing dashboard each
    would be moved off of - plus a confirm_token. Attaching sends
    {"dashboards": [new_id]} per chart, which REPLACES that chart's dashboard
    membership rather than adding to it - a chart already on another dashboard is
    moved off it, not copied. Show this to the user and only pass chart_ids they
    are willing to move. Only after they say yes, call again with the SAME
    arguments plus that confirm_token - a token issued in the current turn is
    refused, so the two calls cannot be chained inside one answer.

    To put a chart on a dashboard that already exists, use add_charts_to_dashboard
    instead: it keeps the chart's current memberships.

    dashboard_title: the dashboard name the user will see.
    chart_ids: numeric chart ids to attach, e.g. [7, 8]. From list_charts or from
    create_chart's result.
    confirm_token: leave unset on the first call; pass back the returned token on
    the second.
    """
    return dashboard_logic.create_dashboard(
        CreateDashboardParams(
            dashboard_title=dashboard_title,
            chart_ids=chart_ids,
            confirm_token=confirm_token,
        )
    ).model_dump()


@mcp.tool()
def add_charts_to_dashboard(
    dashboard_id: int | str, chart_ids: list[int | str]
) -> dict[str, Any]:
    """Adds charts to an EXISTING dashboard, keeping their current memberships.

    This is the safe way to put a chart on a dashboard: unlike create_dashboard, a
    chart already shown elsewhere stays there too. Charts already on this dashboard
    are reported back under already_present and are not touched.

    dashboard_id: numeric id of the target dashboard, from list_dashboards.
    chart_ids: numeric chart ids to attach, e.g. [7, 8].
    """
    return dashboard_logic.add_charts_to_dashboard(
        AddChartsToDashboardParams(dashboard_id=dashboard_id, chart_ids=chart_ids)
    ).model_dump()


@mcp.tool()
def get_dashboard_info(dashboard_id: int | str) -> dict[str, Any]:
    """Gets complete details for a specific dashboard by its ID (integer ID or UUID string).

    Returns dashboard title, slug, published status, total charts_count, and full
    metadata for ALL attached charts (including viz_type, metrics, groupby, time_range,
    row_limit, and pre-generated SQL queries). Do NOT call get_chart or get_chart_sql
    after calling get_dashboard_info, because all chart specs and SQL queries are already
    included in the 'charts' array returned by this tool.

    dashboard_id: an integer id (e.g. 3), a numeric string, a dashboard UUID, an
    embedded-dashboard UUID, or a slug. When it matches nothing, the lowest
    dashboard id is returned instead of an error - this is what the chat panel
    relies on when it only knows the embed UUID.
    """
    return dashboard_logic.get_dashboard_info(
        GetDashboardInfoParams(dashboard_id=dashboard_id)
    ).model_dump()


@mcp.tool()
def list_dashboards() -> dict[str, Any]:
    """Lists every dashboard in Superset with its id, title, slug and published state.

    Call it when the user names a dashboard and you need its id, e.g. before
    add_charts_to_dashboard or get_dashboard_info.
    """
    return dashboard_logic.list_dashboards(ListDashboardsParams()).model_dump()
