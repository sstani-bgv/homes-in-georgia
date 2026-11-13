"""Electronic signing of a generated contract (docs/CONTRACTS.md «Электронная подпись»; the HiG Sign idea, PlatformDGE/hig-sign).

The agent presses «Подписать электронно» on a draft: each party (owner, tenant) gets its own unguessable link
/esign/<token>. The party opens it (no CRM login), reads the contract, types the full name / personal ID / phone, draws
the signature with a finger and confirms. When both have signed, the server prints the signed PDF: the signatures on
the initials line of every page and in the «Signatures of the parties» block, plus a last page with the signing record
(who, when in Tbilisi time, IP, device, SHA-256 of the signed text). That PDF becomes the contract's signed PDF, the
contract → «Договор подписан», the PDF goes to Google Drive and to the agent's Telegram.

A link belongs to the exact text it was made for (doc_sha256): editing the draft voids unsigned links and is refused
once someone has signed (cancel the signing first)."""
import base64
import hashlib
import html as H
import logging
import re
import secrets
import struct

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Contract, ContractSigner, User
from app.services import contracts as C, jobs
from app.services.common import TZ, log_event, now
from app.services.errors import DomainError

log = logging.getLogger(__name__)

ROLES = ("owner", "tenant")
ROLE_LABEL = {  # ka / ru / en
    "owner": {"ka": "გამქირავებელი", "ru": "Арендодатель", "en": "Owner"},
    "tenant": {"ka": "დამქირავებელი", "ru": "Арендатор", "en": "Tenant"},
}
CRM_ROLE = {"owner": "Собственник", "tenant": "Арендатор"}
SIGNABLE_KINDS = ("rent", "comm")  # the parties are an owner and a tenant
MAX_SIGNATURE = 400_000  # the data: URI of the drawn PNG
PNG_PREFIX = "data:image/png;base64,"


def doc_hash(c: Contract) -> str:
    return hashlib.sha256((c.html or "").encode("utf-8")).hexdigest()


def path(token: str) -> str:
    return f"/esign/{token}"


def signers(db: Session, cid: int) -> list[ContractSigner]:
    rows = list(db.scalars(select(ContractSigner).where(ContractSigner.contract_id == cid)))
    return sorted(rows, key=lambda s: ROLES.index(s.role))


def out(db: Session, c: Contract) -> dict | None:
    """The contract's signing state for the CRM (contracts.out → `esign`), null when it was never started."""
    rows = signers(db, c.id)
    if not rows:
        return None
    done = all(s.signed_at for s in rows)
    return {"complete": done, "doc_sha256": rows[0].doc_sha256,
            "signers": [{"role": s.role, "label": CRM_ROLE[s.role], "path": path(s.token) if not done else None,
                         "signed_at": s.signed_at.isoformat() if s.signed_at else None, "full_name": s.full_name}
                        for s in rows]}


# ---------------------------------------------------------------- the agent: start / cancel

def start(db: Session, cid: int, user: User) -> Contract:
    """Links for both parties (kept if they already exist for this text)."""
    c = C.get(db, cid, user, lock=True)
    if c.status != "draft":
        raise DomainError("bad_status", f"Договор: {C.STATUS_LABEL[c.status]}", 409)
    if c.kind not in SIGNABLE_KINDS:
        raise DomainError("bad_kind", "Электронная подпись пока только для договоров аренды", 409)
    h = doc_hash(c)
    have = {s.role: s for s in signers(db, c.id)}
    for role in ROLES:
        s = have.get(role)
        if s and s.doc_sha256 != h:  # the text changed since (update() drops these, this is a guard)
            if s.signed_at:
                raise DomainError("already_signed", "Договор изменён после подписи: отмените подписание", 409)
            db.delete(s)
            db.flush()
            s = None
        if not s:
            db.add(ContractSigner(contract_id=c.id, role=role, token=secrets.token_urlsafe(24), doc_sha256=h,
                                  created_by=user.id))
    # not c.updated_at: the text is the same, and updated_at > generated_at means «PDF stale» (contracts.stale)
    log_event(db, "contract", c.id, "esign_started", user)
    db.commit()
    return c


def cancel(db: Session, cid: int, user: User) -> Contract:
    """Void the links (and signatures already given) of a draft: then it can be edited and sent again."""
    c = C.get(db, cid, user, lock=True)
    if c.status != "draft":
        raise DomainError("bad_status", f"Договор: {C.STATUS_LABEL[c.status]}", 409)
    rows = signers(db, c.id)
    for s in rows:
        db.delete(s)
    log_event(db, "contract", c.id, "esign_cancelled", user, signed=[s.role for s in rows if s.signed_at])
    db.commit()
    return c


def before_edit(db: Session, c: Contract) -> None:
    """contracts.update: an edited text voids the unsigned links; once a party has signed the text is frozen."""
    rows = signers(db, c.id)
    if any(s.signed_at for s in rows):
        raise DomainError("already_signed", "Одна из сторон уже подписала договор. Чтобы изменить текст, отмените "
                                            "электронное подписание", 409)
    for s in rows:
        db.delete(s)


# ---------------------------------------------------------------- the party: view / sign (public, by token)

def _by_token(db: Session, token: str, lock: bool = False) -> tuple[ContractSigner, Contract]:
    s = db.scalar(select(ContractSigner).where(ContractSigner.token == token).with_for_update(of=ContractSigner)
                  if lock else select(ContractSigner).where(ContractSigner.token == token)) \
        if token and len(token) >= 20 else None
    c = db.get(Contract, s.contract_id, with_for_update=lock, populate_existing=lock) if s else None
    if not s or not c or c.status == "closed_no_deal":
        raise DomainError("not_found", "Ссылка недействительна. Попросите агента прислать новую")
    if c.status == "draft" and s.doc_sha256 != doc_hash(c):
        raise DomainError("not_found", "Договор изменён. Попросите агента прислать новую ссылку")
    return s, c


def view(db: Session, token: str) -> dict:
    s, c = _by_token(db, token)
    rows = signers(db, c.id)
    done = all(r.signed_at for r in rows) and c.status != "draft"
    data = c.data or {}
    pre = {"owner": (data.get("owner_full_name") or c.owner_name, data.get("owner_personal_number")),
           "tenant": (data.get("client_full_name") or c.client_name, data.get("client_personal_number"))}[s.role]
    return {
        "role": s.role, "role_label": ROLE_LABEL[s.role], "contract_id": c.id, "kind": c.kind, "lang": c.lang,
        "title": c.title, "address": c.address, "cadastral": c.cadastral, "doc_sha256": s.doc_sha256,
        "prefill": {"full_name": pre[0] or "", "personal_id": pre[1] or ""},
        "signed_at": s.signed_at.isoformat() if s.signed_at else None, "full_name": s.full_name,
        "parties": [{"role": r.role, "label": ROLE_LABEL[r.role], "signed": bool(r.signed_at)} for r in rows],
        "complete": done,
        "html": None if s.signed_at else c.html,  # the text to read before signing
        "has_extract": bool(_extract_key(c)),
    }


def _extract_key(c: Contract) -> str:
    """The registry extract saved with the contract (data.extract_file); only our own storage keys."""
    from app.services import registry
    key = str((c.data or {}).get("extract_file") or "")
    return key if registry.EXTRACT_KEY_RE.match(key) else ""


def _extract(c: Contract) -> bytes | None:
    from app import storage
    key = _extract_key(c)
    return storage.read(key) if key else None


def extract(db: Session, token: str) -> tuple[Contract, bytes]:
    """The extract for this contract's signers (Irakli 11-13: it goes with the signing link): the token is the access."""
    _, c = _by_token(db, token)
    data = _extract(c)
    if not data:
        raise DomainError("not_found", "Выписка не сохранена")
    return c, data


def _png(sig: str) -> bytes:
    if not isinstance(sig, str) or not sig.startswith(PNG_PREFIX) or len(sig) > MAX_SIGNATURE:
        raise DomainError("bad_signature", "Подпись не получена, распишитесь ещё раз")
    try:
        raw = base64.b64decode(sig[len(PNG_PREFIX):], validate=True)
    except ValueError:
        raise DomainError("bad_signature", "Подпись не получена, распишитесь ещё раз")
    if not raw.startswith(b"\x89PNG\r\n\x1a\n") or raw[12:16] != b"IHDR":
        raise DomainError("bad_signature", "Подпись не получена, распишитесь ещё раз")
    w, h = struct.unpack(">II", raw[16:24])
    if w < 24 or h < 12:  # a dot / an empty pad cropped to nothing
        raise DomainError("bad_signature", "Подпись слишком маленькая, распишитесь ещё раз")
    return raw


def sign(db: Session, token: str, data: dict, ip: str | None, user_agent: str | None) -> dict:
    s, c = _by_token(db, token, lock=True)
    if s.signed_at:
        raise DomainError("already_signed", "Вы уже подписали этот договор", 409)
    if c.status != "draft":
        raise DomainError("bad_status", "Договор уже оформлен", 409)
    if (data.get("doc_sha256") or "") != s.doc_sha256:
        raise DomainError("doc_changed", "Текст договора изменился: обновите страницу", 409)
    f = {k: re.sub(r"\s+", " ", str(data.get(k) or "")).strip()[:200] for k in ("full_name", "personal_id", "phone")}
    if not (f["full_name"] and f["personal_id"] and f["phone"]):
        raise DomainError("missing_fields", "Заполните ФИО, личный номер и телефон")
    if data.get("consent") is not True:
        raise DomainError("consent_required", "Нужно согласие с условиями договора")
    _png(data.get("signature"))
    s.full_name, s.personal_id, s.phone, s.signature = f["full_name"], f["personal_id"], f["phone"], data["signature"]
    s.lang = str(data.get("lang") or "")[:5] or None
    s.ip, s.user_agent, s.signed_at = (ip or "")[:100] or None, (user_agent or "")[:300] or None, now()
    log_event(db, "contract", c.id, "esign_signed", None, label=f"{CRM_ROLE[s.role]}: {s.full_name}", role=s.role,
              full_name=s.full_name, personal_id=s.personal_id, phone=s.phone, ip=s.ip, user_agent=s.user_agent,
              doc_sha256=s.doc_sha256)
    db.commit()
    rows = signers(db, c.id)
    if all(r.signed_at for r in rows):
        try:
            finalize(db, c.id)
        except Exception:  # noqa: BLE001 — the signature is saved; the worker prints the PDF again
            db.rollback()
            log.exception("esign: signed PDF for contract %s failed, retrying in the worker", c.id)
            jobs.schedule(db, "contract_esign_pdf", c.id, now())
            db.commit()
    else:
        _notify(db, c, f"✍️ Договор #{c.id}: {CRM_ROLE[s.role].lower()} {s.full_name} подписал(а) электронно. "
                       f"Ждём вторую сторону.")
    return view(db, token)


# ---------------------------------------------------------------- the signed PDF

def _dims(png: bytes) -> tuple[int, int]:
    w, h = struct.unpack(">II", png[16:24])
    return max(w, 1), max(h, 1)


def _svg_uri(sig: str, height: int) -> str:
    """The signature for an @page margin box (content: url(…) takes the image's own size): an SVG of the given CSS
    height wrapping the full-resolution PNG."""
    w, h = _dims(_png(sig))
    width = max(1, round(w * height / h))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{width}" '
           f'height="{height}" viewBox="0 0 {w} {h}"><image width="{w}" height="{h}" href="{sig}" xlink:href="{sig}"/></svg>')
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def _tbilisi(dt) -> str:
    return dt.astimezone(TZ).strftime("%d.%m.%Y %H:%M:%S") + " (Tbilisi, UTC+4)"


SIG_CSS = """.esig{height:15mm;vertical-align:middle;margin:0 4px}.esig-n{font-weight:400;font-size:9pt}
.esign-record{break-before:page;font-size:9.5pt}.esign-record h2{font-size:12pt;text-align:center;margin:0 0 10px}
.esign-record table{width:100%;border-collapse:collapse;margin:8px 0 14px}.esign-record td{border:1px solid #111;padding:4px 6px;vertical-align:top}
.esign-record td:first-child{width:38%;color:#333}.esign-record .esig{height:20mm}.esign-record .mono{font-family:monospace;font-size:8.5pt;word-break:break-all}"""


def _record_lang(c: Contract) -> str:
    return "en" if c.lang == "EN" else "ru"


def _record(c: Contract, rows: list[ContractSigner]) -> str:
    second = _record_lang(c)
    T = {
        "title": {"ka": "ელექტრონული ხელმოწერის ოქმი", "ru": "Протокол электронного подписания", "en": "Electronic signature record"},
        "doc": {"ka": "ხელშეკრულება", "ru": "Договор", "en": "Contract"},
        "hash": {"ka": "ტექსტის SHA-256", "ru": "SHA-256 текста", "en": "Text SHA-256"},
        "name": {"ka": "სახელი, გვარი", "ru": "ФИО", "en": "Full name"},
        "pid": {"ka": "პირადი ნომერი", "ru": "Личный номер", "en": "Personal ID"},
        "phone": {"ka": "ტელეფონი", "ru": "Телефон", "en": "Phone"},
        "at": {"ka": "ხელმოწერის დრო", "ru": "Время подписи", "en": "Signed at"},
        "ip": {"ka": "IP მისამართი", "ru": "IP-адрес", "en": "IP address"},
        "ua": {"ka": "მოწყობილობა", "ru": "Устройство", "en": "Device"},
        "sig": {"ka": "ხელმოწერა", "ru": "Подпись", "en": "Signature"},
        "note": {"ka": "ხელშეკრულება ხელმოწერილია ელექტრონულად Homes in Georgia CRM-ში: თითოეულმა მხარემ მიიღო პირადი "
                       "ბმული, გაეცნო ტექსტს, დაეთანხმა პირობებს და მოაწერა ხელი.",
                 "ru": "Договор подписан электронно в Homes in Georgia CRM: каждая сторона получила личную ссылку, "
                       "ознакомилась с текстом, согласилась с условиями и расписалась.",
                 "en": "The contract was signed electronically in Homes in Georgia CRM: each party received a personal link, "
                       "read the text, agreed to the terms and signed."},
    }
    t = lambda k: f"{T[k]['ka']} / {T[k][second]}"  # noqa: E731
    e = lambda v: H.escape(v or "—")  # noqa: E731
    head = (f"<h2>{t('title')}</h2><table><tr><td>{t('doc')}</td><td>#{c.id} · {e(c.title)} · {e(c.cadastral)}"
            f"</td></tr><tr><td>{t('hash')}</td><td class=\"mono\">{rows[0].doc_sha256}</td></tr></table>")
    parts = []
    for s in rows:
        role = f"{ROLE_LABEL[s.role]['ka']} / {ROLE_LABEL[s.role][second]}"
        parts.append(f"<table><tr><td colspan=\"2\"><b>{H.escape(role)}</b></td></tr>"
                     f"<tr><td>{t('name')}</td><td>{e(s.full_name)}</td></tr>"
                     f"<tr><td>{t('pid')}</td><td>{e(s.personal_id)}</td></tr>"
                     f"<tr><td>{t('phone')}</td><td>{e(s.phone)}</td></tr>"
                     f"<tr><td>{t('at')}</td><td>{_tbilisi(s.signed_at)}</td></tr>"
                     f"<tr><td>{t('ip')}</td><td>{e(s.ip)}</td></tr>"
                     f"<tr><td>{t('ua')}</td><td class=\"mono\">{e(s.user_agent)}</td></tr>"
                     f"<tr><td>{t('sig')}</td><td><img class=\"esig\" src=\"{s.signature}\" alt=\"\"></td></tr></table>")
    note = f"<p>{H.escape(T['note']['ka'])}</p><p>{H.escape(T['note'][second])}</p>"
    return f'<div class="contract-doc"><section class="esign-record">{head}{"".join(parts)}{note}</section></div>'


FOOT_RE = re.compile(r'@bottom-center\{content:("(?:[^"\\]|\\.)*")')
PARA_RE = re.compile(r"<p\b[^>]*>.*?</p>", re.S)
LINE_RE = re.compile(r"_{20,}")


def signed_html(c: Contract, rows: list[ContractSigner]) -> str:
    """The stored contract HTML with the parties' signatures put in: the «Signatures of the parties» lines (the last two
    long underscore lines: owner, then tenant), the initials line of every page (@page footer), the record page."""
    page = c.html
    by = {s.role: s for s in rows}
    img = lambda s: f'<img class="esig" src="{s.signature}" alt=""><span class="esig-n">{H.escape(s.full_name or "")}</span>'  # noqa: E731
    lines = [m for m in PARA_RE.finditer(page) if LINE_RE.search(m.group(0))][-2:]
    if len(lines) == 2:
        for m, role in reversed(list(zip(lines, ROLES))):  # from the end: earlier offsets stay valid
            p = LINE_RE.sub(lambda _: img(by[role]), m.group(0), count=1)
            page = page[:m.start()] + p + page[m.end():]
    extra = SIG_CSS
    foot = FOOT_RE.search(page)
    if foot:  # “გამქირავებელი“ ____  “დამქირავებელი“ ____ → the two signatures on every page
        text = foot.group(1)[1:-1]
        pieces = re.split(r"_{5,}", text)
        if len(pieces) == 3:
            content = (f'"{pieces[0]}" url({_svg_uri(by["owner"].signature, 34)}) "{pieces[1]}" '
                       f'url({_svg_uri(by["tenant"].signature, 34)}) "{pieces[2]}"')
            extra += f"@media print{{@page{{@bottom-center{{content:{content}}}}}}}"
    page = page.replace("</head>", f"<style>{extra}</style></head>", 1) if "</head>" in page else f"<style>{extra}</style>{page}"
    rec = _record(c, rows)
    return page.replace("</body>", rec + "</body>", 1) if "</body>" in page else page + rec


def finalize(db: Session, cid: int) -> str:
    """Both parties signed: print the signed PDF, the contract → signed (Drive + the agent's Telegram). Idempotent."""
    c = db.get(Contract, cid, with_for_update=True, populate_existing=True)
    rows = signers(db, cid) if c else []
    if not c or c.status != "draft" or not rows or not all(s.signed_at for s in rows):
        return "stale"
    pdf = C.render_pdf(signed_html(c, rows))
    c.signed_pdf_path = C._store_pdf(c, pdf, signed=True)
    c.status, c.signed_at, c.signed_by, c.drive_file_id, c.updated_at = "signed", now(), None, None, now()
    jobs.schedule(db, "contract_drive", c.id, now())
    log_event(db, "contract", c.id, "signed", None, label="Электронная подпись", esign=True,
              pdf_sha256=hashlib.sha256(pdf).hexdigest())
    db.commit()
    _notify(db, c, f"✅ Договор #{c.id} подписан электронно обеими сторонами.", pdf)
    return "ok"


def _notify(db: Session, c: Contract, text: str, pdf: bytes | None = None) -> None:
    """The agent who made the contract (Telegram); never fails the signing."""
    from app.telegram import client as tg
    u = db.get(User, c.created_by) if c.created_by else None
    if not u or not u.tg_id:
        return
    try:
        if pdf:
            tg.call("sendDocument", files={"document": (C.filename(c, True), pdf, "application/pdf")}, chat_id=u.tg_id,
                    caption=text)
        else:
            tg.call("sendMessage", chat_id=u.tg_id, text=text)
    except Exception:  # noqa: BLE001
        log.warning("esign: telegram notify failed for contract %s", c.id)


def signed_pdf(db: Session, token: str) -> tuple[Contract, bytes]:
    """The party's copy once the contract is signed by both (the page's «Скачать PDF»)."""
    s, c = _by_token(db, token)
    if c.status == "draft" or not c.signed_pdf_path:
        raise DomainError("not_found", "Договор ещё не подписан всеми сторонами")
    return c, C.pdf_bytes(c, signed=True)
