from fastapi import APIRouter
from app.api.v1.endpoints import user,auth,garmin,coros,settings


api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])

api_router.include_router(user.router, prefix="/user", tags=["User"])

api_router.include_router(garmin.router, prefix="/garmin", tags=["Garmin"])

api_router.include_router(coros.router, prefix="/coros", tags=["Coros"])

api_router.include_router(settings.router, prefix="/settings", tags=["Settings"])