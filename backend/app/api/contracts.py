"""Contract file → the agent's Telegram chat. The Mini App webview blocks blob / <a download> downloads, so the generated
.doc (frontend pages/Contracts.tsx docHtml: HTML that Word opens) comes here and the CRM bot sends it as a document."""
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user, paginate, require, require_level
from app.db import get_db
from app.models import User
from app.services import contracts as C, esign as E
from app.services.errors import DomainError
from app.telegram import client as tg

# «Договоры» in the «Права» matrix (services/perms.py): read / create / update per endpoint below
router = APIRouter(tags=["contracts"])
READ, CREATE, UPDATE = (Depends(require("contracts", a)) for a in "rcu")
public = APIRouter(tags=["contracts"])


class ContractFileIn(BaseModel):
    filename: str = Field(max_length=200)
    html: str = Field(max_length=2_000_000)


@router.post("/contracts/send", dependencies=[READ])
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

class DraftIn(BaseModel):
    """The wizard's autosave: whatever is filled so far."""
    id: int | None = None
    kind: str | None = None
    lang: str | None = None
    lead_id: int | None = None
    deal_id: int | None = None
    property_id: int | None = None  # the object card's «Оформить договор аренды» (11-15)
    data: dict | None = None


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


class LinkIn(BaseModel):
    lead_id: int | None = None
    deal_id: int | None = None
    property_id: int | None = None  # David 11-26: the object alone, when there is no lead


class ESignIn(BaseModel):
    full_name: str = Field(max_length=200)
    personal_id: str = Field(max_length=200)
    phone: str = Field(max_length=200)
    consent: bool = False
    signature: str = Field(max_length=E.MAX_SIGNATURE)
    doc_sha256: str = Field(max_length=64)
    lang: str | None = Field(None, max_length=5)


class SendClientIn(BaseModel):
    to: str | None = Field(None, max_length=100)  # «@username» / phone; empty = the lead's client


class CloseIn(BaseModel):
    reason: str
    comment: str | None = Field(None, max_length=2000)


def _pdf(data: bytes, name: str, download: bool) -> Response:
    disp = "attachment" if download else "inline"
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f"{disp}; filename=\"contract.pdf\"; filename*=UTF-8''{quote(name)}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"})


@router.get("/contracts", dependencies=[READ])
def contracts_list(status: str | None = None, lead_id: int | None = None, deal_id: int | None = None,
                   property_id: int | None = None, agent_id: int | None = None, q: str | None = None, limit: int = 50, offset: int = 0,
                   db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """status: active (default: draft + signed) | draft | signed | deal | closed_no_deal | all."""
    return paginate(db, C.query(db, user, status, lead_id, q, deal_id, agent_id, property_id), lambda c: C.out(db, c), limit, offset)


class TranslateIn(BaseModel):
    text: str = Field(max_length=10_000)
    to: list[str] = Field(max_length=3)


@router.post("/contracts/translate")
def contracts_translate(body: TranslateIn):
    """An edited / added clause of a bilingual contract into the other column's language (frontend changeClause)."""
    return C.translate(body.text, body.to)


@router.get("/properties/{pid}/contract", dependencies=[CREATE])
def property_contract(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """The wizard's prefill from the object card («Оформить договор аренды», Irakli 11-15), as GET /leads/{id}/contract:
    address, cadastral code, floor, area, price, owner. Another agent's object: refused, as in the deal form."""
    return C.property_prefill(db, pid, user)


@router.post("/contracts/draft", dependencies=[CREATE])
def contracts_autosave(body: DraftIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return C.out(db, C.autosave(db, body.model_dump(), user), full=True)


@router.post("/contracts", dependencies=[CREATE])
def contracts_create(body: ContractIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return C.out(db, C.create(db, body.model_dump(), user), full=True)


@router.get("/contracts/{cid}", dependencies=[READ])
def contracts_get(cid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return C.out(db, C.get(db, cid, user), full=True)


@router.put("/contracts/{cid}", dependencies=[UPDATE])
def contracts_update(cid: int, body: ContractIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return C.out(db, C.update(db, cid, body.model_dump(), user), full=True)


@router.get("/contracts/{cid}/pdf", dependencies=[READ])
def contracts_pdf(cid: int, signed: bool = False, download: bool = False, version: int | None = None,
                  db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """version: an earlier generated PDF (the contract's `versions`, 0 = the first)."""
    c = C.get(db, cid, user)
    name = C.filename(c, signed and bool(c.signed_pdf_path))
    return _pdf(C.pdf_bytes(c, signed, version), name if version is None else name.replace(".pdf", f" v{version + 1}.pdf"), download)


@router.get("/contracts/{cid}/extract", dependencies=[READ])
def contracts_extract(cid: int, download: bool = False, db: Session = Depends(get_db, scope="function"),
                      user: User = Depends(current_user)):
    """The registry extract saved with the contract (data.extract_file, registry.store_extract)."""
    from app import storage
    from app.services import registry
    key = str((C.get(db, cid, user).data or {}).get("extract_file") or "")
    data = storage.read(key) if registry.EXTRACT_KEY_RE.match(key) else None  # data is the wizard's: only our extracts
    if not data:
        raise DomainError("not_found", "Выписка не сохранена", 404)
    return _pdf(data, f"Выписка из реестра, договор {cid}.pdf", download)


@router.post("/contracts/{cid}/sign", dependencies=[UPDATE])
async def contracts_sign(cid: int, file: UploadFile | None = File(None), db: Session = Depends(get_db, scope="function"),
                         user: User = Depends(current_user)):
    """«Договор подписан»: multipart with an optional `file` (the signed scan, PDF)."""
    return C.out(db, C.sign(db, cid, user, await file.read() if file else None), full=True)


@router.post("/contracts/{cid}/link", dependencies=[UPDATE])
def contracts_link(cid: int, body: LinkIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """«Привязать к заявке»: a contract created without a lead / deal (11-13)."""
    return C.out(db, C.link(db, cid, body.model_dump(), user), full=True)


@router.post("/contracts/{cid}/deal-lead", dependencies=[UPDATE])
def contracts_deal_lead(cid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """«Оформить сделку» on a signed contract: its lead (found or made from the contract) for the deal form (11-18)."""
    return C.out(db, C.deal_lead(db, cid, user), full=True)


@router.post("/contracts/{cid}/esign", dependencies=[UPDATE])
def contracts_esign(cid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """«Подписать электронно»: a signing link for the owner and one for the tenant (services/esign.py)."""
    return C.out(db, E.start(db, cid, user), full=True)


@router.post("/contracts/{cid}/esign/cancel", dependencies=[UPDATE])
def contracts_esign_cancel(cid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return C.out(db, E.cancel(db, cid, user), full=True)


@router.post("/contracts/{cid}/close", dependencies=[UPDATE])
def contracts_close(cid: int, body: CloseIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return C.out(db, C.close(db, cid, user, body.reason, body.comment), full=True)


@router.post("/contracts/{cid}/deal-due/lift")
def contracts_deal_due_lift(cid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("operator"))):
    """«Снять блокировку»: the agent's 24 h lock for this contract's deal is lifted (the contract is cancelled, …)."""
    return C.out(db, C.lift_deal_due(db, cid, user), full=True)


@router.post("/contracts/{cid}/share", dependencies=[UPDATE])
def contracts_share(cid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return C.out(db, C.share(db, cid, user))


@router.post("/contracts/{cid}/send", dependencies=[READ])
def contracts_send_pdf(cid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """Mini App: the PDF to the user's Telegram chat (the webview can't download files)."""
    c = C.get(db, cid, user)
    if not user.tg_id:
        raise DomainError("no_telegram", "Telegram не привязан к аккаунту: откройте CRM из бота", 409)
    signed = bool(c.signed_pdf_path)
    if tg.call("sendDocument", files={"document": (C.filename(c, signed), C.pdf_bytes(c, signed), "application/pdf")},
               chat_id=user.tg_id, caption=f"Договор #{c.id}: {C.label(c)}") is None:
        raise DomainError("send_failed", "Telegram не принял файл, попробуйте ещё раз", 502)
    return {"ok": True}


@router.post("/contracts/{cid}/send-client", dependencies=[UPDATE])
def contracts_send_client(cid: int, body: SendClientIn, db: Session = Depends(get_db, scope="function"),
                          user: User = Depends(current_user)):
    """«Отправить клиенту в Telegram»: PDF + the registry extract from the operator's account (11-13)."""
    return C.out(db, C.send_to_client(db, cid, user, body.to), full=True)


# the «Поделиться» link: no login, the unguessable token is the access; expires (CONTRACT_SHARE_DAYS), read-only
@public.get("/public/contracts/{token}/pdf")
def public_contract_pdf(token: str, download: bool = False, db: Session = Depends(get_db, scope="function")):
    c = C.by_share_token(db, token)
    signed = bool(c.signed_pdf_path)
    return _pdf(C.pdf_bytes(c, signed), C.filename(c, signed), download)


# a party's electronic signing page (frontend /esign/<token>): no login, the token is the access (services/esign.py)
@public.get("/public/esign/{token}")
def public_esign(token: str, db: Session = Depends(get_db, scope="function")):
    return E.view(db, token)


@public.post("/public/esign/{token}")
def public_esign_sign(token: str, body: ESignIn, request: Request, db: Session = Depends(get_db, scope="function")):
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client and request.client.host)
    return E.sign(db, token, body.model_dump(), ip, request.headers.get("user-agent"))


@public.get("/public/esign/{token}/pdf")
def public_esign_pdf(token: str, download: bool = False, db: Session = Depends(get_db, scope="function")):
    c, data = E.signed_pdf(db, token)
    return _pdf(data, C.filename(c, True), download)


@public.get("/public/esign/{token}/extract")
def public_esign_extract(token: str, download: bool = False, db: Session = Depends(get_db, scope="function")):
    """The registry extract with the signing link (11-13): only by a valid signer token of that contract."""
    c, data = E.extract(db, token)
    return _pdf(data, f"Extract {c.cadastral or c.id}.pdf", download)
