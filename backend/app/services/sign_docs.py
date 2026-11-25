"""«Документ на подпись»: any document signed electronically (David 11-17: a contract with a team member, later sale and
commercial agreements). One fixed template, staff paste the text (Stanislav 11-17: no template designer, no DOCX).

Staff (operator and up) make a draft: the title and the body as plain text with paragraphs (Georgian, Russian or both
in two columns — the NDA's language switch), the other party (a team member from the users, or an outside person: full
name, ID / passport number, phone) and optionally our second signer (a staff member). Our side is the company with
David's signature, drawn once for the NDA (nda.company_signature). The page is the NDA's layout (nda.CSS: header, № and
date, the parties, the pasted text, the signatures) printed by the same Chrome (contracts.render_pdf).

черновик (draft) → «Отправить»: the company signature goes in, the text is frozen (doc_sha256) and each signer gets a
link /doc/<token> (the CRM bot sends it to a team member) → отправлен (sent): the text can still be edited until the
first signature (new hash: an open page asks to reload) → every signer signed on the NDA's signing page (Telegram
confirmation for team members and a named @username, services/signing.py; the signature pad; consent) → подписан
(signed): the signed PDF (signatures + the signing record page) goes to Telegram (the author, each confirmed signer) and
to Google Drive; a signed document never changes. «Отменить» (draft / sent) → отменён (cancelled), the links die.

Or a ready PDF (Irakli 11-18: a lease made elsewhere, «nothing in the contract changes»): instead of the text staff upload
the file (body = {"pdf": {path, name, pages, size, sha256}}, no text, no template), doc_sha256 is the file's hash, the
signer reads the file itself, and the signed PDF is the uploaded file byte-for-byte with the signing record page (who,
when, signatures, Telegram, IP) added as a PDF incremental update after it (pypdf): its pages and bytes stay as they were.

«Договор между сторонами» (Irakli 11-18: owner ↔ client, body.parties): a ready PDF with no company signature and no staff
signer — only outside signers (a role label, a name, Telegram @username or a WhatsApp phone, an optional ID), each with
his own link that staff send themselves; when all signed, the same signed PDF goes to the author in Telegram."""
import base64
import hashlib
import html as H
import io
import logging
import re
import secrets
from itertools import zip_longest

from pypdf import PdfReader, PdfWriter
from sqlalchemy import String, cast, exists, or_, select
from sqlalchemy.orm import Session

from app import storage
from app.config import settings as env
from app.models import SignDoc, SignDocSigner, User, at_least
from app.services import contracts as C, jobs, nda as N, signing as S
from app.services.common import TZ, log_event, now
from app.services.errors import DomainError, forbidden

log = logging.getLogger(__name__)
LANGS = ("KA", "RU", "KA-RU")
STATUS_LABEL = {"draft": "Черновик", "sent": "Отправлен", "signed": "Подписан", "cancelled": "Отменён"}
MAX_TITLE, MAX_BODY = 300, 100_000
MAX_PDF = 15 * 1024 * 1024
MAX_SIGNERS = 10

SIDE = {"side1": {"ka": "მხარე 1", "ru": "Сторона 1"}, "side1b": {"ka": "მხარე 1 — ხელმომწერი", "ru": "Сторона 1 — подписант"},
        "side2": {"ka": "მხარე 2", "ru": "Сторона 2"}}  # the parties' table headings (the NDA's: Оператор | Участник)


def _langs(lang: str) -> list[str]:
    return N.COLS[lang]


def number(d: SignDoc) -> str:
    return f"D-{(d.created_at or now()).astimezone(TZ):%Y}-{d.id:04d}"


def _day(d: SignDoc) -> str:
    return (d.sent_at or now()).astimezone(TZ).strftime("%d.%m.%Y")


def paragraphs(text: str) -> list[str]:
    """Plain text → paragraphs (a blank line between them)."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


NUM_RE = re.compile(r"(\d{1,2}\.\d{1,2})\.?\s+(.*)|(\d{1,2})\.\s*(.*)")  # «1.1.» / «1.1» first, then «1.»


def _short(x: str) -> bool:
    """A heading's text: short, not a sentence."""
    return len(x) <= 80 and not re.search(r"[.,;:]$", x)


def blocks(text: str) -> list[dict]:
    """The pasted text → the NDA's blocks (nda_text: h = clause heading «N. Title», n = numbered clause, sub = heading
    without a number, p = text): «N.» / «N.N.» + a short title → a heading, «N.N.» + a sentence → a numbered clause, a
    short line alone before a blank line → a heading, every other line → a paragraph. «**…**» stays bold."""
    lines = [x.strip() for x in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out = []
    for i, x in enumerate(lines):
        if not x:
            continue
        m = NUM_RE.fullmatch(x)
        alone = (i + 1 == len(lines) or not lines[i + 1]) and (i == 0 or not lines[i - 1])
        if m:
            n, rest = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            out.append({"k": "h" if _short(rest.replace("**", "")) else "n", "n": n, "t": rest})
        elif alone and _short(x.replace("**", "")) and out:  # not the first line: a document starts with its text
            out.append({"k": "sub", "n": "", "t": x})
        else:
            out.append({"k": "p", "n": "", "t": x})
    return out


def _rows(d: SignDoc) -> list[dict]:
    """The languages' blocks side by side (KA-RU: Georgian | Russian, paired in order, like the NDA's two columns)."""
    langs = _langs(d.lang)
    cols = [blocks((d.body or {}).get(ln) or "") for ln in langs]
    rows = []
    for pair in zip_longest(*cols):
        first = next(b for b in pair if b)
        rows.append({"k": first["k"], "n": first["n"], "ka": "", "ru": "", "en": "",
                     **{ln: (b["t"] if b else "") for ln, b in zip(langs, pair)}})
    return rows


def _company(signers) -> SignDocSigner | None:
    return next((s for s in signers if s.side == "company"), None)


def _party(signers) -> SignDocSigner | None:
    return next((s for s in signers if s.side == "party"), None)


def _person_col(s: SignDocSigner) -> dict:
    return {"name": s.full_name, "pid": s.personal_id, "contacts": " · ".join(x for x in (s.phone, s.telegram and f"@{s.telegram}") if x)}


def render(d: SignDoc) -> str:
    """The document's HTML: the NDA's own page (nda.render, its CSS, header, two columns, clause styles, the parties'
    table) with the pasted text as the clauses and this document's title and parties."""
    party, co = _party(d.signers), _company(d.signers)

    def parties(ln: str) -> str:
        cols = [(SIDE["side1"][ln], N.company_cols(ln), "company")]
        if co:
            cols.append((SIDE["side1b"][ln], _person_col(co), f"s{co.id}"))
        if party:
            cols.append((SIDE["side2"][ln], _person_col(party), f"s{party.id}"))
        return N.parties_table(ln, cols)

    return N.render(d.lang, {"num": number(d), "date": _day(d), "name2": party.full_name if party else ""}, _rows(d),
                    edition="", title={ln: (d.title or {}).get(ln) or "" for ln in ("ka", "ru", "en")}, parties=parties)


def _src(d: SignDoc) -> dict | None:
    """An uploaded PDF document: {path, name, pages, size, sha256}; None = the pasted text in our template."""
    return (d.body or {}).get("pdf")


def _parties(d: SignDoc) -> bool:
    """«Договор между сторонами»: outside signers only, no company signature."""
    return bool((d.body or {}).get("parties"))


def _rerender(d: SignDoc) -> None:
    if src := _src(d):
        d.html, d.doc_sha256 = "", src["sha256"]
        return
    page = render(d)
    if page != d.html:
        d.html, d.doc_sha256 = page, N.doc_hash(page)


# ---------------------------------------------------------------- staff

def _staff(user: User) -> None:
    """Operators and up: every document; an agent (Stanislav 11-18, from «Договоры»): his own «между сторонами» only."""
    if not at_least(user, "agent"):
        raise forbidden("only staff send documents for signing")


def _mine(d: SignDoc, user: User) -> bool:
    return at_least(user, "operator") or (_parties(d) and d.created_by == user.id)


def path(token: str) -> str:
    return f"/doc/{token}"


def _username(v: str | None) -> bool:
    """A Telegram @username (5–32, starts with a letter) — not a phone typed into the field."""
    return bool(v and re.fullmatch(r"[A-Za-z]\w{4,31}", v))


def _need_tg(db: Session, s: SignDocSigner) -> bool:
    """Telegram confirmation: team members (their own account) and a named @username."""
    return bool(_username(s.telegram) or (s.user_id and (u := db.get(User, s.user_id)) and u.tg_id))


def _signer_out(db: Session, d: SignDoc, s: SignDocSigner) -> dict:
    return {"id": s.id, "side": s.side, "role": s.role, "user_id": s.user_id, "full_name": s.full_name, "personal_id": s.personal_id,
            "phone": s.phone, "telegram": s.telegram, "need_tg": _need_tg(db, s), "tg_username": s.tg_username,
            "signed_at": s.signed_at.isoformat() if s.signed_at else None,
            "path": path(s.token) if d.status == "sent" and not s.signed_at else None}


def out(db: Session, d: SignDoc, full: bool = False) -> dict:
    by = db.get(User, d.created_by) if d.created_by else None
    r = {"id": d.id, "number": number(d), "status": d.status, "status_label": STATUS_LABEL[d.status], "lang": d.lang,
         "title": d.title or {}, "created_at": d.created_at.isoformat() if d.created_at else None,
         "created_by": {"id": by.id, "name": by.name} if by else None,
         "sent_at": d.sent_at.isoformat() if d.sent_at else None, "signed_at": d.signed_at.isoformat() if d.signed_at else None,
         "has_pdf": bool(d.pdf_path), "signers": [_signer_out(db, d, s) for s in d.signers],
         "editable": editable(d), "pdf": _pdf_out(d), "parties": _parties(d)}
    if full:
        r["body"], r["html"] = d.body or {}, d.html
    return r


def _pdf_out(d: SignDoc) -> dict | None:
    src = _src(d)
    return src and {k: src[k] for k in ("name", "pages", "size")}


def editable(d: SignDoc) -> bool:
    return d.status == "draft" or d.status == "sent" and not any(s.signed_at for s in d.signers)


def _clean(v, n=200) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _texts(body: dict, lang: str, pdf: bool = False) -> tuple[dict, dict]:
    title, text = body.get("title") or {}, body.get("body") or {}
    title = {ln: _clean(title.get(ln), MAX_TITLE) for ln in _langs(lang)}
    text = {ln: str(text.get(ln) or "").strip()[:MAX_BODY] for ln in _langs(lang)}
    for ln in _langs(lang):
        word = "по-грузински" if ln == "ka" else "по-русски"
        if not title[ln]:
            raise DomainError("missing_fields", f"Укажите название документа {word}")
        if not pdf and not paragraphs(text[ln]):
            raise DomainError("missing_fields", f"Вставьте текст документа {word}")
    return title, text


def _person(db: Session, p: dict, side: str) -> dict:
    """A signer from the form: a team member (user_id: blanks from the profile) or an outside person."""
    p = p or {}
    u = db.get(User, int(p["user_id"])) if p.get("user_id") else None
    if p.get("user_id") and (not u or u.deleted_at):
        raise DomainError("not_found", "Пользователь не найден")
    if side == "company" and u and not at_least(u, "operator"):
        raise DomainError("bad_input", "Второй подписант с нашей стороны — сотрудник (оператор и выше)")
    f = {k: _clean(p.get(k)) for k in ("full_name", "personal_id", "phone", "telegram")}
    f["telegram"] = f["telegram"].lstrip("@").split("/")[-1]
    if u:
        f["full_name"] = f["full_name"] or " ".join(x for x in (u.first_name, u.last_name) if x) or u.name
        f["personal_id"] = f["personal_id"] or u.personal_id or ""
        f["phone"] = f["phone"] or u.phone or ""
        f["telegram"] = ""  # a team member confirms with his own account (users.tg_id)
    who = "второго подписанта" if side == "company" else "другой стороны"
    if not f["full_name"]:
        raise DomainError("missing_fields", f"Укажите ФИО {who}")
    if side == "party" and not f["personal_id"]:
        raise DomainError("missing_fields", "Укажите личный номер или номер паспорта другой стороны")
    if side == "party" and not u and not f["phone"]:
        raise DomainError("missing_fields", "Укажите телефон другой стороны")
    return {"user_id": u.id if u else None, **{k: (v or None) for k, v in f.items()}}


def _set_signer(d: SignDoc, side: str, f: dict | None) -> None:
    """The side's signer row: updated in place (the link stays) or added / removed; a changed person must confirm again."""
    s = next((x for x in d.signers if x.side == side), None)
    if not f:
        if s:
            d.signers.remove(s)
        return
    if not s:
        d.signers.append(SignDocSigner(side=side, token=secrets.token_urlsafe(24), **f))
        return
    if (s.user_id, s.telegram) != (f["user_id"], f["telegram"]):
        s.tg_id = s.tg_username = s.tg_name = s.tg_auth_date = s.tg_hash = None
    for k, v in f.items():
        setattr(s, k, v)


def _outside(p: dict) -> dict:
    """A «Договор между сторонами» signer: a name and Telegram or a WhatsApp phone (one of them), the ID optional."""
    f = {k: _clean(p.get(k)) for k in ("role", "full_name", "personal_id", "phone", "telegram")}
    f["role"], f["telegram"] = f["role"][:60], f["telegram"].lstrip("@").split("/")[-1]
    who = f["role"] or f["full_name"] or "подписант"
    if not f["full_name"]:
        raise DomainError("missing_fields", f"Укажите имя: {who}")
    # contacts optional (Irakli 11-25: the links go out by hand); a phone typed into «Telegram» is a phone
    if f["telegram"] and not _username(f["telegram"]):
        f["phone"], f["telegram"] = f["phone"] or f["telegram"], ""
    return {"user_id": None, **{k: (v or None) for k, v in f.items()}}


def _set_parties(d: SignDoc, rows: list) -> None:
    """The signers' rows: a row with its id is updated in place (the link stays), new rows get links, the rest go."""
    if not 1 <= len(rows) <= MAX_SIGNERS:
        raise DomainError("missing_fields", f"Добавьте подписантов (от 1 до {MAX_SIGNERS})")
    old, keep = {s.id: s for s in d.signers if s.side == "party"}, []
    for p in rows:
        f, s = _outside(p or {}), old.pop((p or {}).get("id"), None)
        if not s:
            s = SignDocSigner(side="party", token=secrets.token_urlsafe(24), **f)
        elif s.telegram != f["telegram"]:
            s.tg_id = s.tg_username = s.tg_name = s.tg_auth_date = s.tg_hash = None
        for k, v in f.items():
            setattr(s, k, v)
        keep.append(s)
    d.signers[:] = keep  # removed rows and our side go: their links die


def _read_pdf(data_url, name) -> tuple[bytes, dict]:
    """The uploaded file (a data: URL from the form): a real PDF, ≤ 15 MB, not password-protected, at least one page."""
    bad = DomainError("bad_input", "Загрузите файл PDF")
    if not isinstance(data_url, str) or not data_url.startswith("data:") or ";base64," not in data_url[:100] \
            or len(data_url) > MAX_PDF * 4 // 3 + 200:
        raise DomainError("bad_input", "Загрузите файл PDF до 15 МБ")
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except ValueError:
        raise bad
    if len(raw) > MAX_PDF:
        raise DomainError("bad_input", "PDF больше 15 МБ")
    if not raw.startswith(b"%PDF-"):
        raise bad
    try:
        r = PdfReader(io.BytesIO(raw))
        locked, pages = r.is_encrypted, (0 if r.is_encrypted else len(r.pages))
    except Exception:  # noqa: BLE001 — pypdf raises many kinds on a broken file
        raise DomainError("bad_input", "Файл PDF повреждён или не читается")
    if locked:
        raise DomainError("bad_input", "PDF защищён паролем: загрузите файл без пароля")
    if not pages:
        raise DomainError("bad_input", "В PDF нет страниц")
    name = _clean(name, 150) or "document.pdf"
    return raw, {"name": name, "pages": pages, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _fill(db: Session, d: SignDoc, body: dict) -> None:
    lang = body.get("lang") or "KA-RU"
    if lang not in LANGS:
        raise DomainError("bad_input", "Язык: KA, RU или KA-RU")
    new = _read_pdf(body["pdf"], body.get("pdf_name")) if body.get("pdf") else None
    src = new[1] if new else _src(d)  # an uploaded document stays a PDF; a new file replaces the old one
    parties = body.get("mode") == "parties"
    if d.id and parties != _parties(d):
        raise DomainError("bad_input", "Тип документа не меняется: создайте новый")
    if parties and not src:
        raise DomainError("missing_fields", "Загрузите файл PDF договора")
    d.lang = lang
    d.title, d.body = _texts(body, lang, pdf=bool(src))
    if parties:
        _set_parties(d, body.get("signers") or [])
    else:
        _fill_ours(db, d, body)
    if new:  # stored last: a form error above leaves no file behind
        src["path"] = f"private/sign-docs/src/{secrets.token_hex(12)}.pdf"
        storage.put(src["path"], new[0])
    if src:
        d.body = {"pdf": src, **({"parties": True} if parties else {})}


def _fill_ours(db: Session, d: SignDoc, body: dict) -> None:
    """The company + optional staff signer + Сторона 2."""
    party = _person(db, body.get("party"), "party")
    co = _person(db, body["cosigner"], "company") if (body.get("cosigner") or {}).get("user_id") or \
        _clean((body.get("cosigner") or {}).get("full_name")) else None
    if co and party["user_id"] and co["user_id"] == party["user_id"]:
        raise DomainError("bad_input", "Второй подписант и другая сторона — разные люди")
    _set_signer(d, "party", party)
    _set_signer(d, "company", co)


def create(db: Session, body: dict, user: User) -> SignDoc:
    _staff(user)
    if not at_least(user, "operator") and body.get("mode") != "parties":
        raise forbidden("an agent sends only «Договор между сторонами»")
    d = SignDoc(status="draft", created_by=user.id, lang="KA-RU")
    _fill(db, d, body)
    db.add(d)
    db.flush()  # the id → the number
    _rerender(d)
    log_event(db, "sign_doc", d.id, "created", user, label=(d.title or {}).get("ru") or (d.title or {}).get("ka"))
    db.commit()
    return d


def get(db: Session, did: int, user: User, lock: bool = False) -> SignDoc:
    _staff(user)
    d = db.get(SignDoc, int(did), with_for_update=lock, populate_existing=lock) if str(did).isdigit() else None
    if not d or not _mine(d, user):
        raise DomainError("not_found", "Документ не найден")
    return d


def update(db: Session, did: int, body: dict, user: User) -> SignDoc:
    """The text / parties change until the first signature; once sent it gets a new hash (an open page asks to reload)."""
    d = get(db, did, user, lock=True)
    if not editable(d):
        raise DomainError("bad_status", f"Документ: {STATUS_LABEL[d.status]} — текст не меняется" if d.status != "sent"
                          else "Документ уже подписан одной из сторон — текст не меняется", 409)
    _fill(db, d, body)
    db.flush()
    old = d.doc_sha256
    _rerender(d)
    log_event(db, "sign_doc", d.id, "edited", user, label=_label(d), doc_sha256=d.doc_sha256 if d.doc_sha256 != old else None)
    db.commit()
    return d


def _label(d: SignDoc) -> str:
    return (d.title or {}).get("ru") or (d.title or {}).get("ka") or number(d)


def query(user: User, status: str | None = None, q: str | None = None):
    _staff(user)
    stmt = select(SignDoc)
    if not at_least(user, "operator"):
        stmt = stmt.where(SignDoc.created_by == user.id, SignDoc.body["parties"].as_boolean().is_(True))
    if status and status != "all":
        stmt = stmt.where(SignDoc.status == status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(cast(SignDoc.title, String).ilike(like),
                              exists().where(SignDocSigner.doc_id == SignDoc.id, SignDocSigner.full_name.ilike(like))))
    return stmt.order_by(SignDoc.id.desc())


def cancel(db: Session, did: int, user: User) -> SignDoc:
    d = get(db, did, user, lock=True)
    if d.status not in ("draft", "sent"):
        raise DomainError("bad_status", f"Документ: {STATUS_LABEL[d.status]}", 409)
    d.status, d.cancelled_at = "cancelled", now()
    log_event(db, "sign_doc", d.id, "cancelled", user, label=_label(d))
    db.commit()
    return d


def send(db: Session, did: int, user: User) -> dict:
    """draft → sent (the company signature in, the text frozen); then (and for «Отправить снова») each unsigned team member
    gets his link from the CRM bot. {links: [{id, sent}]}: sent false → staff copy the link."""
    from app.telegram import client as tg
    d = get(db, did, user, lock=True)
    if d.status == "draft":
        if not _parties(d):  # «между сторонами»: no company signature
            comp = N.company_signature()
            if not comp:
                raise DomainError("no_company_signature", "Сначала Давид должен один раз расписаться за компанию (NDA → «Подпись компании»)", 409)
            d.company_signature, d.company_signed_at = comp["signature"], now()
        d.status, d.sent_at = "sent", now()
        _rerender(d)  # the date of sending
        log_event(db, "sign_doc", d.id, "sent", user, label=_label(d), doc_sha256=d.doc_sha256)
    elif d.status != "sent":
        raise DomainError("bad_status", f"Документ: {STATUS_LABEL[d.status]}", 409)
    db.commit()
    links = []
    for s in d.signers:
        if s.signed_at:
            continue
        u = db.get(User, s.user_id) if s.user_id else None
        ok = False
        if u and u.tg_id:
            url = env.PUBLIC_URL.rstrip("/") + path(s.token)
            text = (f"📝 <b>Homes in Georgia</b>\nდოკუმენტი ხელმოწერისთვის / Документ на подпись:\n"
                    f"<b>{H.escape(_label(d))}</b> № {number(d)}")
            ok = tg.send(u.tg_id, text, {"inline_keyboard": [[{"text": "✍️ ხელმოწერა / Подписать", "url": url}]]}) is not None
        links.append({"id": s.id, "sent": ok})
        log_event(db, "sign_doc", d.id, "link_sent" if ok else "link_not_sent", user, label=_label(d), signer=s.id)
    db.commit()
    return {**out(db, d), "links": links}


def pdf(db: Session, did: int, user: User) -> tuple[SignDoc, bytes]:
    """Signed → the signed PDF; before that → the current text printed (unsigned slots) to check it."""
    d = get(db, did, user)
    if d.status == "signed":
        return d, _stored(d)
    return d, _original(d) if _src(d) else C.render_pdf(d.html)


def _stored(d: SignDoc) -> bytes:
    data = storage.read(d.pdf_path) if d.pdf_path else None
    if not data:
        raise DomainError("not_found", "PDF ещё не готов")
    return data


def _original(d: SignDoc) -> bytes:
    data = storage.read(_src(d)["path"])
    if not data:
        raise DomainError("not_found", "Файл PDF не найден")
    return data


def filename(d: SignDoc) -> str:
    return re.sub(r"[\\/:*?\"<>|\s]+", " ", f"{_label(d)} {number(d)}").strip()[:150] + ".pdf"


# ---------------------------------------------------------------- the signer (public, by token): the NDA's signing page

def _by_token(db: Session, token: str, lock: bool = False) -> tuple[SignDoc, SignDocSigner]:
    s = db.scalar(select(SignDocSigner).where(SignDocSigner.token == token)) if token and len(token) >= 20 else None
    d = db.get(SignDoc, s.doc_id, with_for_update=lock, populate_existing=lock) if s else None
    if not d or d.status in ("draft", "cancelled"):
        raise DomainError("not_found", "Ссылка недействительна. Попросите прислать новую")
    if lock:
        db.refresh(s)
    return d, s


def view(db: Session, token: str) -> dict:
    d, s = _by_token(db, token)
    return {"kind": "doc", "lang": d.lang, "status": d.status, "title": d.title or {}, "number": number(d),
            "full_name": s.full_name, "telegram": s.telegram, "need_tg": _need_tg(db, s), "doc_sha256": d.doc_sha256,
            "confirmed": S.confirmed(s) or bool(s.signed_at), "tg_username": s.tg_username, "tg_name": s.tg_name,
            "signed": bool(s.signed_at), "signed_at": (s.signed_at or d.signed_at) and (s.signed_at or d.signed_at).isoformat(),
            "has_pdf": bool(d.pdf_path), "test_mode": S.test_mode(),
            "signers": [{"full_name": x.full_name, "role": x.role, "signed": bool(x.signed_at)} for x in d.signers],
            "html": d.html if d.status == "sent" else None, "pdf": _pdf_out(d)}


def _expected(db: Session, s: SignDocSigner):
    u = db.get(User, s.user_id) if s.user_id else None
    return u.tg_id if u and u.tg_id else None


def confirm_telegram(db: Session, token: str, data: dict) -> dict:
    d, s = _by_token(db, token, lock=True)
    if d.status != "sent" or s.signed_at:
        raise DomainError("bad_status", "Документ уже подписан", 409)
    S.apply(s, S.widget(data, s.telegram), telegram=s.telegram, tg_id=_expected(db, s),
            wrong="Это документ для другого Telegram-аккаунта. Войдите своим аккаунтом")
    log_event(db, "sign_doc", d.id, "telegram_confirmed", None, label=s.full_name, tg_id=s.tg_id, tg_username=s.tg_username)
    db.commit()
    return view(db, token)


def confirm_by_bot(db: Session, token: str, frm: dict) -> None:
    """/start sd_<token> in the CRM sign-in bot (telegram/login_bot.py)."""
    d, s = _by_token(db, token, lock=True)
    if d.status != "sent" or s.signed_at:
        raise DomainError("bad_status", "Документ уже подписан", 409)
    S.apply(s, S.from_bot(s.token, frm), telegram=s.telegram, tg_id=_expected(db, s),
            wrong="Это документ для другого Telegram-аккаунта. Откройте ссылку из своего аккаунта")
    log_event(db, "sign_doc", d.id, "telegram_confirmed", None, label=s.full_name, tg_id=s.tg_id, via="bot")
    db.commit()


def sign(db: Session, token: str, data: dict, ip: str | None, user_agent: str | None) -> dict:
    d, s = _by_token(db, token, lock=True)
    if d.status != "sent" or s.signed_at:
        raise DomainError("already_signed", "Документ уже подписан", 409)
    if (data.get("doc_sha256") or "") != d.doc_sha256:
        raise DomainError("doc_changed", "Текст изменился: обновите страницу", 409)
    if _need_tg(db, s) and not S.confirmed(s):
        raise DomainError("telegram_required", "Подтвердите себя через Telegram", 403)
    if data.get("consent") is not True:
        raise DomainError("consent_required", "Нужно согласие с условиями")
    N._png(data.get("signature"))
    s.signature, s.signed_at = data["signature"], now()
    s.ip, s.user_agent = (ip or "")[:100] or None, (user_agent or "")[:300] or None
    log_event(db, "sign_doc", d.id, "signer_signed", None, label=s.full_name, signer=s.id, tg_id=s.tg_id, ip=s.ip,
              doc_sha256=d.doc_sha256)
    if all(x.signed_at for x in d.signers):
        d.status, d.signed_at = "signed", now()
        log_event(db, "sign_doc", d.id, "signed", None, label=_label(d), doc_sha256=d.doc_sha256)
    db.commit()
    if d.status == "signed":
        try:
            finalize(db, d.id)
        except Exception:  # noqa: BLE001 — the signatures are saved; the worker prints the PDF again
            db.rollback()
            log.exception("sign_doc %s: signed PDF failed, retrying in the worker", d.id)
            jobs.schedule(db, "sign_doc_pdf", d.id, now())
            db.commit()
    return view(db, token)


# ---------------------------------------------------------------- the signed PDF

def _record(d: SignDoc, by: User | None) -> str:
    """The NDA's e-signing protocol page (nda.record_page) with this document's signers."""
    second = "ru" if "ru" in _langs(d.lang) else "en"
    src = _src(d)
    file = f" · PDF «{H.escape(src['name'])}», {src['pages']} стр." if src else ""
    return N.record_page(second, f"{H.escape(_label(d))} № {number(d)} · {d.lang}{file}", d.doc_sha256,
                         [(s.role or ("side2" if s.side == "party" else "side1"), s) for s in d.signers],
                         None if _parties(d) else ("side1", d.company_signed_at, by, d.company_signature))


def signed_html(d: SignDoc, by: User | None = None) -> str:
    """The frozen text with every signature and time in the parties' table, plus the NDA's signing record page."""
    img = lambda sig: f'<img src="{sig}" alt="">' if sig else "—"  # noqa: E731
    fill = {"company-sig": img(d.company_signature), "company-time": N._tbilisi(d.company_signed_at) if d.company_signed_at else "—"}
    for s in d.signers:
        fill[f"s{s.id}-sig"], fill[f"s{s.id}-time"] = img(s.signature), N._tbilisi(s.signed_at) if s.signed_at else "—"
    return N.fill_slots(d.html, fill).replace("</body>", _record(d, by) + "</body>", 1)


def record_html(d: SignDoc, by: User | None = None) -> str:
    """An uploaded PDF's added page: only the signing record (the NDA's CSS, no page number: it follows the file's pages)."""
    css = N.CSS.replace(' · {edition} · " counter(page)', '"').replace("{lang}", d.lang).replace("{num}", number(d))
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{css}.rec{{break-before:auto}}</style></head>'
            f'<body>{_record(d, by)}</body></html>')


def append_pdf(original: bytes, extra: bytes) -> bytes:
    """original + extra's pages as an incremental update: the result starts with the original bytes unchanged, its pages
    keep their content, the new pages come after the last one."""
    w = PdfWriter(io.BytesIO(original), incremental=True)
    for p in PdfReader(io.BytesIO(extra)).pages:
        w.add_page(p)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def finalize(db: Session, did: int) -> str:
    """Signed: print the PDF, keep it, send it (the author + each confirmed signer), queue Google Drive. Idempotent."""
    d = db.get(SignDoc, did, with_for_update=True, populate_existing=True)
    if not d or d.status != "signed" or d.pdf_path:
        return "stale"
    by = db.get(User, d.created_by) if d.created_by else None
    data = append_pdf(_original(d), C.render_pdf(record_html(d, by))) if _src(d) else C.render_pdf(signed_html(d, by))
    d.pdf_path = f"private/sign-docs/{d.id}/{secrets.token_hex(8)}.pdf"
    storage.put(d.pdf_path, data)
    jobs.schedule(db, "sign_doc_drive", d.id, now())
    log_event(db, "sign_doc", d.id, "pdf", None, pdf_sha256=hashlib.sha256(data).hexdigest())
    db.commit()
    name = filename(d)
    N._bg(N._send, env.BOT_TOKEN, by and by.tg_id, f"✅ Подписан: {_label(d)} № {number(d)}", name, data)
    for s in d.signers:
        if s.tg_id and s.tg_id != "0":
            N._bg(N._send, env.LOGIN_BOT_TOKEN or env.BOT_TOKEN, s.tg_id,
                  f"Homes in Georgia: ხელმოწერილი დოკუმენტი / подписанный документ № {number(d)}", name, data)
    return "ok"


def drive_upload(db: Session, did: int) -> str:
    """Worker: the signed document → the contracts Drive folder (the contracts' settings)."""
    d = db.get(SignDoc, did)
    if not d or d.status != "signed" or not d.pdf_path or d.drive_file_id:
        return "stale"
    sa = C.drive_configured()
    if not sa:
        return "skipped"
    d.drive_file_id = C.drive_put(sa, filename(d), _stored(d))
    log_event(db, "sign_doc", d.id, "drive_uploaded", None, file_id=d.drive_file_id)
    return "ok"


def signed_pdf(db: Session, token: str) -> tuple[SignDoc, bytes]:
    d, _ = _by_token(db, token)
    if d.status == "sent" and _src(d):  # the uploaded file itself: what the signer reads before signing
        return d, _original(d)
    if d.status != "signed":
        raise DomainError("not_found", "Документ ещё не подписан всеми сторонами")
    return d, _stored(d)
