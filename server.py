#!/usr/bin/env python3
"""
PAAS GSC MCP — Google Search Console MCP server for the PAAS Poços Artesianos workflow.

Tools exposed:
    - gsc_list_sites              : list all GSC properties accessible to the service account
    - gsc_search_analytics        : flexible query of search analytics (clicks, impressions, CTR, position)
    - gsc_quick_wins              : ranked list of pages currently in positions 11-25 (page 2/top 3)
    - gsc_top_pages               : top N pages by clicks/impressions
    - gsc_top_queries             : top N queries by clicks/impressions
    - gsc_url_inspect             : inspect a URL's indexing status

Auth: service account JSON. Path provided via env var GSC_CREDENTIALS_PATH.
The service-account email must be added as a User in Search Console for each property
you want to query.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError as e:
    sys.stderr.write(
        f"[paas-gsc-mcp] Missing dependencies — install with:\n"
        f"  pip install mcp google-api-python-client google-auth\n"
        f"Original error: {e}\n"
    )
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

CRED_PATH = os.environ.get("GSC_CREDENTIALS_PATH", "").strip()
if not CRED_PATH or not os.path.isfile(CRED_PATH):
    sys.stderr.write(
        f"[paas-gsc-mcp] GSC_CREDENTIALS_PATH is unset or not a file: {CRED_PATH!r}\n"
    )
    sys.exit(1)

credentials = service_account.Credentials.from_service_account_file(
    CRED_PATH, scopes=SCOPES
)
gsc = build("searchconsole", "v1", credentials=credentials, cache_discovery=False)

mcp = FastMCP("paas-gsc")


def _default_dates(days: int) -> tuple[str, str]:
    end = date.today() - timedelta(days=2)
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def _format_error(err: Exception) -> str:
    if isinstance(err, HttpError):
        try:
            content = json.loads(err.content.decode("utf-8"))
            message = content.get("error", {}).get("message", str(err))
            return f"GSC API error ({err.status_code}): {message}"
        except Exception:
            return f"GSC API error: {err}"
    return f"Error: {err}"


def _service_account_email() -> str:
    try:
        with open(CRED_PATH) as f:
            data = json.load(f)
        return data.get("client_email", "(unknown)")
    except Exception:
        return "(unknown)"


@mcp.tool()
def gsc_list_sites() -> str:
    """List all GSC properties accessible to the service account."""
    try:
        resp = gsc.sites().list().execute()
        entries = resp.get("siteEntry", [])
        if not entries:
            return json.dumps({
                "sites": [],
                "hint": (
                    "No properties accessible. Add the service account email "
                    f"({_service_account_email()}) as a User in Search Console "
                    "for each property you want to query."
                ),
            }, indent=2, ensure_ascii=False)
        return json.dumps(entries, indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


@mcp.tool()
def gsc_search_analytics(
    site_url: str,
    days: int = 90,
    dimensions: list[str] | None = None,
    row_limit: int = 100,
    filter_query: str | None = None,
    filter_page: str | None = None,
    search_type: str = "web",
) -> str:
    """Flexible query of GSC search analytics."""
    try:
        start, end = _default_dates(days)
        body: dict[str, Any] = {
            "startDate": start,
            "endDate": end,
            "dimensions": dimensions or ["page", "query"],
            "rowLimit": min(max(row_limit, 1), 25000),
            "type": search_type,
        }
        filters = []
        if filter_query:
            filters.append({"dimension": "query", "operator": "contains", "expression": filter_query})
        if filter_page:
            filters.append({"dimension": "page", "operator": "contains", "expression": filter_page})
        if filters:
            body["dimensionFilterGroups"] = [{"filters": filters}]
        resp = gsc.searchanalytics().query(siteUrl=site_url, body=body).execute()
        return json.dumps(
            {"site": site_url, "period": f"{start} to {end}", "rows": resp.get("rows", [])},
            indent=2, ensure_ascii=False,
        )
    except Exception as e:
        return _format_error(e)


@mcp.tool()
def gsc_quick_wins(
    site_url: str,
    days: int = 90,
    min_position: float = 11.0,
    max_position: float = 25.0,
    min_impressions: int = 100,
    row_limit: int = 50,
) -> str:
    """Identify quick-win pages: ranking on page 2 or top of page 3 with meaningful impressions."""
    try:
        start, end = _default_dates(days)
        body = {
            "startDate": start,
            "endDate": end,
            "dimensions": ["page", "query"],
            "rowLimit": row_limit,
            "type": "web",
        }
        resp = gsc.searchanalytics().query(siteUrl=site_url, body=body).execute()
        rows = resp.get("rows", [])
        per_page: dict[str, dict[str, Any]] = {}
        for r in rows:
            page, query = r["keys"][0], r["keys"][1]
            if not (min_position <= r["position"] <= max_position):
                continue
            if r["impressions"] < min_impressions:
                continue
            existing = per_page.get(page)
            if existing is None or r["impressions"] > existing["impressions"]:
                per_page[page] = {
                    "page": page,
                    "top_query": query,
                    "position": round(r["position"], 1),
                    "impressions": r["impressions"],
                    "current_clicks": r["clicks"],
                    "ctr": round(r["ctr"], 4),
                    "potential_clicks_at_pos5": round(r["impressions"] * 0.10, 0),
                    "incremental_clicks": round(r["impressions"] * 0.10 - r["clicks"], 0),
                }
        ranked = sorted(per_page.values(), key=lambda x: x["incremental_clicks"], reverse=True)
        return json.dumps({
            "site": site_url,
            "period": f"{start} to {end}",
            "criteria": {"position_range": [min_position, max_position], "min_impressions": min_impressions},
            "quick_wins": ranked,
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


@mcp.tool()
def gsc_top_pages(site_url: str, days: int = 90, sort_by: str = "clicks", row_limit: int = 25) -> str:
    """Top N pages by clicks (default) or impressions."""
    try:
        start, end = _default_dates(days)
        body = {"startDate": start, "endDate": end, "dimensions": ["page"], "rowLimit": row_limit, "type": "web"}
        resp = gsc.searchanalytics().query(siteUrl=site_url, body=body).execute()
        rows = resp.get("rows", [])
        rows.sort(key=lambda r: r.get(sort_by, 0), reverse=True)
        formatted = [{
            "page": r["keys"][0],
            "clicks": r["clicks"],
            "impressions": r["impressions"],
            "ctr": round(r["ctr"], 4),
            "position": round(r["position"], 1),
        } for r in rows]
        return json.dumps(
            {"site": site_url, "period": f"{start} to {end}", "sort_by": sort_by, "pages": formatted},
            indent=2, ensure_ascii=False,
        )
    except Exception as e:
        return _format_error(e)


@mcp.tool()
def gsc_top_queries(
    site_url: str,
    days: int = 90,
    sort_by: str = "clicks",
    row_limit: int = 25,
    filter_page: str | None = None,
) -> str:
    """Top N queries by clicks or impressions, optionally filtered to a specific page."""
    try:
        start, end = _default_dates(days)
        body: dict[str, Any] = {
            "startDate": start, "endDate": end, "dimensions": ["query"], "rowLimit": row_limit, "type": "web",
        }
        if filter_page:
            body["dimensionFilterGroups"] = [
                {"filters": [{"dimension": "page", "operator": "contains", "expression": filter_page}]}
            ]
        resp = gsc.searchanalytics().query(siteUrl=site_url, body=body).execute()
        rows = resp.get("rows", [])
        rows.sort(key=lambda r: r.get(sort_by, 0), reverse=True)
        formatted = [{
            "query": r["keys"][0],
            "clicks": r["clicks"],
            "impressions": r["impressions"],
            "ctr": round(r["ctr"], 4),
            "position": round(r["position"], 1),
        } for r in rows]
        return json.dumps({
            "site": site_url, "period": f"{start} to {end}", "sort_by": sort_by,
            "filter_page": filter_page, "queries": formatted,
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


@mcp.tool()
def gsc_url_inspect(site_url: str, inspection_url: str) -> str:
    """Inspect a URL's indexing status."""
    try:
        resp = gsc.urlInspection().index().inspect(
            body={"siteUrl": site_url, "inspectionUrl": inspection_url}
        ).execute()
        return json.dumps(resp, indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


if __name__ == "__main__":
    mcp.run()
