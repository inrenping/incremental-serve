import os
from fastapi import FastAPI
from app.api.v1.api import api_router
from app.api.v1.endpoints.oauth import router as oauth_router
from app.api.v1.endpoints.mcp_auth import router as mcp_auth_router
from app.core.config import settings
from app.mcp.server import mcp

if settings.ENV == "production":
    app = FastAPI(title="Incremental", docs_url=None, redoc_url=None, openapi_url=None)
else:
    app = FastAPI(title="Incremental")

app.include_router(api_router, prefix="/api/v1")
app.include_router(oauth_router)
app.include_router(mcp_auth_router)

# Mount MCP SSE endpoint with built-in OAuth 2.0 Authorization Code Flow
mcp_sse = mcp.sse_app()
app.mount("/mcp", mcp_sse)


@app.get("/")
def root():
    return {"status": "online", "version": "v1.0.0"}
