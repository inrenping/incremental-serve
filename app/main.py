from fastapi import FastAPI
from app.api.v1.api import api_router
from app.api.v1.endpoints.oauth import router as oauth_router
from app.api.v1.endpoints.well_known import router as well_known_router
from app.core.config import settings

if settings.ENV == "production":
    app = FastAPI(title="Incremental", docs_url=None, redoc_url=None, openapi_url=None)
else:
    app = FastAPI(title="Incremental")

app.include_router(api_router, prefix="/api/v1")
app.include_router(oauth_router)
app.include_router(well_known_router)


@app.get("/")
def root():
    return {"status": "online", "version": "v1.0.0"}
