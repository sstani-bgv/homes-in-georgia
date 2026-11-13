"""Generated contracts (docs/CONTRACTS.md): stored HTML → PDF (headless Chrome, the same print layout as the wizard's
«Печать»), lifecycle draft → signed → deal | closed_no_deal, expiring share links, signed PDF → Google Drive (worker)."""
import base64
import html as H
import json
import logging
import os
import re
import secrets
import subprocess
import tempfile
import time
import uuid
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import storage
from app.config import REPO_ROOT, settings as env
from app.models import CONTRACT_CLOSE_REASONS, Contract, Deal, Lead, User, at_least
from app.services import jobs
from app.services.common import get_or_404, log_event, now
from app.services.errors import DomainError

log = logging.getLogger(__name__)
STATUS_LABEL = {"draft": "Черновик", "signed": "Договор подписан", "deal": "Сделка", "closed_no_deal": "Сделка не состоялась"}
ACTIVE = ("draft", "signed")
MAX_HTML = 3_000_000
MAX_PDF = 20 * 1024 * 1024
KINDS, LANGS = ("rent", "comm", "sale"), ("GEO", "RU", "EN")

# ---------------------------------------------------------------- HTML → PDF

# The wizard's HTML is typed / edited by people: Chrome may only see these tags and attributes, images only as data:
# (the two brand files are inlined first), CSS without url()/@import — no file:// or network reads into a PDF.
TAGS = {"html", "head", "meta", "title", "style", "body", "article", "section", "div", "p", "span", "b", "strong", "i", "em",
        "u", "s", "br", "hr", "table", "thead", "tbody", "tr", "td", "th", "img", "font", "sup", "sub", "ul", "ol", "li",
        "h1", "h2", "h3", "h4", "h5", "h6"}
VOID = {"meta", "br", "hr", "img"}
HTML_VOID = VOID | {"area", "base", "col", "embed", "input", "link", "source", "track", "wbr", "param", "keygen"}
ATTRS = {"class", "style", "colspan", "rowspan", "lang", "align", "alt", "src", "charset", "width", "height"}
BRAND = ("contract-watermark.png", "ge-flag.svg")


def _brand_uri(name: str) -> str:
    for d in (REPO_ROOT / "frontend" / "dist" / "brand", REPO_ROOT / "frontend" / "public" / "brand"):
        if (d / name).is_file():
            mt = "image/svg+xml" if name.endswith(".svg") else "image/png"
            return f"data:{mt};base64,{base64.b64encode((d / name).read_bytes()).decode()}"
    return ""


def _css(css: str) -> str:
    css = re.sub(r"\\[0-9a-fA-F]", "", css)  # no CSS escapes (u\72l( …)
    css = re.sub(r"@import[^;]*;?|@font-face\s*\{[^}]*\}|image-set\([^)]*\)", "", css, flags=re.I)
    return re.sub(r"url\(\s*(['\"]?)(?!data:image/)[^)]*\)", "none", css, flags=re.I)


class _Clean(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.in_style = [], 0, False

    def handle_starttag(self, tag, attrs):
        if tag not in TAGS:
            self.skip += tag not in HTML_VOID  # a dropped element drops its content too (script, iframe, svg…)
            return
        if self.skip:
            return
        keep = []
        for k, v in attrs:
            v = v or ""
            if k not in ATTRS or (k == "src" and not (tag == "img" and v.startswith("data:image/"))) \
                    or (k == "charset") != (tag == "meta"):
                continue
            keep.append(f' {k}="{H.escape(_css(v) if k == "style" else v, quote=True)}"')
        if tag == "meta" and not keep:
            return
        self.in_style = tag == "style"
        self.out.append(f"<{tag}{''.join(keep)}>")

    def handle_endtag(self, tag):
        if tag not in TAGS:
            if tag not in HTML_VOID:
                self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag in VOID:
            return
        self.in_style = False
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self.skip:
            return
        self.out.append(_css(data).replace("</", "") if self.in_style else H.escape(data, quote=False))

    def handle_decl(self, decl):
        if decl.lower().startswith("doctype"):
            self.out.append("<!doctype html>")


def clean_html(src: str) -> str:
    for name in BRAND:  # the wizard links /brand/<file>: inline them (Chrome reads no network, no other files)
        src = src.replace(f"/brand/{name}", _brand_uri(name))
    p = _Clean()
    p.feed(src)
    p.close()
    return "".join(p.out)


def render_pdf(page: str) -> bytes:
    """Print the (cleaned) page with headless Chrome: @page margin boxes give the header, page numbers and the
    initials line on every page exactly as the wizard's «Печать». Scripts are cleaned out, network goes to a dead proxy."""
    with tempfile.TemporaryDirectory() as d:
        src, out = Path(d) / "contract.html", Path(d) / "contract.pdf"
        src.write_text(page, encoding="utf-8")
        cmd = [env.CHROME_BIN, "--headless", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
               "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--no-pdf-header-footer",
               "--proxy-server=127.0.0.1:9", "--proxy-bypass-list=<-loopback>",
               f"--user-data-dir={d}/profile", f"--print-to-pdf={out}", src.as_uri()]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.error("contract pdf: %s", e)
            raise DomainError("pdf_failed", "Не удалось создать PDF, попробуйте ещё раз", 502)
        data = out.read_bytes() if out.is_file() else b""
        if not data.startswith(b"%PDF"):
            log.error("contract pdf: chrome exit %s: %s", r.returncode, r.stderr[-500:])
            raise DomainError("pdf_failed", "Не удалось создать PDF, попробуйте ещё раз", 502)
        return data


# ---------------------------------------------------------------- access, output

def can_view(db: Session, c: Contract, user: User) -> bool:
    if at_least(user, "operator") or c.created_by == user.id:
        return True
    lead = db.get(Lead, c.lead_id) if c.lead_id else None
    return bool(lead and lead.agent_id == user.id)


def get(db: Session, cid: int, user: User, lock: bool = False) -> Contract:
    c = get_or_404(db, Contract, int(cid), "contract", lock=lock)
    if not can_view(db, c, user):
        raise DomainError("not_found", "contract not found")
    return c


def _name(db: Session, uid):
    u = db.get(User, uid) if uid else None
    return {"id": u.id, "name": u.name} if u else None


def _iso(v):
    return v.isoformat() if v else None


def label(c: Contract) -> str:
    """A draft reads «Черновик» while it is being filled (autosaved, no PDF) and «Сгенерирован» once it has a PDF."""
    return ("Сгенерирован" if c.pdf_path else "Черновик") if c.status == "draft" else STATUS_LABEL[c.status]


def stale(c: Contract) -> bool:
    """The form data changed after the last PDF: «Обновить PDF» before signing."""
    return bool(c.pdf_path and c.generated_at and c.updated_at and c.updated_at > c.generated_at)


def out(db: Session, c: Contract, full: bool = False) -> dict:
    share = c.share_token if c.share_expires_at and c.share_expires_at > now() else None
    lead = db.get(Lead, c.lead_id) if c.lead_id else None
    d = {"id": c.id, "status": c.status, "status_label": label(c), "kind": c.kind, "lang": c.lang,
         "has_pdf": bool(c.pdf_path), "generated_at": _iso(c.generated_at), "pdf_stale": stale(c),
         "versions_count": len(c.versions or []), "lead_status": lead.status if lead else None,
         "title": c.title, "cadastral": c.cadastral, "address": c.address, "client_name": c.client_name,
         "owner_name": c.owner_name, "lead_id": c.lead_id, "deal_id": c.deal_id, "property_id": c.property_id,
         "created_by": _name(db, c.created_by), "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at),
         "signed_at": _iso(c.signed_at), "signed_by": _name(db, c.signed_by), "has_signed_pdf": bool(c.signed_pdf_path),
         "close_reason": c.close_reason, "close_reason_label": CONTRACT_CLOSE_REASONS.get(c.close_reason or ""),
         "close_comment": c.close_comment, "closed_at": _iso(c.closed_at), "closed_by": _name(db, c.closed_by),
         "in_drive": bool(c.drive_file_id), "filename": filename(c),
         "share": {"path": share_path(share), "url": env.PUBLIC_URL.rstrip("/") + share_path(share),
                   "expires_at": _iso(c.share_expires_at)} if share else None}
    from app.services import esign  # esign imports this module
    d["esign"] = esign.out(db, c)
    if full:
        d["html"], d["data"] = c.html, c.data
        d["versions"] = [{"n": i, "at": v.get("at"), "by": _name(db, v.get("by"))} for i, v in enumerate(c.versions or [])]
    return d


def summary(c: Contract | None) -> dict | None:
    """The lead's contract in lead payloads (GET /leads/{id}, /leads/{id}/contract)."""
    if not c:
        return None
    return {"id": c.id, "status": c.status, "status_label": label(c), "has_pdf": bool(c.pdf_path), "signed_at": _iso(c.signed_at),
            "has_signed_pdf": bool(c.signed_pdf_path), "close_reason": c.close_reason,
            "close_reason_label": CONTRACT_CLOSE_REASONS.get(c.close_reason or ""), "closed_at": _iso(c.closed_at)}


def for_lead(db: Session, lead_id: int) -> Contract | None:
    """The lead's live contract: signed / with a deal first, then a generated draft, then one being filled; else the
    latest closed one (a fresh autosaved draft must not hide the signed contract from the deal guard)."""
    rows = list(db.scalars(select(Contract).where(Contract.lead_id == lead_id).order_by(Contract.id.desc())))
    rank = lambda c: (0 if c.status in ("signed", "deal") else 1 if c.pdf_path else 2) if c.status != "closed_no_deal" else 3  # noqa: E731
    return min(rows, key=rank) if rows else None


def query(db: Session, user: User, status: str | None = None, lead_id: int | None = None, q: str | None = None,
          deal_id: int | None = None, agent_id: int | None = None):
    """status: active (draft + signed, the default lists) | draft | signed | deal | closed_no_deal | all.
    agent_id (staff): that agent's contracts, as the agent sees them in his cabinet."""
    stmt = select(Contract)
    who = user.id if not at_least(user, "operator") else agent_id
    if who:
        mine = select(Lead.id).where(Lead.agent_id == who)
        stmt = stmt.where((Contract.created_by == who) | Contract.lead_id.in_(mine))
    if deal_id:
        stmt = stmt.where(Contract.deal_id == deal_id)
    st = status or "active"
    if st == "active":
        stmt = stmt.where(Contract.status.in_(ACTIVE))
    elif st != "all":
        stmt = stmt.where(Contract.status.in_(st.split(",")))
    if lead_id:
        stmt = stmt.where(Contract.lead_id == lead_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Contract.cadastral.ilike(like) | Contract.address.ilike(like) | Contract.client_name.ilike(like)
                          | Contract.owner_name.ilike(like))
    return stmt.order_by(Contract.updated_at.desc(), Contract.id.desc())


def filename(c: Contract, signed: bool = False) -> str:
    """As the old system's Drive files: by the cadastral code (+ date and client)."""
    day = (c.signed_at or c.generated_at or c.created_at or now()).date().isoformat()
    parts = [c.cadastral or f"contract-{c.id}", day, c.client_name or ""]
    name = re.sub(r"[\\/:*?\"<>|\s]+", " ", " ".join(p for p in parts if p)).strip()[:150]
    return f"{name}{' signed' if signed else ''}.pdf"


# ---------------------------------------------------------------- create / autosave / (re)generate

DATA_META = ("_step", "_dep", "_end")  # the wizard's own state kept with CxData: reopened at the same step


def _link(db: Session, body: dict, user: User) -> dict:
    """The deal a contract belongs to (required): the lead («сделка» at the contract stage, the Deal row comes after
    signing) or the Deal itself; the other ids follow from it."""
    lead_id = body.get("lead_id") or None
    deal_id = body.get("deal_id") or None
    prop_id = body.get("property_id") or None
    if not lead_id and not deal_id:
        raise DomainError("no_deal", "Договор привязывается к сделке: выберите заявку или сделку")
    if lead_id:
        lead = get_or_404(db, Lead, int(lead_id), "lead")
        if not at_least(user, "operator") and lead.agent_id != user.id:
            raise DomainError("not_found", "lead not found")
        prop_id = prop_id or lead.property_id
        deal_id = deal_id or lead.deal_id
    if deal_id:
        deal = get_or_404(db, Deal, int(deal_id), "deal")
        if not at_least(user, "operator") and deal.agent_id != user.id:
            raise DomainError("not_found", "deal not found")
        lead_id, prop_id = lead_id or deal.lead_id, prop_id or deal.property_id
    return {"lead_id": lead_id, "deal_id": deal_id, "property_id": prop_id}


def _data(data) -> dict | None:
    """The wizard's CxData (scalars only), kept for the restore and the deal prefill; not a dict = left as is."""
    if not isinstance(data, dict):
        return None
    return {str(k)[:64]: v for k, v in data.items() if isinstance(v, (str, int, float, bool)) and len(str(v)) <= (2000 if k == "extract_url" else 500)}


def _fields(db: Session, body: dict, user: User) -> dict:
    kind, lang, page = body.get("kind"), body.get("lang"), body.get("html") or ""
    if kind not in KINDS or lang not in LANGS:
        raise DomainError("bad_input", "kind / lang")
    if not page.strip() or len(page) > MAX_HTML:
        raise DomainError("bad_input", "html")
    f = {k: (str(body.get(k) or "").strip() or None) for k in ("title", "cadastral", "address", "client_name", "owner_name")}
    data = _data(body.get("data"))
    if data is not None:
        f["data"] = data
    return {**f, **_link(db, body, user), "kind": kind, "lang": lang, "html": clean_html(page)}


def _store_pdf(c: Contract, data: bytes, signed: bool = False) -> str:
    key = f"private/contracts/{c.id}/{uuid.uuid4().hex}.pdf"
    storage.put(key, data)
    old = c.signed_pdf_path if signed else None  # a generated PDF is kept in versions (regenerate), a scan is replaced
    if old:
        try:
            storage.delete(old)
        except Exception:  # noqa: BLE001
            log.warning("could not delete %s", old)
    return key


def _draft_for(db: Session, link: dict, user: User) -> Contract | None:
    """The deal's draft being filled (no PDF yet): one per lead / deal, so a reopened form continues it."""
    col, v = (Contract.lead_id, link["lead_id"]) if link["lead_id"] else (Contract.deal_id, link["deal_id"])
    return db.scalar(select(Contract).where(col == v, Contract.status == "draft", Contract.pdf_path.is_(None))
                     .order_by(Contract.id.desc()).with_for_update())


def autosave(db: Session, body: dict, user: User) -> Contract:
    """The wizard's debounced autosave, from the first field: the draft (any fill) in the deal, no PDF. With `id`, that
    draft (filled or generated) gets the new data; a generated one then shows «PDF устарел» until regenerated."""
    kind, lang = body.get("kind") or None, body.get("lang") or None
    if (kind and kind not in KINDS) or (lang and lang not in LANGS):
        raise DomainError("bad_input", "kind / lang")
    data = _data(body.get("data")) or {}
    if body.get("id"):
        c = get(db, int(body["id"]), user, lock=True)
        if c.status != "draft":
            raise DomainError("bad_status", "Подписанный или закрытый договор не меняется", 409)
    else:
        link = _link(db, body, user)
        c = _draft_for(db, link, user)
        if c is None:
            c = Contract(**link, created_by=user.id, status="draft")
            db.add(c)
            db.flush()
            log_event(db, "contract", c.id, "draft_started", user, lead_id=c.lead_id)
    core = lambda x: {k: v for k, v in (x or {}).items() if k not in DATA_META}  # noqa: E731
    if (kind or c.kind, lang or c.lang, core(data)) == (c.kind, c.lang, core(c.data)):
        c.data = data  # only the wizard's step / flags: the PDF stays current
        db.commit()
        return c
    c.kind, c.lang, c.data, c.updated_at = kind or c.kind, lang or c.lang, data, now()
    for col, key in (("cadastral", "cadastral_code"), ("address", "street_house"), ("client_name", "client_full_name"),
                     ("owner_name", "owner_full_name")):
        setattr(c, col, str(data.get(key) or "").strip() or None)
    db.commit()
    return c


def create(db: Session, body: dict, user: User) -> Contract:
    f = _fields(db, body, user)
    pdf = render_pdf(f["html"])
    c = Contract(**f, created_by=user.id)
    db.add(c)
    db.flush()
    c.pdf_path, c.generated_at = _store_pdf(c, pdf), now()
    c.updated_at = c.generated_at
    log_event(db, "contract", c.id, "created", user, lead_id=c.lead_id)
    db.commit()
    return c


def update(db: Session, cid: int, body: dict, user: User) -> Contract:
    """Generate the autosaved draft, or regenerate a generated one after an edit in the same form, while it is a draft.
    The previous PDF stays in `versions` (GET /contracts/{id}/pdf?version=N)."""
    c = get(db, cid, user, lock=True)
    if c.status != "draft":
        raise DomainError("bad_status", "Подписанный или закрытый договор не меняется", 409)
    f = _fields(db, body, user)
    if f["html"] != c.html:
        from app.services import esign
        esign.before_edit(db, c)  # unsigned e-sign links die with the old text; a signed text is frozen
    pdf = render_pdf(f["html"])
    if c.pdf_path:
        c.versions = [*(c.versions or []), {"pdf_path": c.pdf_path, "at": _iso(c.generated_at or c.created_at), "by": c.created_by}]
    for k, v in f.items():
        setattr(c, k, v)
    t = now()
    c.pdf_path, c.generated_at, c.updated_at = _store_pdf(c, pdf), t, t
    log_event(db, "contract", c.id, "regenerated" if c.versions else "generated", user)
    db.commit()
    return c


# ---------------------------------------------------------------- lifecycle

def sign(db: Session, cid: int, user: User, pdf: bytes | None = None) -> Contract:
    """«Договор подписан»: draft → signed, with the signed scan (PDF) or as a plain confirmation; the PDF (scan, else
    the generated one) is queued for Google Drive. A signed / deal contract may get its scan later the same way."""
    c = get(db, cid, user, lock=True)
    if c.status not in ("draft", "signed", "deal") or (c.status != "draft" and not pdf):
        raise DomainError("bad_status", f"Договор: {STATUS_LABEL[c.status]}", 409)
    if not c.pdf_path:
        raise DomainError("not_generated", "Сначала сгенерируйте договор (PDF)", 409)
    if c.status == "draft" and stale(c):
        raise DomainError("pdf_stale", "Данные изменены после генерации: обновите PDF перед подписью", 409)
    if pdf is not None:
        if not pdf.startswith(b"%PDF"):
            raise DomainError("bad_file", "Подписанный договор: нужен PDF")
        if len(pdf) > MAX_PDF:
            raise DomainError("too_large", f"Файл больше {MAX_PDF // 1024 // 1024} МБ")
        c.signed_pdf_path = _store_pdf(c, pdf, signed=True)
        c.drive_file_id = None  # the scan replaces what Drive got
    if c.status == "draft":
        c.status, c.signed_at, c.signed_by = "signed", now(), user.id
    c.updated_at = now()
    jobs.schedule(db, "contract_drive", c.id, now())
    log_event(db, "contract", c.id, "signed", user, scan=pdf is not None)
    db.commit()
    return c


def close(db: Session, cid: int, user: User, reason: str, comment: str | None = None) -> Contract:
    """The deal fell through: draft / signed → closed_no_deal with a required reason (other → a comment)."""
    c = get(db, cid, user, lock=True)
    if c.status not in ACTIVE:
        raise DomainError("bad_status", f"Договор: {STATUS_LABEL[c.status]}", 409)
    comment = (comment or "").strip() or None
    if reason not in CONTRACT_CLOSE_REASONS:
        raise DomainError("bad_input", "Укажите причину")
    if reason == "other" and not comment:
        raise DomainError("bad_input", "Причина «Другое»: напишите комментарий")
    c.status, c.close_reason, c.close_comment = "closed_no_deal", reason, comment
    c.closed_at, c.closed_by, c.updated_at = now(), user.id, now()
    c.share_token = c.share_expires_at = None
    log_event(db, "contract", c.id, "closed_no_deal", user, reason=reason, comment=comment)
    db.commit()
    return c


def mark_deal(db: Session, deal: Deal, actor: User | None = None) -> None:
    """The deal is confirmed (services/deals.confirm): the lead's active contracts → deal. Does not commit."""
    if not deal.lead_id:
        return
    for c in db.scalars(select(Contract).where(Contract.lead_id == deal.lead_id, Contract.status.in_(ACTIVE),
                                               Contract.pdf_path.isnot(None))):  # a draft still being filled stays one
        c.status, c.deal_id, c.updated_at = "deal", deal.id, now()
        log_event(db, "contract", c.id, "deal", actor, deal_id=deal.id)


# ---------------------------------------------------------------- files, share links

def pdf_bytes(c: Contract, signed: bool = False, version: int | None = None) -> bytes:
    """The current PDF (signed: the scan if there is one); version: an earlier generated PDF (0 = the first)."""
    vs = c.versions or []
    if version is not None:
        key = vs[version]["pdf_path"] if 0 <= version < len(vs) else None
    else:
        key = (c.signed_pdf_path if signed else None) or c.pdf_path
    data = storage.read(key) if key else None
    if not data:
        raise DomainError("not_found", "PDF не найден")
    return data


def share_path(token: str | None) -> str:
    return f"/api/public/contracts/{token}/pdf" if token else ""


def share(db: Session, cid: int, user: User) -> Contract:
    """An unguessable read-only link to the PDF, CONTRACT_SHARE_DAYS long (reused while it has a day left)."""
    c = get(db, cid, user, lock=True)
    if c.status == "closed_no_deal":
        raise DomainError("bad_status", "Договор закрыт", 409)
    if not c.pdf_path:
        raise DomainError("not_generated", "Сначала сгенерируйте договор (PDF)", 409)
    if not c.share_token or not c.share_expires_at or c.share_expires_at < now() + timedelta(days=1):
        c.share_token = secrets.token_urlsafe(24)
    c.share_expires_at = now() + timedelta(days=env.CONTRACT_SHARE_DAYS)
    log_event(db, "contract", c.id, "shared", user)
    db.commit()
    return c


def by_share_token(db: Session, token: str) -> Contract:
    c = db.scalar(select(Contract).where(Contract.share_token == token)) if token and len(token) >= 20 else None
    if not c or not c.share_expires_at or c.share_expires_at < now() or c.status == "closed_no_deal":
        raise DomainError("not_found", "Ссылка недействительна или истекла")
    return c


# ---------------------------------------------------------------- Google Drive (worker job contract_drive)

DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true&fields=id"


def drive_configured() -> str | None:
    p = env.GOOGLE_SA_JSON
    return p if p and os.path.isfile(p) and env.GDRIVE_CONTRACTS_FOLDER else None


def _drive_token(sa_path: str) -> str:
    info = json.loads(Path(sa_path).read_text())
    t = int(time.time())
    claims = {"iss": info["client_email"], "scope": "https://www.googleapis.com/auth/drive", "aud": info["token_uri"],
              "iat": t, "exp": t + 3600}
    if env.GOOGLE_SA_SUBJECT:
        claims["sub"] = env.GOOGLE_SA_SUBJECT
    r = httpx.post(info["token_uri"], timeout=30, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                                        "assertion": jwt.encode(claims, info["private_key"], algorithm="RS256")})
    if r.status_code != 200:
        raise RuntimeError(f"google token {r.status_code}: {r.text[:300]}")
    return r.json()["access_token"]


def drive_put(sa_path: str, name: str, data: bytes) -> str:
    b = uuid.uuid4().hex
    meta = json.dumps({"name": name, "parents": [env.GDRIVE_CONTRACTS_FOLDER], "mimeType": "application/pdf"})
    body = (f"--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n--{b}\r\n"
            "Content-Type: application/pdf\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
    r = httpx.post(DRIVE_UPLOAD, content=body, timeout=120, headers={
        "Authorization": f"Bearer {_drive_token(sa_path)}", "Content-Type": f"multipart/related; boundary={b}"})
    if r.status_code != 200:  # e.g. 403 storageQuotaExceeded: the folder must be on a shared drive (docs/CONTRACTS.md)
        raise RuntimeError(f"drive upload {r.status_code}: {r.text[:300]}")
    return r.json()["id"]


def drive_upload(db: Session, cid: int) -> str:
    """Worker: the signed contract's PDF → GDRIVE_CONTRACTS_FOLDER. Errors raise: the worker retries with backoff."""
    c = db.get(Contract, cid)
    if not c or c.status not in ("signed", "deal") or c.drive_file_id:
        return "stale"
    sa = drive_configured()
    if not sa:
        log.warning("drive: not configured (GOOGLE_SA_JSON file and GDRIVE_CONTRACTS_FOLDER in .env), skipping contract %s", c.id)
        return "skipped"
    c.drive_file_id = drive_put(sa, filename(c), pdf_bytes(c, signed=True))
    log_event(db, "contract", c.id, "drive_uploaded", None, file_id=c.drive_file_id)
    return "ok"
