from fastapi import APIRouter

from app.api import auth, business, content, contracts, funnel1, funnel2, people

api_router = APIRouter()
for r in (auth.router, people.router, funnel1.router, funnel2.router, business.router, contracts.router, content.router, content.public):
    api_router.include_router(r)
