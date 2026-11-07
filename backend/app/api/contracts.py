"""Contract file → the agent's Telegram chat. The Mini App webview blocks blob / <a download> downloads, so the generated
.doc (frontend pages/Contracts.tsx docHtml: HTML that Word opens) comes here and the CRM bot sends it as a document."""
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import current_user
from app.models import User
from app.services.errors import DomainError
from app.telegram import client as tg

router = APIRouter(tags=["contracts"])


class ContractFileIn(BaseModel):
    filename: str = Field(max_length=200)
    html: str = Field(max_length=2_000_000)


@router.post("/contracts/send")
def contract_send(body: ContractFileIn, user: User = Depends(current_user)):
    if not user.tg_id:
        raise DomainError("no_telegram", "Telegram не привязан к аккаунту: откройте CRM из бота", 409)
    name = re.sub(r"[^\w.-]+", "_", body.filename).strip("._")[:120] or "contract"
    if not name.lower().endswith(".doc"):
        name += ".doc"
    data = ("﻿" + body.html).encode("utf-8")
    if tg.call("sendDocument", files={"document": (name, data, "application/msword")}, chat_id=user.tg_id,
               caption="Договор: откройте в Word или Google Docs") is None:
        raise DomainError("send_failed", "Telegram не принял файл, попробуйте ещё раз", 502)
    return {"ok": True}
