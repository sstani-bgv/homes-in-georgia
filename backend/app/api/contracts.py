"""Contract file → the agent's Telegram chat. The Mini App webview blocks blob / <a download> downloads, so the generated
.doc (frontend pages/Contracts.tsx docHtml: HTML that Word opens) comes here and the CRM bot sends it as a document."""
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user, paginate, require_level
from app.db import get_db
from app.models import User
from app.services import contracts as C
from app.services.errors import DomainError
from app.telegram import client as tg

router = APIRouter(tags=["contracts"], dependencies=[Depends(require_level("agent"))])  # recruits: no contracts (Irakli 11-12)
public = APIRouter(tags=["contracts"])


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


# ---- stored contracts (services/contracts.py): list, PDF, lifecycle, share link

class ContractIn(BaseModel):
    kind: str
    lang: str
    html: str = Field(max_length=C.MAX_HTML)
    title: str | None = Field(None, max_length=300)
    cadastral: str | None = Field(None, max_length=100)
    address: str | None = Field(None, max_length=500)
    client_name: str | None = Field(None, max_length=300)
    owner_name: str | None = Field(None, max_length=300)
    lead_id: int | None = None
    deal_id: int | None = None
    property_id: int | None = None
    data: dict | None = None  # the wizard's CxData (deal prefill); omitted = kept


class CloseIn(BaseModel):
    reason: str
    comment: str | None = Field(None, max_length=2000)


def _pdf(data: bytes, name: str, download: bool) -> Response:
    disp = "attachment" if download else "inline"
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f"{disp}; filename=\"contract.pdf\"; filename*=UTF-8''{quote(name)}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"})


@router.get("/contracts")
def contracts_list(status: str | None = None, lead_id: int | None = None, q: str | None = None, limit: int = 50,
                   offset: int = 0, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """status: active (default: draft + signed) | draft | signed | deal | closed_no_deal | all."""
    return paginate(db, C.query(db, user, status, lead_id, q), lambda c: C.out(db, c), limit, offset)


@router.post("/contracts")
def contracts_create(body: ContractIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return C.out(db, C.create(db, body.model_dump(), user), full=True)


@router.get("/contracts/{cid}")
def contracts_get(cid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return C.out(db, C.get(db, cid, user), full=True)


@router.put("/contracts/{cid}")
def contracts_update(cid: int, body: ContractIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return C.out(db, C.update(db, cid, body.model_dump(), user), full=True)


@router.get("/contracts/{cid}/pdf")
def contracts_pdf(cid: int, signed: bool = False, download: bool = False, db: Session = Depends(get_db, scope="function"),
                  user: User = Depends(current_user)):
    c = C.get(db, cid, user)
    return _pdf(C.pdf_bytes(c, signed), C.filename(c, signed and bool(c.signed_pdf_path)), download)


@router.post("/contracts/{cid}/sign")
async def contracts_sign(cid: int, file: UploadFile | None = File(None), db: Session = Depends(get_db, scope="function"),
                         user: User = Depends(current_user)):
    """«Договор подписан»: multipart with an optional `file` (the signed scan, PDF)."""
    return C.out(db, C.sign(db, cid, user, await file.read() if file else None), full=True)


@router.post("/contracts/{cid}/close")
def contracts_close(cid: int, body: CloseIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return C.out(db, C.close(db, cid, user, body.reason, body.comment), full=True)


@router.post("/contracts/{cid}/share")
def contracts_share(cid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return C.out(db, C.share(db, cid, user))


@router.post("/contracts/{cid}/send")
def contracts_send_pdf(cid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """Mini App: the PDF to the user's Telegram chat (the webview can't download files)."""
    c = C.get(db, cid, user)
    if not user.tg_id:
        raise DomainError("no_telegram", "Telegram не привязан к аккаунту: откройте CRM из бота", 409)
    signed = bool(c.signed_pdf_path)
    if tg.call("sendDocument", files={"document": (C.filename(c, signed), C.pdf_bytes(c, signed), "application/pdf")},
               chat_id=user.tg_id, caption=f"Договор #{c.id}: {C.STATUS_LABEL[c.status]}") is None:
        raise DomainError("send_failed", "Telegram не принял файл, попробуйте ещё раз", 502)
    return {"ok": True}


# the «Поделиться» link: no login, the unguessable token is the access; expires (CONTRACT_SHARE_DAYS), read-only
@public.get("/public/contracts/{token}/pdf")
def public_contract_pdf(token: str, download: bool = False, db: Session = Depends(get_db, scope="function")):
    c = C.by_share_token(db, token)
    signed = bool(c.signed_pdf_path)
    return _pdf(C.pdf_bytes(c, signed), C.filename(c, signed), download)
