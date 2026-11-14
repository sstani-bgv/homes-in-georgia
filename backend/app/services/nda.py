"""NDA for new agents, signed electronically (docs/NDA.md; the HiG Sign idea, PlatformDGE/hig-sign, moved into the CRM).

Staff: «Новое NDA» — the agent's full name, personal ID / passport, phone, Telegram, role and the language (KA, KA-RU,
KA-EN). The text is rendered and frozen (doc_sha256); the company's signature (David draws it once: company_signature)
goes in at once. The CRM gives a personal link /nda/<token> to send the agent.

The agent: opens the link (no CRM login), reads the agreement, confirms himself with Telegram (Login Widget — Telegram
asks him to confirm in the app; the server checks the answer's HMAC with the bot token; if the NDA names a @username
only that account passes), draws the signature, ticks the consent, signs. The server prints the signed PDF (both
signatures in the «Signatures» table + a last page with the signing record: Telegram id / @username, the confirmation's
signed hash = the «confirmation code» of clause 5, time in Tbilisi, IP, device, SHA-256 of the text) and sends it to the
staff member who made the NDA and to the agent (Telegram), and to Google Drive when configured."""
import base64
import hashlib
import html as H
import json
import logging
import re
import secrets
import struct
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import storage
from app.config import settings as env
from app.models import STAFF, SUPER, AgentNda, User
from app.services import contracts as C, jobs
from app.services.common import TZ, log_event, now
from app.services.errors import DomainError, forbidden
from app.services.nda_text import (APPENDIX, BLOCKS, COMPANY, EDITION, LEVELS, REQUISITES, ROLES, SHORT, SIGNATURES,
                                   SUBTITLE, TITLE)

log = logging.getLogger(__name__)
LANGS = ("KA", "KA-RU", "KA-EN")
SECOND = {"KA": None, "KA-RU": "ru", "KA-EN": "en"}
COMPANY_SIG_KEY = "private/nda/company-signature.json"
CONFIRM_TTL = timedelta(hours=1)  # the Telegram confirmation is good for this long before signing
PNG_PREFIX = "data:image/png;base64,"
MAX_SIGNATURE = 400_000
STATUS_LABEL = {"sent": "Отправлено", "signed": "Подписано", "cancelled": "Отменено"}


# ---------------------------------------------------------------- the text

def _md(s: str) -> str:
    """Escaped text with «**bold**»."""
    return "".join(f"<b>{p}</b>" if i % 2 else p for i, p in enumerate(H.escape(s).split("**")))


def _fill(s: str, f: dict) -> str:
    return s.format(**{k: f"**{v}**" if v and v != "—" else "—" for k, v in f.items()})


CSS = """@page{size:A4;margin:16mm 15mm 18mm;@bottom-center{content:"Homes in Georgia · № {num} · {lang} · {edition} · " counter(page);
font:7.5pt 'Noto Sans Georgian','DejaVu Sans',Arial,sans-serif;color:#888}}
body{margin:0;font-family:'Noto Sans Georgian','DejaVu Sans',Arial,sans-serif;font-size:9.3pt;line-height:1.35;color:#111}
h1{font-size:13pt;text-align:center;color:#1D3560;margin:0 0 2px}h1+h1{margin-top:0}.sub{text-align:center;color:#666;margin:2px 0 12px}
h2{font-size:12pt;color:#1D3560;margin:0 0 6px}h3{font-size:10.5pt;color:#2F5394;margin:9px 0 3px}p{margin:0 0 5px;text-align:justify}
.cols{width:100%;border-collapse:collapse;table-layout:fixed}.cols>tbody>tr>td{vertical-align:top;padding:0 7px;width:50%}
.cols>tbody>tr>td:first-child{padding-left:0}.cols>tbody>tr>td:last-child{padding-right:0}.cols>tbody>tr>td[colspan]{padding:0}
.li{display:flex;gap:6px;margin:0 0 3px 8px}.li span:first-child{min-width:14px}
.cl{display:flex;gap:6px;margin:0 0 5px}.cl>span:first-child{min-width:26px;color:#2F5394;font-weight:700}
.cl>span:last-child{flex:1}.page{break-before:page}
.grid{width:100%;border-collapse:collapse;margin:4px 0 8px;font-size:8pt;line-height:1.25}
.grid th{background:#1D3560;color:#fff;padding:4px 5px;font-weight:700;text-align:left;vertical-align:top}
.grid td{border:1px solid #c8cfdb;padding:4px 5px;vertical-align:top;height:12px;overflow-wrap:break-word}.grid i{display:block;font-style:normal;opacity:.75;font-size:7.3pt}
.grid th i{font-weight:400}.wrap{overflow-x:auto}
.sig{width:100%;border-collapse:collapse;margin-top:6px;font-size:9pt;table-layout:fixed}.sig th{background:#1D3560;color:#fff;padding:5px;font-weight:700}
.sig td{border:1px solid #c8cfdb;padding:5px;vertical-align:middle;height:16px;overflow-wrap:anywhere}.sig td:first-child{color:#444;width:28%}
.sig img{height:13mm;max-width:100%}.slot{color:#777}
.rec{break-before:page;font-size:9pt}.rec h2{font-size:12pt;text-align:center;color:#1D3560;margin:0 0 10px}
.rec table{width:100%;border-collapse:collapse;margin:6px 0 12px}.rec td{border:1px solid #888;padding:4px 6px;vertical-align:top}
.rec td:first-child{width:36%;color:#333}.rec .mono{font-family:monospace;font-size:8pt;word-break:break-all}.rec img{height:18mm}
@media screen and (max-width:700px){.cols>tbody>tr>td{display:block;width:auto;padding:0 0 8px}}"""


def _sig_table(lang: str, f: dict) -> str:
    """The parties' details and signatures (one table per language column): the Operator | the Participant."""
    t = lambda k: H.escape((REQUISITES.get(k) or SIGNATURES[k])[lang])  # noqa: E731
    e = lambda v: H.escape(v) if v else "—"  # noqa: E731
    slot = lambda name, text: f'<span class="slot" data-slot="{name}">{H.escape(text)}</span>'  # noqa: E731
    contacts = " · ".join(v for v in (f["phone"], f["email"], f["telegram"]) if v and v != "—") or "—"
    name = f["name_ka"] if lang == "ka" else f["name2"]
    rows = [("name", COMPANY["name"][lang], name), ("ie", COMPANY["ie"], f["ie"]), ("pid", COMPANY["pid"], f["pid"]),
            ("address", COMPANY["address"][lang], f["address"]), ("iban_gel", COMPANY["iban_gel"], f["iban_gel"]),
            ("iban_usd", COMPANY["iban_usd"], f["iban_usd"]), ("contacts", COMPANY["contacts"], contacts)]
    return (f'<h3>{t("title")}</h3><table class="sig"><tr><th></th><th>{t("company")}</th><th>{t("recipient")}</th></tr>'
            + "".join(f'<tr><td>{t(k)}</td><td>{e(a)}</td><td>{e(b)}</td></tr>' for k, a, b in rows)
            + f'<tr><td>{t("signature")}</td><td>{slot("company-sig", SIGNATURES["pending"][lang])}</td>'
            f'<td>{slot("recipient-sig", SIGNATURES["pending"][lang])}</td></tr>'
            f'<tr><td>{t("time")}</td><td>{slot("company-time", SIGNATURES["auto"][lang])}</td>'
            f'<td>{slot("recipient-time", SIGNATURES["auto"][lang])}</td></tr></table>')


def render(lang: str, f: dict) -> str:
    """The agreement's HTML (A4 print CSS) for the participant's fields f (nda_text: {num} {date} {name_ka} {name2} …;
    role_ka / role2 come from f["role"], a ROLES key)."""
    second = SECOND[lang]
    langs = ["ka"] + ([second] if second else [])
    f = {k: (str(v).strip() if v else "—") for k, v in f.items()}
    role = ROLES.get(f.pop("role", ""), None)
    per = {ln: {**f, "role_ka": role["ka"] if role else "—", "role2": role[ln if ln != "ka" else "ru"] if role else "—"}
           for ln in ("ka", "ru", "en")}
    fill = lambda texts, ln: _md(_fill(texts[ln], per[ln]))  # noqa: E731

    def cell(b: dict, ln: str) -> str:
        k = b["k"]
        if k == "h":
            return f'<h3>{H.escape(b["n"])}. {fill(b, ln)}</h3>'
        if k == "sub":
            return f"<h3>{fill(b, ln)}</h3>"
        if k == "app":
            return f'<h2>{H.escape(APPENDIX[ln])} {H.escape(b["n"])}. {fill(b, ln)}</h2>'
        if k in ("n", "li"):
            return f'<div class="cl"><span>{H.escape(b["n"])}</span><span>{fill(b, ln)}</span></div>' if b[ln] else \
                f'<div class="cl"><span>{H.escape(b["n"])}</span><span></span></div>'
        return f"<p>{fill(b, ln)}</p>"

    def grid(b: dict) -> str:
        two = lambda c, tag: (f"{fill(c, 'ka')}" + (f"<i>{fill(c, second)}</i>" if second and c[second] != c["ka"] and c[second] else ""))  # noqa: E731
        rows = b["rows"] or [[{"ka": "", "ru": "", "en": ""} for _ in b["head"]] for _ in range(2)]
        return ('<div class="wrap"><table class="grid"><tr>' + "".join(f"<th>{two(c, 'th')}</th>" for c in b["head"]) + "</tr>"
                + "".join("<tr>" + "".join(f"<td>{two(c, 'td')}</td>" for c in row) + "</tr>" for row in rows)
                + "</table></div>")

    # sections: the agreement, the parties' details + signatures, each appendix on a new page
    sections: list[list[str]] = [[]]
    sig_done = False
    for b in BLOCKS:
        if b.get("only") and lang not in b["only"].split():
            continue
        if b["k"] == "app":
            if not sig_done:
                sections.append([("sig", None)])
                sig_done = True
            sections.append([])
        sections[-1].append(("t", grid(b)) if b["k"] == "t" else ("row", [cell(b, ln) for ln in langs]))
    if not sig_done:
        sections.append([("sig", None)])

    def section(items, i) -> str:
        cls = ' class="page"' if i and not any(kind == "sig" for kind, _ in items) else ""
        if any(kind == "sig" for kind, _ in items):
            cells = [_sig_table(ln, per[ln]) for ln in langs]
            items = [("row", cells)]
        if second:
            trs = "".join(f'<tr><td colspan="2">{x}</td></tr>' if kind == "t" else f"<tr><td>{x[0]}</td><td>{x[1]}</td></tr>"
                          for kind, x in items)
            return f'<div{cls}><table class="cols"><tbody>{trs}</tbody></table></div>'
        return f"<div{cls}>" + "".join(x if kind == "t" else x[0] for kind, x in items) + "</div>"

    head = f'<h1>{H.escape(TITLE["ka"])}</h1>' + (f'<h1>{H.escape(TITLE[second])}</h1>' if second else "") + \
        f'<div class="sub">{H.escape(SUBTITLE)} · № {H.escape(f["num"])} · {H.escape(f["date"])}</div>'
    body = "".join(section(items, i) for i, items in enumerate(sections) if items)
    css = CSS.replace("{lang}", lang).replace("{num}", f["num"].replace('"', "")).replace("{edition}", EDITION)
    return (f'<!doctype html><html lang="ka"><head><meta charset="utf-8"><title>{H.escape(SHORT["ka"])} · '
            f'{H.escape(f["name2"])}</title><style>{css}</style></head><body>{head}{body}</body></html>')


def doc_hash(page: str) -> str:
    return hashlib.sha256(page.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- the company's signature (David, once)

def company_signature() -> dict | None:
    raw = storage.read(COMPANY_SIG_KEY)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def set_company_signature(signature: str, user: User) -> dict:
    if user.role not in SUPER:
        raise forbidden("only a superadmin sets the company signature")
    _png(signature)
    d = {"signature": signature, "name": COMPANY["name"]["ru"], "by": user.id, "by_name": user.name,
         "at": now().isoformat()}
    storage.put(COMPANY_SIG_KEY, json.dumps(d).encode())
    return d


# ---------------------------------------------------------------- staff: create / list / cancel

def _staff(user: User) -> None:
    if user.role not in STAFF:
        raise forbidden("only staff send NDAs")


def path(token: str) -> str:
    return f"/nda/{token}"


def out(db: Session, n: AgentNda, full: bool = False) -> dict:
    by = db.get(User, n.created_by) if n.created_by else None
    d = {"id": n.id, "status": n.status, "status_label": STATUS_LABEL[n.status], "lang": n.lang, "full_name": n.full_name,
         "personal_id": n.personal_id, "phone": n.phone, "telegram": n.telegram, "role": n.role,
         "path": path(n.token) if n.status == "sent" else None, "created_at": n.created_at.isoformat() if n.created_at else None,
         "created_by": {"id": by.id, "name": by.name} if by else None,
         "signed_at": n.signed_at.isoformat() if n.signed_at else None,
         "tg_username": n.tg_username, "has_pdf": bool(n.pdf_path), "in_drive": bool(n.drive_file_id),
         "number": number(n), "details": n.details or {}}
    if full:
        d["html"] = n.html
    return d


def _clean(v, n=200) -> str:
    return re.sub(r"\s+", " ", str(v or "").replace("*", "")).strip()[:n]  # «*» would toggle bold in the text


DETAILS = ("full_name_ka", "ie", "address", "email", "level", "access", "iban_gel", "iban_usd")  # → agent_ndas.details


def _date(v) -> str:
    """«2026-11-14» (a date input) or «14.11.2026» → «14.11.2026»; anything else as typed."""
    v = _clean(v, 20)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v)
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else v


def number(n: AgentNda) -> str:
    """The agreement's number: «2026-0007» (year made + id)."""
    return f"{(n.created_at or now()).astimezone(TZ):%Y}-{n.id:04d}"


def create(db: Session, body: dict, user: User) -> AgentNda:
    _staff(user)
    lang = body.get("lang") or "KA-RU"
    if lang not in LANGS:
        raise DomainError("bad_input", "Язык: KA, KA-RU или KA-EN")
    f = {k: _clean(body.get(k)) for k in ("full_name", "personal_id", "phone", "telegram")}
    f["telegram"] = f["telegram"].lstrip("@").split("/")[-1] or ""  # «@name» or «t.me/name» → «name»
    if not f["full_name"] or not f["personal_id"]:
        raise DomainError("missing_fields", "Укажите ФИО и личный номер агента")
    role = body.get("role") if body.get("role") in ROLES else "agent"
    d = {k: _clean(body.get(k)) for k in DETAILS}
    d["level"] = d["level"] if d["level"] in LEVELS else ("25" if role == "recruit" else "50")
    d["access"] = _date(d["access"]) or now().astimezone(TZ).strftime("%d.%m.%Y")
    d["role"] = role
    comp = company_signature()
    if not comp:
        raise DomainError("no_company_signature", "Сначала Давид должен один раз расписаться за компанию (NDA → «Подпись компании»)", 409)
    n = AgentNda(lang=lang, token=secrets.token_urlsafe(24), html="", doc_sha256="", details=d, role=ROLES[role]["ru"],
                 company_signature=comp["signature"], company_signed_at=now(), created_by=user.id,
                 **{k: (v or None) for k, v in f.items()})
    db.add(n)
    db.flush()  # the id → the agreement's number
    day = now().astimezone(TZ).strftime("%d.%m.%Y")
    page = render(lang, {"num": number(n), "date": day, "name_ka": d["full_name_ka"] or f["full_name"],
                         "name2": f["full_name"], "ie": d["ie"], "pid": f["personal_id"], "address": d["address"],
                         "phone": f["phone"], "email": d["email"], "telegram": f"@{f['telegram']}" if f["telegram"] else "",
                         "role": role, "level": d["level"], "access": d["access"], "iban_gel": d["iban_gel"],
                         "iban_usd": d["iban_usd"]})
    n.html, n.doc_sha256 = page, doc_hash(page)
    log_event(db, "nda", n.id, "created", user, label=f["full_name"], lang=lang)
    db.commit()
    return n


def get(db: Session, nid: int, user: User) -> AgentNda:
    _staff(user)
    n = db.get(AgentNda, int(nid))
    if not n:
        raise DomainError("not_found", "NDA не найдено")
    return n


def query(user: User, status: str | None = None):
    _staff(user)
    q = select(AgentNda)
    if status and status != "all":
        q = q.where(AgentNda.status == status)
    return q.order_by(AgentNda.id.desc())


def cancel(db: Session, nid: int, user: User) -> AgentNda:
    n = get(db, nid, user)
    if n.status != "sent":
        raise DomainError("bad_status", f"NDA: {STATUS_LABEL[n.status]}", 409)
    n.status, n.cancelled_at = "cancelled", now()
    log_event(db, "nda", n.id, "cancelled", user)
    db.commit()
    return n


def pdf_bytes(n: AgentNda) -> bytes:
    data = storage.read(n.pdf_path) if n.pdf_path else None
    if not data:
        raise DomainError("not_found", "PDF ещё не готов")
    return data


def filename(n: AgentNda) -> str:
    day = (n.signed_at or n.created_at or now()).astimezone(TZ).date().isoformat()
    return re.sub(r"[\\/:*?\"<>|\s]+", " ", f"NDA {n.full_name} {day}").strip()[:150] + ".pdf"


# ---------------------------------------------------------------- the agent (public, by token)

def _by_token(db: Session, token: str, lock: bool = False) -> AgentNda:
    q = select(AgentNda).where(AgentNda.token == token)
    n = db.scalar(q.with_for_update() if lock else q) if token and len(token) >= 20 else None
    if not n or n.status == "cancelled":
        raise DomainError("not_found", "Ссылка недействительна. Попросите прислать новую")
    return n


def _test_mode() -> bool:
    """A test stand (settings.TEST_MODE, when it has one): Telegram is off there, the page offers a marked stand-in."""
    return bool(getattr(env, "TEST_MODE", False))


def _confirmed(n: AgentNda) -> bool:
    return bool(n.tg_id and n.tg_auth_date and now() - n.tg_auth_date < CONFIRM_TTL)


def view(db: Session, token: str) -> dict:
    n = _by_token(db, token)
    return {"lang": n.lang, "status": n.status, "full_name": n.full_name, "personal_id": n.personal_id, "phone": n.phone,
            "telegram": n.telegram, "role": n.role, "doc_sha256": n.doc_sha256,
            "confirmed": _confirmed(n) or n.status == "signed", "tg_username": n.tg_username, "tg_name": n.tg_name,
            "signed_at": n.signed_at.isoformat() if n.signed_at else None, "has_pdf": bool(n.pdf_path),
            "test_mode": _test_mode(), "html": n.html if n.status == "sent" else None}


def confirm_telegram(db: Session, token: str, data: dict) -> dict:
    """The Login Widget's answer → checked with the bot token; only the NDA's @username (when given) passes."""
    n = _by_token(db, token, lock=True)
    if n.status != "sent":
        raise DomainError("bad_status", "NDA уже подписано", 409)
    if _test_mode() and data.get("test") is True:  # the test server has no Telegram: a marked stand-in
        fields = {"id": "0", "username": n.telegram or "test", "first_name": "TEST", "auth_date": str(int(now().timestamp())),
                  "hash": "test-mode-" + secrets.token_hex(8)}
    else:
        from app.api.auth import verify_login_widget
        payload = {k: (str(v) if v is not None else None) for k, v in data.items() if k in
                   ("id", "first_name", "last_name", "username", "photo_url", "auth_date", "hash")}
        fields = verify_login_widget(payload, env.LOGIN_BOT_TOKEN or env.BOT_TOKEN, max_age=int(CONFIRM_TTL.total_seconds()))
        fields["hash"] = payload["hash"]
    got = (fields.get("username") or "").lower()
    if n.telegram and got != n.telegram.lower():
        raise DomainError("wrong_telegram", f"Это NDA для Telegram @{n.telegram}. Войдите в Telegram этим аккаунтом", 403)
    n.tg_id, n.tg_username = str(fields.get("id")), fields.get("username") or None
    n.tg_name = " ".join(x for x in (fields.get("first_name"), fields.get("last_name")) if x) or None
    n.tg_auth_date = datetime.fromtimestamp(int(fields.get("auth_date") or 0), timezone.utc)
    n.tg_hash = fields.get("hash")
    log_event(db, "nda", n.id, "telegram_confirmed", None, label=n.full_name, tg_id=n.tg_id, tg_username=n.tg_username)
    db.commit()
    return view(db, token)


def _png(sig) -> bytes:
    if not isinstance(sig, str) or not sig.startswith(PNG_PREFIX) or len(sig) > MAX_SIGNATURE:
        raise DomainError("bad_signature", "Подпись не получена, распишитесь ещё раз")
    try:
        raw = base64.b64decode(sig[len(PNG_PREFIX):], validate=True)
    except ValueError:
        raise DomainError("bad_signature", "Подпись не получена, распишитесь ещё раз")
    if not raw.startswith(b"\x89PNG\r\n\x1a\n") or raw[12:16] != b"IHDR" or min(struct.unpack(">II", raw[16:24])) < 12:
        raise DomainError("bad_signature", "Подпись не получена, распишитесь ещё раз")
    return raw


def sign(db: Session, token: str, data: dict, ip: str | None, user_agent: str | None) -> dict:
    n = _by_token(db, token, lock=True)
    if n.status != "sent":
        raise DomainError("already_signed", "NDA уже подписано", 409)
    if (data.get("doc_sha256") or "") != n.doc_sha256:
        raise DomainError("doc_changed", "Текст изменился: обновите страницу", 409)
    if not _confirmed(n):
        raise DomainError("telegram_required", "Подтвердите себя через Telegram", 403)
    if data.get("consent") is not True:
        raise DomainError("consent_required", "Нужно согласие с условиями")
    _png(data.get("signature"))
    n.signature, n.signed_at = data["signature"], now()
    n.ip, n.user_agent = (ip or "")[:100] or None, (user_agent or "")[:300] or None
    n.status = "signed"
    log_event(db, "nda", n.id, "signed", None, label=n.full_name, tg_id=n.tg_id, tg_username=n.tg_username, ip=n.ip,
              user_agent=n.user_agent, doc_sha256=n.doc_sha256)
    db.commit()
    try:
        finalize(db, n.id)
    except Exception:  # noqa: BLE001 — the signature is saved; the worker prints the PDF again
        db.rollback()
        log.exception("nda %s: signed PDF failed, retrying in the worker", n.id)
        jobs.schedule(db, "nda_pdf", n.id, now())
        db.commit()
    return view(db, token)


# ---------------------------------------------------------------- the signed PDF

def _tbilisi(dt) -> str:
    return dt.astimezone(TZ).strftime("%d.%m.%Y %H:%M:%S") + " (Tbilisi)"


def _record(n: AgentNda, by: User | None) -> str:
    second = SECOND[n.lang] or "en"
    L = {"title": ("ელექტრონული ხელმოწერის ოქმი", {"ru": "Протокол электронного подписания", "en": "Electronic signature record"}),
         "doc": ("დოკუმენტი", {"ru": "Документ", "en": "Document"}), "hash": ("ტექსტის SHA-256", {"ru": "SHA-256 текста", "en": "Text SHA-256"}),
         "rec": ("მონაწილე", {"ru": "Участник", "en": "Participant"}), "comp": ("ოპერატორი", {"ru": "Оператор", "en": "Operator"}),
         "name": ("სახელი, გვარი", {"ru": "ФИО", "en": "Full name"}), "pid": ("პირადი ნომერი", {"ru": "Личный номер", "en": "Personal number"}),
         "phone": ("ტელეფონი", {"ru": "Телефон", "en": "Phone"}), "tg": ("Telegram (დადასტურებული)", {"ru": "Telegram (подтверждён)", "en": "Telegram (confirmed)"}),
         "code": ("დადასტურების კოდი", {"ru": "Код подтверждения", "en": "Confirmation code"}),
         "conf": ("დადასტურების დრო", {"ru": "Время подтверждения", "en": "Confirmed at"}),
         "at": ("ხელმოწერის დრო", {"ru": "Время подписи", "en": "Signed at"}), "ip": ("IP მისამართი", {"ru": "IP-адрес", "en": "IP address"}),
         "ua": ("მოწყობილობა", {"ru": "Устройство", "en": "Device"}), "sig": ("ხელმოწერა", {"ru": "Подпись", "en": "Signature"}),
         "sent": ("გაგზავნა", {"ru": "Отправил", "en": "Sent by"})}
    t = lambda k: H.escape(f"{L[k][0]} / {L[k][1][second]}")  # noqa: E731
    e = lambda v: H.escape(str(v)) if v else "—"  # noqa: E731
    row = lambda k, v, cls="": f'<tr><td>{t(k)}</td><td{cls}>{v}</td></tr>'  # noqa: E731
    tg = f'@{e(n.tg_username)} · id {e(n.tg_id)}' + (f' · {e(n.tg_name)}' if n.tg_name else "")
    return (f'<section class="rec"><h2>{t("title")}</h2><table>{row("doc", f"{e(SHORT[second])} № {number(n)} · {e(n.lang)} · {EDITION}")}'
            f'{row("hash", n.doc_sha256, " class=mono")}</table>'
            f'<table><tr><td colspan="2"><b>{t("rec")}</b></td></tr>{row("name", e(n.full_name))}{row("pid", e(n.personal_id))}'
            f'{row("phone", e(n.phone))}{row("tg", tg)}{row("code", e(n.tg_hash), " class=mono")}'
            f'{row("conf", _tbilisi(n.tg_auth_date) if n.tg_auth_date else "—")}{row("at", _tbilisi(n.signed_at))}'
            f'{row("ip", e(n.ip))}{row("ua", e(n.user_agent), " class=mono")}'
            f'{row("sig", f"<img src={chr(34)}{n.signature}{chr(34)} alt={chr(34)}{chr(34)}>")}</table>'
            f'<table><tr><td colspan="2"><b>{t("comp")}</b></td></tr>{row("name", e(COMPANY["name"][second]) + " · IE " + COMPANY["ie"])}'
            f'{row("at", _tbilisi(n.company_signed_at) if n.company_signed_at else "—")}{row("sent", e(by and by.name))}'
            f'{row("sig", f"<img src={chr(34)}{n.company_signature}{chr(34)} alt={chr(34)}{chr(34)}>")}</table></section>')


def signed_html(n: AgentNda, by: User | None = None) -> str:
    """The frozen text with both signatures and times in the «Signatures» tables, plus the signing record page."""
    img = lambda s: f'<img src="{s}" alt="">' if s else "—"  # noqa: E731
    fill = {"company-sig": img(n.company_signature), "recipient-sig": img(n.signature),
            "company-time": _tbilisi(n.company_signed_at) if n.company_signed_at else "—",
            "recipient-time": _tbilisi(n.signed_at) if n.signed_at else "—"}
    page = re.sub(r'<span class="slot" data-slot="([\w-]+)">[^<]*</span>', lambda m: fill.get(m.group(1), m.group(0)), n.html)
    return page.replace("</body>", _record(n, by) + "</body>", 1)


def finalize(db: Session, nid: int) -> str:
    """Signed: print the PDF, keep it, send it (staff + agent), queue Google Drive. Idempotent."""
    n = db.get(AgentNda, nid, with_for_update=True, populate_existing=True)
    if not n or n.status != "signed" or n.pdf_path:
        return "stale"
    by = db.get(User, n.created_by) if n.created_by else None
    pdf = C.render_pdf(signed_html(n, by))
    n.pdf_path = f"private/nda/{n.id}/{secrets.token_hex(8)}.pdf"
    storage.put(n.pdf_path, pdf)
    jobs.schedule(db, "nda_drive", n.id, now())
    log_event(db, "nda", n.id, "pdf", None, pdf_sha256=hashlib.sha256(pdf).hexdigest())
    db.commit()
    _send(env.BOT_TOKEN, by and by.tg_id, f"✅ NDA подписано: {n.full_name} (@{n.tg_username or '—'})", filename(n), pdf)
    _send(env.LOGIN_BOT_TOKEN or env.BOT_TOKEN, n.tg_id if n.tg_id and n.tg_id != "0" else None,
          "Homes in Georgia: your signed NDA / ხელმოწერილი NDA", filename(n), pdf)
    return "ok"


def _send(token: str, chat_id, caption: str, name: str, pdf: bytes) -> None:
    """A PDF to a Telegram chat through the given bot; never fails the signing (the agent may not have allowed the
    login bot to write to him)."""
    if not token or not chat_id:
        return
    try:
        base = (env.TG_API_BASE or "https://api.telegram.org").rstrip("/")
        httpx.post(f"{base}/bot{token}/sendDocument", data={"chat_id": str(chat_id), "caption": caption},
                   files={"document": (name, pdf, "application/pdf")}, timeout=30)
    except Exception:  # noqa: BLE001
        log.warning("nda: telegram send to %s failed", chat_id)


def drive_upload(db: Session, nid: int) -> str:
    """Worker: the signed NDA → the contracts Drive folder (same settings as contracts)."""
    n = db.get(AgentNda, nid)
    if not n or n.status != "signed" or not n.pdf_path or n.drive_file_id:
        return "stale"
    sa = C.drive_configured()
    if not sa:
        return "skipped"
    n.drive_file_id = C.drive_put(sa, filename(n), pdf_bytes(n))
    log_event(db, "nda", n.id, "drive_uploaded", None, file_id=n.drive_file_id)
    return "ok"


def signed_pdf(db: Session, token: str) -> tuple[AgentNda, bytes]:
    n = _by_token(db, token)
    if n.status != "signed":
        raise DomainError("not_found", "NDA ещё не подписано")
    return n, pdf_bytes(n)
