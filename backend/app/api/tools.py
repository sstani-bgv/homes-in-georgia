"""Server-side Telegram proxy for the legacy post generator: the bot token never reaches the browser."""
import json

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import AGENT_PLUS, require_roles
from app.config import settings
from app.models import User
from app.services.errors import DomainError

router = APIRouter(prefix="/tools", tags=["tools"])
METHODS = {"sendMessage", "sendPhoto", "sendMediaGroup", "sendVideo", "sendDocument"}


def _topic(v: str):
    return int(v) if v and v.lstrip("-").isdigit() else None


@router.get("/config")
def tools_config(user: User = Depends(require_roles(*AGENT_PLUS))):
    return {"chat_id": settings.PUBLISH_CHAT_ID or None,
            "topics": {"originals": _topic(settings.PUBLISH_TOPIC_ORIGINALS), "owner": _topic(settings.PUBLISH_TOPIC_OWNER),
                       "album": _topic(settings.PUBLISH_TOPIC_ALBUM)},
            "channel": settings.AGENCY_CHANNEL}


@router.post("/telegram/{method}")
async def telegram_proxy(method: str, request: Request, user: User = Depends(require_roles(*AGENT_PLUS))):
    if method not in METHODS:
        raise DomainError("forbidden", f"method {method} is not allowed")
    if not settings.BOT_TOKEN:
        raise DomainError("not_configured", "BOT_TOKEN is not configured", 503)
    ctype = request.headers.get("content-type", "")
    data, files = {}, []
    if ctype.startswith("multipart/") or ctype.startswith("application/x-www-form-urlencoded"):
        form = await request.form()
        for k, v in form.multi_items():
            if hasattr(v, "read"):
                files.append((k, (v.filename or k, await v.read(), v.content_type or "application/octet-stream")))
            else:
                data[k] = v
    else:
        try:
            data = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in (await request.json()).items()}
        except ValueError:
            raise DomainError("bad_request", "expected multipart form or JSON body")
    chat = str(data.get("chat_id") or "")
    allowed = settings.publish_chat_ids
    if not chat or chat not in allowed:
        raise DomainError("forbidden", "chat_id is not allowed")
    url = f"{settings.TG_API_BASE.rstrip('/')}/bot{settings.BOT_TOKEN}/{method}"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(url, data=data, files=files or None)
        return JSONResponse(r.json(), status_code=200 if r.is_success else r.status_code)
    except (httpx.HTTPError, ValueError) as e:
        return JSONResponse({"ok": False, "description": f"Telegram request failed: {type(e).__name__}"}, status_code=502)
