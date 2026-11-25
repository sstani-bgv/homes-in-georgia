"""«Документ на подпись» (services/sign_docs.py): staff routes + the signer's public page /doc/<token> (the NDA's page)."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user, paginate
from app.api.nda import SignIn, _pdf
from app.db import get_db
from app.models import User
from app.services import sign_docs as D

router = APIRouter(tags=["sign-docs"])
public = APIRouter(tags=["sign-docs"])


class Person(BaseModel):
    user_id: int | None = None
    full_name: str | None = Field(None, max_length=200)
    personal_id: str | None = Field(None, max_length=60)
    phone: str | None = Field(None, max_length=60)
    telegram: str | None = Field(None, max_length=100)


class Signer(Person):  # «Договор между сторонами»: a row (id = the existing signer, its link stays)
    id: int | None = None
    role: str | None = Field(None, max_length=60)


class DocIn(BaseModel):
    lang: str = "KA-RU"
    title: dict[str, str] = {}
    body: dict[str, str] = {}
    party: Person = Person()
    cosigner: Person | None = None
    mode: str | None = None  # "parties" = «Договор между сторонами» (a ready PDF, the signers below, no company signature)
    signers: list[Signer] = Field([], max_length=20)
    pdf: str | None = Field(None, max_length=21_000_000)  # a ready PDF instead of the text: data:…;base64 (≤ 15 MB)
    pdf_name: str | None = Field(None, max_length=300)


@router.get("/sign-docs")
def docs_list(status: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0,
              db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return paginate(db, D.query(user, status, q), lambda d: D.out(db, d), limit, offset)


@router.post("/sign-docs")
def docs_create(body: DocIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return D.out(db, D.create(db, body.model_dump(), user), full=True)


@router.get("/sign-docs/{did}")
def docs_get(did: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return D.out(db, D.get(db, did, user), full=True)


@router.put("/sign-docs/{did}")
def docs_update(did: int, body: DocIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return D.out(db, D.update(db, did, body.model_dump(), user), full=True)


@router.post("/sign-docs/{did}/send")
def docs_send(did: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return D.send(db, did, user)


@router.post("/sign-docs/{did}/cancel")
def docs_cancel(did: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return D.out(db, D.cancel(db, did, user))


@router.get("/sign-docs/{did}/pdf")
def docs_pdf(did: int, download: bool = False, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    d, data = D.pdf(db, did, user)
    return _pdf(data, D.filename(d), download)


# the signer's page: no login, the unguessable token is the access
@public.get("/public/doc/{token}")
def public_view(token: str, db: Session = Depends(get_db, scope="function")):
    return D.view(db, token)


@public.post("/public/doc/{token}/telegram")
def public_telegram(token: str, body: dict, db: Session = Depends(get_db, scope="function")):
    return D.confirm_telegram(db, token, body)


@public.post("/public/doc/{token}")
def public_sign(token: str, body: SignIn, request: Request, db: Session = Depends(get_db, scope="function")):
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client and request.client.host)
    return D.sign(db, token, body.model_dump(), ip, request.headers.get("user-agent"))


@public.get("/public/doc/{token}/pdf")
def public_pdf(token: str, download: bool = False, db: Session = Depends(get_db, scope="function")):
    d, data = D.signed_pdf(db, token)
    return _pdf(data, D.filename(d), download)
