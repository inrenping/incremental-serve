import os
from fastapi import FastAPI

ENV = os.getenv("APP_ENV", "development")

if ENV == "production":
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
else:
    app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Hello, FastAPI is running!"}