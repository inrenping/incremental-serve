"""
MCP (Model Context Protocol) 服务器入口。

配置 MCP 服务器并注册所有工具。
支持 stdio / SSE 两种传输模式。
SSE 模式内置 OAuth 2.0 Authorization Code Flow。
"""

import argparse
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from app.mcp.auth_provider import MCPAuthProvider
from app.mcp.tools.activity_data import get_activities
from app.mcp.tools.activity_stats import get_activity_stats
from app.mcp.tools.heart_rate import get_heart_rate_data

logger = logging.getLogger(__name__)

# ── MCP Server ─────────────────────────────────────────────────────────

mcp = FastMCP(
    "incremental-activities",
    auth=AuthSettings(
        issuer_url=AnyHttpUrl("https://i.incremental.icu/mcp"),
        resource_server_url=AnyHttpUrl("https://i.incremental.icu/mcp"),
        required_scopes=["read"],
    ),
    auth_server_provider=MCPAuthProvider(),
)

# ── Tool Registrations ─────────────────────────────────────────────────


@mcp.tool(
    name="get_activities",
    description="""Query activity records for a user within a date range.

Returns detailed data including distance, duration, heart rate,
pace, calories, elevation gain, etc. Supports sport type filtering
and pagination.

Use this when you need specific activity details, not aggregated stats.
""",
)
def get_activities_tool(
    user_id: int,
    start_date: str,
    end_date: str,
    sport_types: str | None = None,
    page_size: int = 50,
    page_count: int = 1,
) -> dict:
    """
    Parameters
    ----------
    user_id : int
        The user's ID in the system.
    start_date : str
        Start date (inclusive), format YYYY-MM-DD.
    end_date : str
        End date (inclusive), format YYYY-MM-DD.
    sport_types : str, optional
        Optional sport type filter. Comma separated, e.g. "running,cycling".
    page_size : int, optional
        Items per page (default 50, max 200).
    page_count : int, optional
        Page number (default 1).
    """
    return get_activities(
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        sport_types=sport_types,
        page_size=page_size,
        page_count=page_count,
    )


@mcp.tool(
    name="get_activity_stats",
    description="""Query aggregated activity statistics for a user within a date range.

Returns grouped statistics (by day/week/month) including total distance,
duration, count, average heart rate, calories, elevation gain, and pace.
Also returns overall totals for the entire period.

Use this when you need summarized trends rather than individual activities.
""",
)
def get_activity_stats_tool(
    user_id: int,
    start_date: str,
    end_date: str,
    group_by: str = "month",
    sport_types: str | None = None,
) -> dict:
    """
    Parameters
    ----------
    user_id : int
        The user's ID in the system.
    start_date : str
        Start date (inclusive), format YYYY-MM-DD.
    end_date : str
        End date (inclusive), format YYYY-MM-DD.
    group_by : str, optional
        Aggregation granularity: "day", "week", or "month" (default "month").
    sport_types : str, optional
        Optional sport type filter. Comma separated, e.g. "running,cycling".
    """
    return get_activity_stats(
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        group_by=group_by,
        sport_types=sport_types,
    )


@mcp.tool(
    name="get_heart_rate_data",
    description="""Query daily heart rate data for a user within a date range.

Returns daily summaries (max, min, resting heart rate) and optionally
includes per-minute heart rate sampling details.
Heart rate data is sourced from Garmin devices and synced daily.

Use this when you need heart rate trends, resting heart rate,
or detailed heart rate curves for specific dates.
""",
)
def get_heart_rate_data_tool(
    user_id: int,
    start_date: str,
    end_date: str,
    include_details: bool = True,
) -> dict:
    """
    Parameters
    ----------
    user_id : int
        The user's ID in the system.
    start_date : str
        Start date (inclusive), format YYYY-MM-DD.
    end_date : str
        End date (inclusive), format YYYY-MM-DD.
    include_details : bool, optional
        Whether to include per-minute heart rate sampling data (default True).
        Set to False to only get daily summary metrics.
    """
    return get_heart_rate_data(
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        include_details=include_details,
    )


# ── Entry Point ────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP Server for Incremental")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (SSE mode)")
    parser.add_argument(
        "--port", type=int, default=8001, help="Port to bind (SSE mode)"
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Disable JWT Bearer auth (SSE mode, for development only)",
    )

    args = parser.parse_args()

    if args.transport == "sse" and args.no_auth:
        logger.warning("Auth is disabled via --no-auth flag")

    if args.transport == "sse":
        import uvicorn

        uvicorn.run(
            mcp.sse_app(),
            host=args.host,
            port=args.port,
        )
    else:
        mcp.run(transport="stdio")
