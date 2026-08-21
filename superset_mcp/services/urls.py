"""Browser-facing URLs for charts and dashboards.

Returned as plain dicts on purpose: every caller splats them straight into a
Result model whose `ChartUrlFields`/`DashboardUrlFields` mixin declares exactly
these three keys, keeping `embed_url` at the top level where the gateway looks
for it.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from superset_mcp.config import (
    DASHBOARD_STANDALONE,
    EXPLORE_STANDALONE,
    SUPERSET_PUBLIC_URL,
    SUPERSET_URL,
)
from superset_mcp.services.ids import parse_id


def chart_urls(chart_id: int | str) -> dict[str, Any]:
    c_id = parse_id(chart_id)
    base = f"{SUPERSET_PUBLIC_URL}/explore/?slice_id={c_id}"
    return {"type": "chart", "url": base, "embed_url": f"{base}&standalone={EXPLORE_STANDALONE}"}


def dashboard_urls(dashboard_id: int | str) -> dict[str, Any]:
    d_id = parse_id(dashboard_id)
    base = f"{SUPERSET_PUBLIC_URL}/superset/dashboard/{d_id}/"
    return {
        "type": "dashboard",
        "url": base,
        "embed_url": f"{base}?standalone={DASHBOARD_STANDALONE}",
    }


def explore_preview_url(
    sess: requests.Session, dataset_id: int, chart_name: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Renders a chart from unsaved form_data via Superset's own ExploreFormData
    API - the same mechanism Superset's Explore UI uses for its URL while you are
    still building a chart, before you hit Save. This stores the form_data
    server-side under a short-lived key so the Explore page can fetch it back;
    it does NOT create a chart object, so nothing here touches Superset's chart
    table. (A raw `form_data=<json>` query param on `/explore/` is not read by
    this build's frontend - only `form_data_key` is - so the key round-trip
    below is required, not optional.)
    """
    form_data = {"datasource": f"{dataset_id}__table", "slice_name": chart_name, **params}
    resp = sess.post(
        f"{SUPERSET_URL}/api/v1/explore/form_data",
        json={
            "datasource_id": dataset_id,
            "datasource_type": "table",
            "form_data": json.dumps(form_data),
        },
        timeout=15,
    )
    resp.raise_for_status()
    key = resp.json()["key"]
    base = f"{SUPERSET_PUBLIC_URL}/explore/?form_data_key={key}"
    return {"type": "chart", "url": base, "embed_url": f"{base}&standalone={EXPLORE_STANDALONE}"}


def explore_link(dataset_id: int) -> str:
    return f"{SUPERSET_PUBLIC_URL}/explore/?dataset_id={dataset_id}&dataset_type=physical"


def sql_lab_url(sql: str) -> str:
    import urllib.parse

    return f"{SUPERSET_PUBLIC_URL}/sqllab/?sql={urllib.parse.quote(sql)}"
