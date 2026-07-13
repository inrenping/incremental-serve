"""
MCP Server for Incremental Serv — Activity Data Tools.

Exposes activity queries as MCP tools that can be consumed by
any MCP client (Claude Desktop, Cursor, Trae IDE, etc.).

Authentication
--------------
- stdio mode: no authentication (local usage)
- SSE mode: JWT Bearer token authentication via --require-auth (default: on)

Usage
-----
    # stdio mode (for local / IDE integration)
    python -m app.mcp.server

    # SSE mode with auth (default)
    python -m app.mcp.server --transport sse --port 8001

    # SSE mode without auth (internal network only)
    python -m app.mcp.server --transport sse --port 8001 --no-auth

Run ``python -m app.mcp.server --help`` for all options.
"""

import argparse
import sys

from mcp.server.fastmcp import FastMCP

from app.mcp.tools.activity_data import get_activities
from app.mcp.tools.activity_stats import get_activity_stats
from app.mcp.tools.heart_rate import get_heart_rate_data

# ── Create MCP Server ──────────────────────────────────────────────────
mcp = FastMCP(
    "incremental-activities",
    instructions="""Activity data service for Incremental fitness tracker.

Provides tools to query and aggregate all types of fitness activities
(running, cycling, swimming, hiking, strength training, etc.) by date range.

All distances are in metres/kilometres, durations in seconds/hours,
pace in min/km.
""",
)


# ── Register Tools ─────────────────────────────────────────────────────


@mcp.tool(
    name="get_activities",
    description="""Query detailed fitness activity records for a user within a date range.

Returns paginated results with metrics including distance, duration,
heart rate, cadence, pace, elevation gain/loss, and calories.
Supports optional sport type filtering (e.g. 'running,cycling').

Use this when you need individual activity details.
""",
)
def get_activities_tool(
    user_id: int,
    start_date: str,
    end_date: str,
    sport_types: str = None,
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
        Comma-separated sport types. Examples: "running", "cycling,swimming".
        If omitted, all sport types are included.
    page_size : int, optional
        Results per page (default 50, max 200).
    page_count : int, optional
        Page number starting from 1 (default 1).
    """
    return get_activities(
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        sport_types=sport_types,
        page_size=min(page_size, 200),
        page_count=page_count,
    )


@mcp.tool(
    name="get_activity_stats",
    description="""Get aggregated activity statistics for a user within a date range.

Returns totals grouped by day, week, or month, including total distance,
total duration, activity count, average pace, average heart rate,
total calories, and total elevation gain.
Supports optional sport type filtering.

Use this when you need summary statistics or trends over time.
""",
)
def get_activity_stats_tool(
    user_id: int,
    start_date: str,
    end_date: str,
    group_by: str = "month",
    sport_types: str = None,
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
        Grouping granularity: "day", "week", or "month" (default "month").
    sport_types : str, optional
        Comma-separated sport types to filter, e.g. "running,cycling".
        If omitted, all sport types are included.
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


def main():
    parser = argparse.ArgumentParser(description="Incremental Activity Data MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind (default: 127.0.0.1, only for SSE)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8001,
        help="Port to bind (default: 8001, only for SSE)",
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Disable JWT Bearer auth for SSE mode (not recommended)",
    )
    args = parser.parse_args()

    if args.transport == "sse":
        import uvicorn
        from app.mcp.auth import require_bearer_auth

        app = mcp.sse_app()

        if not args.no_auth:
            app = require_bearer_auth(app)
            print(
                "MCP server [transport=sse, auth=JWT Bearer] "
                f"listening on {args.host}:{args.port}",
                file=sys.stderr,
            )
        else:
            print(
                f"MCP server [transport=sse, auth=disabled] "
                f"listening on {args.host}:{args.port}",
                file=sys.stderr,
            )

        uvicorn.run(app, host=args.host, port=args.port)
    else:
        print(
            "MCP server [transport=stdio] started — waiting for MCP client...",
            file=sys.stderr,
        )
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
