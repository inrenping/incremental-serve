import os
from fastapi import FastAPI
from app.api.v1.api import api_router

app = FastAPI(title="Running AI MVP")

ENV = os.getenv("APP_ENV", "development")

if ENV == "production":
    app = FastAPI(title="blunt",docs_url=None, redoc_url=None, openapi_url=None)
else:
    app = FastAPI(title="blunt")

app.include_router(api_router, prefix="/api/v1")

@app.get("/")
def root():
    return {"status": "online", "version": "v1.0.0"}