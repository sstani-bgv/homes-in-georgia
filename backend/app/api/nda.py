"""NDA for new agents (services/nda.py, docs/NDA.md): staff routes + the agent's public signing page /nda/<token>."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user, paginate
from app.db import get_db
from app.models import User
from app.services import nda as N

router = APIRouter(tags=["nda"])
public = APIRouter(tags=["nda"])


class NdaIn(BaseModel):
    lang: str = "KA-RU"
    full_name: str = Field(max_length=200)
    personal_id: str = Field(max_length=60)
    phone: str | None = Field(None, max_length=60)
    telegram: str | None = Field(None, max_length=100)
    role: str | None = Field(None, max_length=100)  # nda_text.ROLES key: recruit | agent | mentor
    full_name_ka: str | None = Field(None, max_length=200)  # the name in Georgian (else full_name)
    ie: str | None = Field(None, max_length=60)  # IE registration number
    address: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=200)
    level: str | None = Field(None, max_length=4)  # 25 | 50 | 70 | 90
    access: str | None = Field(None, max_length=20)  # access start date: 2026-11-14 or 14.11.2026
    iban_gel: str | None = Field(None, max_length=60)
    iban_usd: str | None = Field(None, max_length=60)


class SignatureIn(BaseModel):
    signature: str = Field(max_length=N.MAX_SIGNATURE)


class SignIn(SignatureIn):
    consent: bool = False
    doc_sha256: str = Field(max_length=64)


def _pdf(data: bytes, name: str, download: bool) -> Response:
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f"{'attachment' if download else 'inline'}; filename=\"nda.pdf\"; filename*=UTF-8''{quote(name)}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"})


@router.get("/ndas")
def ndas_list(status: str | None = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db, scope="function"),
              user: User = Depends(current_user)):
    return paginate(db, N.query(user, status), lambda n: N.out(db, n), limit, offset)


@router.post("/ndas")
def ndas_create(body: NdaIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return N.out(db, N.create(db, body.model_dump(), user), full=True)


@router.get("/ndas/company-signature")
def ndas_company_signature(user: User = Depends(current_user)):
    N._staff(user)
    d = N.company_signature()
    return {"signature": d and d["signature"], "at": d and d.get("at"), "by_name": d and d.get("by_name")}


@router.put("/ndas/company-signature")
def ndas_set_company_signature(body: SignatureIn, user: User = Depends(current_user)):
    d = N.set_company_signature(body.signature, user)
    return {"signature": d["signature"], "at": d["at"], "by_name": d["by_name"]}


@router.get("/ndas/{nid}")
def ndas_get(nid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return N.out(db, N.get(db, nid, user), full=True)


@router.post("/ndas/{nid}/cancel")
def ndas_cancel(nid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return N.out(db, N.cancel(db, nid, user))


@router.get("/ndas/{nid}/pdf")
def ndas_pdf(nid: int, download: bool = False, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    n = N.get(db, nid, user)
    return _pdf(N.pdf_bytes(n), N.filename(n), download)


# the agent's page: no login, the unguessable token is the access
@public.get("/public/nda/{token}")
def public_nda(token: str, db: Session = Depends(get_db, scope="function")):
    return N.view(db, token)


@public.post("/public/nda/{token}/telegram")
def public_nda_telegram(token: str, body: dict, db: Session = Depends(get_db, scope="function")):
    """The Telegram Login Widget's answer (id, first_name, username, auth_date, hash…); on the test server {test: true}."""
    return N.confirm_telegram(db, token, body)


@public.post("/public/nda/{token}")
def public_nda_sign(token: str, body: SignIn, request: Request, db: Session = Depends(get_db, scope="function")):
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client and request.client.host)
    return N.sign(db, token, body.model_dump(), ip, request.headers.get("user-agent"))


@public.get("/public/nda/{token}/pdf")
def public_nda_pdf(token: str, download: bool = False, db: Session = Depends(get_db, scope="function")):
    n, data = N.signed_pdf(db, token)
    return _pdf(data, N.filename(n), download)
