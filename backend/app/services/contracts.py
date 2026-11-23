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
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import storage
from app.config import REPO_ROOT, settings as env
from app.models import CONTRACT_CLOSE_REASONS, Client, Contract, ContractSigner, Deal, Event, Lead, Property, User, at_least
from app.services import jobs
from app.services.common import get_or_404, log_event, notify_later, now, now_text
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
         "lead_deal_id": lead.deal_id if lead else None,  # the lead's deal before its confirm sets deal_id (11-18)
         "title": c.title, "cadastral": c.cadastral, "address": c.address, "client_name": c.client_name,
         "owner_name": c.owner_name, "lead_id": c.lead_id, "deal_id": c.deal_id, "property_id": c.property_id,
         "created_by": _name(db, c.created_by), "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at),
         "signed_at": _iso(c.signed_at), "deal_due_at": _iso(c.deal_due_at), "signed_by": _name(db, c.signed_by), "has_signed_pdf": bool(c.signed_pdf_path),
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
        d["client_tg"] = (r := recipient(db, c)) and {"label": r["label"]}
        ev = db.scalar(select(Event).where(Event.entity == "contract", Event.entity_id == str(c.id), Event.action == "sent_to_client")
                       .order_by(Event.id.desc()).limit(1))
        d["sent_to_client"] = ev and {"at": _iso(ev.at), "by": ev.actor_label, **ev.data}
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
          deal_id: int | None = None, agent_id: int | None = None, property_id: int | None = None):
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
    if property_id:
        stmt = stmt.where(Contract.property_id == property_id)
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
    """The deal a contract belongs to: the lead («сделка» at the contract stage, the Deal row comes after signing) or
    the Deal itself; the other ids follow from it. Optional since 11-13 (Stanislav): a contract without a lead / deal
    (the parties came directly, the deal isn't in the CRM yet) belongs to its creator; «Привязать к заявке» later."""
    lead_id = body.get("lead_id") or None
    deal_id = body.get("deal_id") or None
    prop_id = body.get("property_id") or None
    linked = None  # the object of the lead / deal
    if lead_id:
        lead = get_or_404(db, Lead, int(lead_id), "lead")
        if not at_least(user, "operator") and lead.agent_id != user.id:
            raise DomainError("not_found", "lead not found")
        linked = lead.property_id
        deal_id = deal_id or lead.deal_id
    if deal_id:
        deal = get_or_404(db, Deal, int(deal_id), "deal")
        if not at_least(user, "operator") and deal.agent_id != user.id:
            raise DomainError("not_found", "deal not found")
        lead_id, linked = lead_id or deal.lead_id, linked or deal.property_id
    # with a lead / deal the object is its own, never another one sent along: a contract opens its object's card to
    # its creator (properties.can_open); an explicit object only where the lead / deal has none, and only one he may use
    if linked:
        prop_id = linked
    elif prop_id:  # also «Оформить договор аренды» in the object card (Irakli 11-15)
        from app.services import properties
        _own_object(db, int(prop_id), user)  # another agent's: 403, as the deal form
        properties.get_visible(db, int(prop_id), user)  # a card he can't open (a platform object): 404, as the card
    return {"lead_id": lead_id, "deal_id": deal_id, "property_id": prop_id}


def _own_object(db: Session, pid: int, user: User) -> Property:
    """Another agent's object: his agent draws up its contract, as its deal (properties.deal_block)."""
    from app.services import properties
    p = get_or_404(db, Property, pid, "property")
    if blocked := properties.deal_block(db, p, user):
        raise DomainError("forbidden", f"{blocked}: договор по нему оформляет его агент", 403)
    return p


def property_prefill(db: Session, pid: int, user: User) -> dict:
    """The object card → the wizard (CxData keys); the owner only for those who see him (properties.deal_fields)."""
    from app.services import properties
    properties.get_visible(db, pid, user)  # a card he can't open: 404, as the card
    p = _own_object(db, pid, user)
    f = properties.deal_fields(db, p, user)
    num = lambda v: f"{float(v):g}" if v else ""  # noqa: E731
    data = {"street_house": p.address or "", "cadastral_code": p.cadastral_code or "", "floor": str(p.floor or ""),
            "apartment_area": num(p.area), "price_usd": num(f.get("price")), "deposit_usd": num(f.get("price")),
            "owner_full_name": f.get("owner_name") or ""}
    return {"property_id": p.id, "type": "rent", "data": data, "district": p.district,
            "owner": {"name": f.get("owner_name"), "phone": f.get("owner_phone")}}


def _data(data) -> dict | None:
    """The wizard's CxData (scalars only), kept for the restore and the deal prefill; not a dict = left as is."""
    if not isinstance(data, dict):
        return None
    out = {str(k)[:64]: v for k, v in data.items() if isinstance(v, (str, int, float, bool)) and len(str(v)) <= (2000 if k == "extract_url" else 500)}
    edits = _edits(data.get("_edits"))
    if edits:
        out["_edits"] = edits
    return out


def _edits(e) -> dict | None:
    """The wizard's clause edits (11-18, frontend templates.ts CxEdits): {"<draft>:<path>": text | null (removed),
    "<draft>:<path>+": [added paragraphs]}. Text only; bounded so a contract's data stays small."""
    if not isinstance(e, dict):
        return None
    ok = lambda s: isinstance(s, str) and len(s) <= 10_000  # noqa: E731
    out = {}
    for k, v in list(e.items())[:500]:
        if not isinstance(k, str) or len(k) > 64:
            continue
        if v is None or ok(v):
            out[k] = v
        elif isinstance(v, list) and (k.endswith("+") or k.endswith(":auto")):  # :auto = the autotranslated paths
            out[k] = [x for x in v[:50] if ok(x)]
    return out


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
    """The deal's draft being filled (no PDF yet): one per lead / deal, so a reopened form continues it. A standalone
    contract has none to continue: the wizard keeps its id after the first autosave (11-13)."""
    if link["lead_id"] or link["deal_id"]:
        col, v = (Contract.lead_id, link["lead_id"]) if link["lead_id"] else (Contract.deal_id, link["deal_id"])
        cond = col == v
    elif link["property_id"]:  # from the object card: the agent's own draft on the object (11-15)
        cond = (Contract.property_id == link["property_id"]) & Contract.lead_id.is_(None) & Contract.deal_id.is_(None) \
            & (Contract.created_by == user.id)
    else:
        return None
    return db.scalar(select(Contract).where(cond, Contract.status == "draft", Contract.pdf_path.is_(None))
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
        # the wizard's editing session key (data._key): a retried / overlapping first autosave that went out before the
        # first one returned its id gets that same draft back instead of a second one. The user's row lock serializes
        # his keyless-id creates, so two such requests can't both miss the draft (no column: the key lives in data).
        key = data.get("_key") if isinstance(data.get("_key"), str) and 8 <= len(data["_key"]) <= 64 else None
        c = None
        if key:
            db.scalar(select(User.id).where(User.id == user.id).with_for_update())
            c = db.scalar(select(Contract).where(Contract.created_by == user.id, Contract.status == "draft",
                                                 Contract.created_at >= now() - timedelta(days=2),
                                                 Contract.data["_key"].astext == key)
                          .order_by(Contract.id.desc()).limit(1).with_for_update())
        c = c or _draft_for(db, link, user)
        if c is None:
            c = Contract(**link, created_by=user.id, status="draft")
            db.add(c)
            db.flush()
            log_event(db, "contract", c.id, "draft_started", user, lead_id=c.lead_id)
    core = lambda x: {k: v for k, v in (x or {}).items() if k not in DATA_META and k != "_key"}  # noqa: E731
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
    if not body.get("lead_id") and not body.get("deal_id"):  # a form without a link keeps the contract's own (11-13)
        body = {**body, "lead_id": c.lead_id, "deal_id": c.deal_id, "property_id": body.get("property_id") or c.property_id}
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


def link(db: Session, cid: int, body: dict, user: User) -> Contract:
    """«Привязать к заявке»: a standalone contract joins a lead / deal later (11-13); a linked one keeps its link."""
    c = get(db, cid, user, lock=True)
    if c.lead_id or c.deal_id:
        raise DomainError("already_linked", "Договор уже привязан к заявке или сделке", 409)
    if c.status == "closed_no_deal":
        raise DomainError("bad_status", "Закрытый договор не меняется", 409)
    ids = _link(db, body, user)
    if not ids["lead_id"] and not ids["deal_id"]:
        raise DomainError("bad_input", "Выберите заявку или сделку")
    c.lead_id, c.deal_id, c.property_id = ids["lead_id"], ids["deal_id"], c.property_id or ids["property_id"]
    if c.deal_id and c.status == "signed":  # as mark_deal: the deal is already there
        c.status = "deal"
    c.updated_at = now()
    log_event(db, "contract", c.id, "linked", user, lead_id=c.lead_id, deal_id=c.deal_id)
    db.commit()
    return c


LANG_CLIENT = {"RU": "ru", "EN": "en", "GEO": "ka"}


def deal_lead(db: Session, cid: int, user: User) -> Contract:
    """«Оформить сделку» on a signed contract (Irakli 11-18): the deal form works on a lead, so a contract without one
    (made from the object card) gets it here — the same client's live lead on this object if there is one, else a new
    client + lead from the contract (name, the tenant's e-sign phone) at «Договор», contacts open to its agent.
    The contract's creator or staff only (get(): another agent's is 404); a contract with a lead returns as is."""
    from app.services import leads
    c = get(db, cid, user, lock=True)
    if c.status not in ("signed", "deal"):
        raise DomainError("bad_status", "Сделку оформляют после подписания договора", 409)
    if c.lead_id or c.deal_id:
        return c
    if not at_least(user, "operator") and c.created_by != user.id:
        raise DomainError("not_found", "contract not found")
    creator = db.get(User, c.created_by) if c.created_by else None
    prop = db.get(Property, c.property_id) if c.property_id else None
    agent_id = creator.id if creator and not at_least(creator, "operator") else (prop.agent_id if prop else None)
    signer = db.scalar(select(ContractSigner).where(ContractSigner.contract_id == c.id, ContractSigner.role == "tenant"))
    name = (c.client_name or (signer.full_name if signer else None) or "").strip() or None
    phone = ((signer.phone if signer else None) or "").strip() or None
    digits = re.sub(r"\D", "", phone or "")[-9:]
    same = []  # the same client: by the phone's last 9 digits, else by the name
    if len(digits) == 9:
        same.append(func.right(func.regexp_replace(Client.phone, r"\D", "", "g"), 9) == digits)
    if name:
        same.append(func.lower(Client.name) == name.lower())
    lead = None
    if prop and same:
        lead = db.scalar(select(Lead).join(Client, Client.id == Lead.client_id)
                         .where(Lead.property_id == prop.id, Lead.archived_at.is_(None), Lead.status.notin_(("rented", "refused")),
                                or_(*same), *([Lead.agent_id == agent_id] if agent_id else []))
                         .order_by(Lead.id.desc()).limit(1).with_for_update(of=Lead))
    t = now_text()
    if lead is None:
        client = db.scalar(select(Client).where(Client.phone.isnot(None), same[0]).limit(1)) if len(digits) == 9 else None
        if client is None:
            client = Client(channel="crm", external_id=f"contract:{c.id}", name=name, phone=phone,
                            lang=LANG_CLIENT.get(c.lang or "", "ru"), state="{}", profile="{}", created_at=t, updated_at=t)
            db.add(client)
            db.flush()
        lead = Lead(client_id=client.id, property_id=c.property_id, agent_id=agent_id, status="contract",
                    contacts_shared_at=now(), request=f"Договор #{c.id}", created_at=t, updated_at=t)
        db.add(lead)
        db.flush()
        leads._event(db, lead, "created", user, label="contract", contract_id=c.id)
    elif lead.status not in leads.DEAL_STAGES:  # an earlier stage: the signed contract moves it to «Договор»
        leads._touch(lead, "contract")
        leads._set_sla(db, lead, None)
        leads._event(db, lead, "contract_started", user, contract_id=c.id)
    if not lead.contacts_shared_at:
        lead.contacts_shared_at = now()
    c.lead_id, c.property_id, c.updated_at = lead.id, c.property_id or lead.property_id, now()
    log_event(db, "contract", c.id, "linked", user, lead_id=lead.id, auto=True)
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
        start_deal_due(db, c)
        notify_later(db, "contract_event", c, "✍️ <b>Договор подписан</b> (вручную)", user.id)
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
    c.share_token = c.share_expires_at = c.deal_due_at = None
    jobs.cancel(db, "deal_due", c.id)
    log_event(db, "contract", c.id, "closed_no_deal", user, reason=reason, comment=comment)
    notify_later(db, "contract_event", c, f"❌ <b>Договор закрыт без сделки: {CONTRACT_CLOSE_REASONS[reason]}</b>", user.id,
                 True, comment)
    db.commit()
    return c


DEAL_DUE_HOURS = 24
DEAL_OVERDUE = "deal_overdue"


def start_deal_due(db: Session, c: Contract) -> None:
    """Both parties signed («Договор подписан» or the e-signature): the deal is due in 24 h (David 11-23); at the
    deadline the worker's deal_due warns the agent and the operator, and overdue_for locks the agent's CRM."""
    c.deal_due_at = now() + timedelta(hours=DEAL_DUE_HOURS)
    jobs.schedule(db, "deal_due", c.id, c.deal_due_at)


def owes_deal(db: Session, c: Contract) -> bool:
    """A signed contract with no deal entered yet (a pending one counts as entered)."""
    if c.status != "signed" or c.deal_id:
        return False
    if c.lead_id:
        mine = Deal.lead_id == c.lead_id
    elif c.property_id and c.signed_at:
        mine = (Deal.property_id == c.property_id) & (Deal.created_at >= c.signed_at)
    else:
        return True
    return db.scalar(select(Deal.id).where(mine, Deal.status != "cancelled").limit(1)) is None


def deal_owers(db: Session, c: Contract) -> list[int]:
    """Who must enter the deal: the lead's agent and the contract's author (an agent or a recruit), never staff."""
    lead = db.get(Lead, c.lead_id) if c.lead_id else None
    ids = dict.fromkeys(x for x in (lead.agent_id if lead else None, c.created_by) if x)
    return [u.id for u in (db.get(User, i) for i in ids) if u and not at_least(u, "operator")]


def overdue_for(db: Session, user: User) -> Contract | None:
    """The user's signed contract whose 24 h for the deal ran out: until the deal is entered his CRM opens only it."""
    if at_least(user, "operator"):
        return None
    mine = or_(Contract.created_by == user.id, Contract.lead_id.in_(select(Lead.id).where(Lead.agent_id == user.id)))
    for c in db.scalars(select(Contract).where(Contract.status == "signed", Contract.deal_due_at <= now(), mine)
                        .order_by(Contract.deal_due_at)):
        if owes_deal(db, c) and user.id in deal_owers(db, c):
            return c
    return None


def overdue_out(c: Contract | None) -> dict | None:
    return c and {"id": c.id, "address": c.address, "client_name": c.client_name, "lead_id": c.lead_id,
                  "due_at": c.deal_due_at.isoformat()}


def lift_deal_due(db: Session, cid: int, user: User) -> Contract:
    """The operator lifts the lock (the contract was cancelled, the deal is entered elsewhere, …)."""
    c = get(db, cid, user, lock=True)
    c.deal_due_at, c.updated_at = None, now()
    jobs.cancel(db, "deal_due", c.id)
    log_event(db, "contract", c.id, "deal_due_lifted", user)
    db.commit()
    return c


def mark_deal(db: Session, deal: Deal, actor: User | None = None) -> None:
    """The deal is confirmed (services/deals.confirm): the lead's active contracts → deal, and so do the contracts made
    from the object card without a lead (11-15: the object turns «Сдано» by the same confirm). Does not commit."""
    mine = Contract.lead_id == deal.lead_id if deal.lead_id else None
    if deal.property_id:
        own = (Contract.property_id == deal.property_id) & Contract.lead_id.is_(None) & Contract.deal_id.is_(None)
        mine = own if mine is None else mine | own
    if mine is None:
        return
    for c in db.scalars(select(Contract).where(mine, Contract.status.in_(ACTIVE),
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


# ---------------------------------------------------------------- to the client from the operator's Telegram (11-13)

KIND_TEXT = {"rent": "аренды", "comm": "аренды", "sale": "купли-продажи"}
TG_NAME = re.compile(r"^(?:@|(?:https?://)?t\.me/)?([A-Za-z][A-Za-z0-9_]{3,31})/?$")


def tg_peer(to: str) -> str | None:
    """What the agent typed as the recipient: «@name» / «t.me/name» / «name» -> «@name»; a phone -> «+995…»."""
    to = (to or "").strip()
    if m := TG_NAME.match(to):
        return "@" + m[1]
    d = re.sub(r"\D", "", to) if re.fullmatch(r"[+\d\s()-]{9,20}", to) else ""
    d = "995" + d if len(d) == 9 else d  # a Georgian mobile without the code (as notify.intl_phone)
    return "+" + d if len(d) >= 10 else None


def recipient(db: Session, c: Contract) -> dict | None:
    """The tenant / buyer = the linked lead's client: a Telegram client row (the userbot knows it by id), else his
    phone -> {client_id | peer, label}; None = the agent types it («@username» / phone)."""
    lead = db.get(Lead, c.lead_id) if c.lead_id else None
    if not lead and c.deal_id and (deal := db.get(Deal, c.deal_id)) and deal.lead_id:
        lead = db.get(Lead, deal.lead_id)
    cl = db.get(Client, lead.client_id) if lead and lead.client_id else None
    if not cl:
        return None
    who = cl.name or f"клиент #{cl.id}"
    if cl.channel == "tg":
        return {"client_id": cl.id, "label": f"{who} (@{cl.username})" if cl.username else f"{who} (Telegram)"}
    peer = tg_peer(cl.phone or "")
    return {"peer": peer, "label": f"{who} ({peer})"} if peer and peer.startswith("+") else None


def send_to_client(db: Session, cid: int, user: User, to: str | None = None) -> Contract:
    """«Отправить клиенту в Telegram»: the current PDF (the signed scan once there is one) + the registry extract as
    documents from the operator's account (userbot). Only on the agent's click, never on regeneration: an outward send.
    to: the agent's recipient (contracts without a lead / without the client's Telegram) over the lead's client."""
    from app.services import registry
    from app.telegram import notify, userbot
    c = get(db, cid, user)
    if c.status == "closed_no_deal":
        raise DomainError("bad_status", "Договор закрыт", 409)
    if not c.pdf_path:
        raise DomainError("not_generated", "Сначала сгенерируйте договор (PDF)", 409)
    if c.status == "draft" and stale(c):
        raise DomainError("pdf_stale", "Данные изменены после генерации: обновите PDF перед отправкой", 409)
    if to and (to or "").strip():
        peer = tg_peer(to)
        if not peer:
            raise DomainError("bad_input", "Получатель: @username в Telegram или телефон")
        r = {"peer": peer, "label": peer}
    else:
        r = recipient(db, c)
    if not r:
        raise DomainError("no_recipient", "У клиента нет Telegram: укажите @username или телефон получателя", 409)
    from app.telegram.client import conf
    if not conf("USERBOT_URL").strip():
        raise DomainError("userbot_off", notify.USERBOT_OFF, 409)
    signed = bool(c.signed_pdf_path)
    files = [(filename(c, signed), pdf_bytes(c, signed))]
    key = str((c.data or {}).get("extract_file") or "")
    if registry.EXTRACT_KEY_RE.match(key) and (ex := storage.read(key)):
        files.append((f"Выписка из реестра {c.cadastral or c.id}.pdf", ex))
    where = c.address or (f"кад. код {c.cadastral}" if c.cadastral else "")
    what = " ".join(x for x in ("Договор", KIND_TEXT.get(c.kind or ""), where and f"— {where}") if x)
    text = (f"Здравствуйте! {what} во вложении{', вместе с выпиской из публичного реестра' if len(files) > 1 else ''}. "
            "Посмотрите, пожалуйста; если будут вопросы — напишите сюда.")
    if not userbot.send_files(text, files, r.get("client_id"), r.get("peer")):
        raise DomainError("send_failed", f"{notify.USERBOT_FAILED}: проверьте получателя ({r['label']})", 502)
    log_event(db, "contract", c.id, "sent_to_client", user, to=r["label"], extract=len(files) > 1,
              version="подписанный" if signed else len(c.versions or []) + 1)
    db.commit()
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


# ---- autotranslation of an edited clause (David 11-23): the agent writes Russian, the Georgian column follows

TR_LANGS = {"GEO": "Georgian", "RU": "Russian", "EN": "English"}


def _llm():
    """bot/llm.py: the project's one model client (OPENAI_* / LLM_MODEL env, CRM keys, Расходы)."""
    import sys
    if str(REPO_ROOT / "bot") not in sys.path:
        sys.path.append(str(REPO_ROOT / "bot"))
    import llm
    return llm


def translate(text: str, to: list[str]) -> dict[str, str]:
    """A contract paragraph into each of `to` (GEO / RU / EN): {lang: text}. Failure = DomainError (the edit itself is kept)."""
    langs = [x for x in dict.fromkeys(to) if x in TR_LANGS]
    if not text.strip() or not langs:
        return {}
    schema = {"type": "object", "additionalProperties": False, "required": langs,
              "properties": {x: {"type": "string"} for x in langs}}
    prompt = ("You translate one paragraph of a Georgian residential lease contract (legal style, as in the rest of the "
              f"contract). Translate it into: {', '.join(f'{x} = {TR_LANGS[x]}' for x in langs)}. If the paragraph is "
              "already in a target language, return it unchanged for that one. Keep clause numbers, amounts, dates, names, "
              "ID numbers, addresses as written, the **bold** markers around the same words and [[field]] placeholders as is. "
              "No comments, only the translations.\n\nParagraph:\n" + text)
    try:
        out = _llm().ask(prompt, schema, retries=1, timeout=90, purpose="contract_translate")
    except Exception as e:  # noqa: BLE001
        log.warning("contract translate failed: %s", e)
        raise DomainError("translate_failed", "Автоперевод не удался", 502) from e
    return {x: str(out.get(x) or "").strip() for x in langs if str(out.get(x) or "").strip()}
