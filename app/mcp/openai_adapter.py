"""
OpenAI Function Calling Adapter for Activity Data Tools.

Exposes MCP tools as OpenAI-compatible function definitions so they can
be used with OpenAI's tool calling (function calling) API.

Usage
-----
1. Fetch tool definitions:
   GET /api/v1/openai/tools?user_id=123
   → Returns a list of OpenAI-compatible tool definitions.

2. Execute a tool call (from OpenAI's response):
   POST /api/v1/openai/execute
   Body: { "user_id": 123, "function_name": "...", "arguments": {...} }
   → Returns the tool execution result.
"""

from typing import Any

from app.mcp.tools.activity_data import get_activities
from app.mcp.tools.activity_stats import get_activity_stats
from app.mcp.tools.heart_rate import get_heart_rate_data

# ── Tool Definition Schemas (OpenAI Function Calling format) ──────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_activities",
            "description": "查询指定时间范围内的运动活动明细数据，支持按运动类型过滤。返回数据包括距离、时长、心率、配速、爬升等。不传 sport_types 则返回所有类型的运动。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "用户ID",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "起始日期（含），格式 YYYY-MM-DD",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期（含），格式 YYYY-MM-DD",
                    },
                    "sport_types": {
                        "type": "string",
                        "description": "运动类型过滤，逗号分隔，如 'running,cycling,swimming'。不传则包含所有运动类型",
                    },
                    "page_size": {
                        "type": "integer",
                        "description": "每页条数，默认 50，最大 200",
                        "default": 50,
                    },
                    "page_count": {
                        "type": "integer",
                        "description": "页码，从 1 开始，默认 1",
                        "default": 1,
                    },
                },
                "required": ["user_id", "start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_activity_stats",
            "description": "查询指定时间范围内的运动统计汇总（按天/周/月分组），包括总距离、总时长、次数、平均配速、平均心率、总消耗等。支持按运动类型过滤。不传 sport_types 则统计所有类型的运动。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "用户ID",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "起始日期（含），格式 YYYY-MM-DD",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期（含），格式 YYYY-MM-DD",
                    },
                    "group_by": {
                        "type": "string",
                        "description": "聚合粒度：day（按天）、week（按周）、month（按月），默认 month",
                        "default": "month",
                    },
                    "sport_types": {
                        "type": "string",
                        "description": "运动类型过滤，逗号分隔，如 'running,cycling'。不传则包含所有运动类型",
                    },
                },
                "required": ["user_id", "start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_heart_rate_data",
            "description": "查询指定时间范围内用户的每日心率数据（来源：Garmin 设备每日同步）。返回每日心率汇总（最高/最低/静息心率），并可选择返回每分钟的心率采样明细数据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "用户ID",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "起始日期（含），格式 YYYY-MM-DD",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期（含），格式 YYYY-MM-DD",
                    },
                    "include_details": {
                        "type": "boolean",
                        "description": "是否包含每分钟的心率采样明细数据，默认 true。如果只想看每日汇总指标（最高/最低/静息心率），设为 false",
                        "default": True,
                    },
                },
                "required": ["user_id", "start_date", "end_date"],
            },
        },
    },
]


def get_tool_definitions() -> list[dict]:
    """Return OpenAI-compatible function tool definitions."""
    return TOOL_DEFINITIONS


def execute_tool(function_name: str, arguments: dict[str, Any]) -> Any:
    """
    Execute an MCP tool by name with the given arguments.

    Parameters
    ----------
    function_name : str
        Tool / function name ('get_activities' or 'get_activity_stats').
    arguments : dict
        Function arguments as a dict.

    Returns
    -------
    The raw result from the tool (will be serialised as JSON).
    """
    if function_name == "get_activities":
        return get_activities(**arguments)
    elif function_name == "get_activity_stats":
        return get_activity_stats(**arguments)
    elif function_name == "get_heart_rate_data":
        return get_heart_rate_data(**arguments)
    else:
        raise ValueError(f"Unknown function: {function_name}")
