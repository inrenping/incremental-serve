import os
from fastapi import FastAPI
from app.api.v1.api import api_router
from app.api.v1.endpoints.oauth import router as oauth_router
from app.core.config import settings
from app.mcp.server import mcp
from app.mcp.auth import require_bearer_auth

if settings.ENV == "production":
    app = FastAPI(title="Incremental", docs_url=None, redoc_url=None, openapi_url=None)
else:
    app = FastAPI(title="Incremental")

app.include_router(api_router, prefix="/api/v1")
app.include_router(oauth_router)

# Mount MCP SSE endpoint with JWT Bearer auth
mcp_sse = mcp.sse_app()
mcp_sse = require_bearer_auth(mcp_sse)
app.mount("/mcp", mcp_sse)


@app.get("/")
def root():
    return {"status": "online", "version": "v1.0.0"}
