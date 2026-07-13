"""
FastAPI endpoints exposing activity data tools in OpenAI Function Calling format.

These endpoints allow any LLM application (OpenAI, etc.) to discover
and invoke the activity data tools via standard HTTP.

Authentication
--------------
All endpoints require JWT Bearer token authentication.
The authenticated user's ID is automatically passed to the tools,
so the caller does NOT need to provide user_id.

Endpoints
---------
- GET  /openai/tools     → list of tool definitions
- POST /openai/execute   → execute a tool and return results
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.security import get_current_user
from app.models.user import User
from app.mcp.openai_adapter import get_tool_definitions, execute_tool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/openai", tags=["openai-mcp"])


class ExecuteRequest(BaseModel):
    function_name: str = Field(description="Tool/function name, e.g. 'get_activities'")
    arguments: dict = Field(
        description="Function arguments as a dict (do NOT include user_id)"
    )


class ExecuteResponse(BaseModel):
    success: bool
    result: dict | list | None = None
    error: str | None = None


@router.get("/tools")
def list_tools(current_user: User = Depends(get_current_user)):
    """
    Get all available tool definitions in OpenAI function calling format.
    Requires JWT Bearer token authentication.
    """
    return {
        "user_id": current_user.id,
        "tools": get_tool_definitions(),
    }


@router.post("/execute", response_model=ExecuteResponse)
def call_tool(
    req: ExecuteRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Execute a tool by name with the provided arguments.
    The authenticated user's ID is automatically injected — do NOT pass user_id in arguments.

    Example:
    ```json
    {
      "function_name": "get_activity_stats",
      "arguments": {
        "start_date": "2026-06-01",
        "end_date": "2026-07-13",
        "group_by": "month"
      }
    }
    ```
    """
    # Inject the authenticated user's ID automatically
    args = dict(req.arguments)
    args["user_id"] = current_user.id

    try:
        result = execute_tool(req.function_name, args)
        return ExecuteResponse(success=True, result=result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Tool execution failed")
        return ExecuteResponse(success=False, error=str(e))
