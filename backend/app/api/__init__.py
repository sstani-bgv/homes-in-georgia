from fastapi import APIRouter

from app.api import auth, business, content, contracts, daily_rent, funnel1, funnel2, people

api_router = APIRouter()
for r in (auth.router, people.router, funnel1.router, funnel2.router, business.router, contracts.router, contracts.public, content.router, content.public, daily_rent.router):
    api_router.include_router(r)
