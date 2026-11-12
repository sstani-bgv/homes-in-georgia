"""Work reports (Google Sheets Reports_* replacement) + counters vs plans + work-group topic texts.

Contract with notify.report_posted(report): `report.data["posts"]` is a list of {"topic", "text"} where topic is one of
photo|showing|stickers|deal|t14|daily|general (→ env TOPIC_<TOPIC>) and text is Telegram HTML (apps-script texts)."""
import html
import logging
from datetime import date, datetime

from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from app.models import FIELD_SQL, REPORT_KINDS, Deal, Report, User, at_least, is_field
from app.services.common import TZ, log_event, now
from app.services.errors import DomainError
from app.telegram import notify

log = logging.getLogger(__name__)
COUNTED = {"photo": "photo", "showing": "showing", "stickers": "stickers"}
DEFAULT_PLANS = {"photo": 15, "showing": 15, "stickers": 15, "rental": 15}


def _e(v) -> str:
    return html.escape(str(v if v not in (None, "") else "—"))


def _find_user(db: Session, ident) -> User | None:
    if ident in (None, ""):
        return None
    if isinstance(ident, int) or str(ident).isdigit():
        u = db.get(User, int(ident))
        if u:
            return u
    name = str(ident).lstrip("#@").strip().lower()
    return db.scalar(select(User).where((func.lower(User.name) == name) | (func.lower(User.username) == name)))


def month_bounds(month: str | None):
    """"M/YYYY" or "YYYY-MM" → (year, month); None → current month in Tbilisi time."""
    if month:
        try:
            if "/" in month:
                m, y = month.split("/")
            else:
                y, m = month.split("-")[:2]
            return int(y), int(m)
        except ValueError:
            raise DomainError("bad_month", "month must be M/YYYY or YYYY-MM")
    t = now().astimezone(TZ)
    return t.year, t.month


def count(db: Session, kind: str, recruit_id: int, month: str | None = None) -> int:
    y, m = month_bounds(month)
    return db.scalar(select(func.count()).select_from(Report).where(
        Report.kind == kind, Report.recruit_id == recruit_id, extract("year", Report.date) == y,
        extract("month", Report.date) == m)) or 0


def _plan(u: User | None, key: str) -> int:
    return int(((u.plans if u else None) or DEFAULT_PLANS).get(key, DEFAULT_PLANS[key]))


def build_posts(kind: str, d: dict) -> list[dict]:
    rec, men = _e(d.get("recruit")), _e(d.get("mentor"))
    cnt = f"{d.get('counter', '?')}/{d.get('plan', 15)}"
    if kind == "photo":
        return [{"topic": "photo", "text": f"📷 <b>#Фотография | Отчёт</b>\n\nРекрут: <b>#{rec}</b>\nНаставник: {men}"
                 f"\nРайон/Адрес: {_e(d.get('district_address'))}\nСсылка: {_e(d.get('ad_link_advertising'))}"
                 f"\nУчёт: <b>{cnt}</b>\n\nДата: {_e(d.get('date'))}"}]
    if kind == "showing":
        return [{"topic": "showing", "text": f"🏠 <b>#Просмотры | Отчёт</b>\n\nРекрут: <b>#{rec}</b>\nНаставник: {men}"
                 f"\nРайон/Адрес: {_e(d.get('district_address'))}\nСсылка: {_e(d.get('ad_link_advertising'))}"
                 f"\nКлиент: {_e(d.get('client_telegram'))}\nРезультат: <b>{_e(d.get('result'))}</b>"
                 f"\nУчёт: <b>{cnt}</b>\n\nДата: {_e(d.get('date'))}"}]
    if kind == "stickers":
        return [{"topic": "stickers", "text": f"📌 <b>#Расклейка | Отчёт</b>\n\nРекрут: <b>@{rec}</b>\nНаставник: {men}"
                 f"\nРайон: {_e(d.get('district'))}\nРасклеено: <b>{_e(d.get('stickers_count'))} стикеров</b>"
                 f"\nУчёт: <b>{cnt}</b>\n\nДата: {_e(d.get('date'))}"}]
    if kind == "general":
        a, posts = _e(d.get("author")), []
        if int(d.get("photo") or 0) > 0:
            posts.append({"topic": "photo", "text": f"📸 <b>#Фотография | Отчёт</b>\n\nАгент: <b>#{a}</b>\n"
                          + (f"Адрес: {_e(d['photoAddr'])}\n" if d.get("photoAddr") else "") + f"Фото: <b>{_e(d['photo'])}</b>"
                          + "".join("\n" + _e(x) for x in d.get("photoLinks") or [])})
        if int(d.get("showing") or 0) > 0:
            posts.append({"topic": "showing", "text": f"🏠 <b>#Просмотры | Отчёт</b>\n\nАгент: <b>#{a}</b>\n"
                          + (f"Район: {_e(d['showingDist'])}\n" if d.get("showingDist") else "")
                          + f"Показов: <b>{_e(d['showing'])}</b>" + "".join("\n" + _e(x) for x in d.get("showingLinks") or [])})
        if int(d.get("stickers") or 0) > 0:
            posts.append({"topic": "stickers", "text": f"📌 <b>#Расклейка | Отчёт</b>\n\nАгент: <b>#{a}</b>\n"
                          + (f"Район: {_e(d['stickersDist'])}\n" if d.get("stickersDist") else "")
                          + f"Стикеров: <b>{_e(d['stickers'])}</b>"})
        if not posts and d.get("preview"):
            posts.append({"topic": "general", "text": _e(d["preview"])})
        return posts
    if kind == "daily":
        return [{"topic": "daily", "text": _e(d.get("preview")) if d.get("preview") else f"📅 Daily Report · {_e(d.get('author'))}"}]
    if kind == "makefoto":
        status = {"added": "\n\n✅ <b>Добавлено в базу</b>",
                  "exists": "\n\n⚠️ <b>Уже есть в базе!</b> Проверь перед съёмкой."}.get(d.get("status"), "")
        body = (f"Address: {_e(d.get('address'))}\nDate/time: {_e(d.get('date'))}\nAgent: #{_e(d.get('agent'))}"
                f"\nOwner: {_e(d.get('owner'))} {_e(d.get('phone')) if d.get('phone') else ''}\nLink: {_e(d.get('link'))}")
        return [{"topic": "photo", "text": "📸 <b>Make Foto From Platform</b>\n\n" + body + status}]
    if kind == "calc":
        excl = "#Эксклюзив" if d.get("is_exclusive") else "#Неэксклюзив"
        t = "Продажа" if d.get("deal_type") == "sale" else "Аренда"
        return [{"topic": "deal", "text": f"💼 <b>Расчёт сделки | {excl}</b>\nАгент: {_e(d.get('agent'))} ({_e(d.get('agent_level'))}%)\n"
                 f"Тип: {t}\nСсылка: {_e(d.get('link'))}\n\nPrice: <b>{_e(d.get('price'))}$</b>\n"
                 f"Commission ({_e(d.get('comm_pct'))}%): {_e(d.get('comm_total'))}$\nPlatform: {_e(d.get('platform'))}$\n"
                 f"Agent: <b>{_e(d.get('agent_amt'))}$</b>"
                 + (f"\nПериод: {_e(d.get('rent_from'))} – {_e(d.get('rent_to'))}" if d.get("rent_from") else "")}]
    if kind == "assigned_showing":
        return [{"topic": "showing", "text": f"📅 <b>Назначен просмотр</b>\n\nАгент: #{_e(d.get('agent'))}\n"
                 f"Клиент: {_e(d.get('client_name'))}\nАдрес: {_e(d.get('address'))}\n"
                 f"Когда: {_e(d.get('date'))} {_e(d.get('time')) if d.get('time') else ''}\n{_e(d.get('comment')) if d.get('comment') else ''}".strip()}]
    return []


def _parse_date(v) -> date:
    if isinstance(v, date):
        return v
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(str(v)[:16] if "T" in str(v) else str(v)[:10], fmt).date()
        except (TypeError, ValueError):
            continue
    return now().astimezone(TZ).date()


def save(db: Session, kind: str, data: dict, author: User | None, post: bool = True) -> Report:
    if kind not in REPORT_KINDS:
        raise DomainError("bad_kind", f"report kind must be one of {REPORT_KINDS}")
    d = {k: v for k, v in (data or {}).items() if k not in ("posts", "counter")}
    recruit = _find_user(db, d.get("recruit_id") or d.get("recruit"))
    if recruit is None and author is not None and is_field(author) and kind in COUNTED:
        recruit = author
    mentor = _find_user(db, d.get("mentor_id") or d.get("mentor"))
    if mentor is None and recruit is not None and recruit.mentor_id:
        mentor = db.get(User, recruit.mentor_id)
    d.setdefault("recruit", recruit.name if recruit else None)
    d.setdefault("mentor", mentor.name if mentor else None)
    d.setdefault("author", author.name if author else None)
    d.setdefault("agent", author.name if author else None)
    rdate = _parse_date(d.get("date"))
    r = Report(kind=kind, author_id=author.id if author else None, recruit_id=recruit.id if recruit else None,
               mentor_id=mentor.id if mentor else None, date=rdate, data={})
    db.add(r)
    db.flush()
    if kind in COUNTED and recruit:
        d["counter"] = count(db, kind, recruit.id, f"{rdate.month}/{rdate.year}")
        d["plan"] = _plan(recruit, COUNTED[kind])
    d["posts"] = build_posts(kind, d)
    r.data = d
    log_event(db, "report", r.id, "created", author, kind=kind)
    db.commit()
    if post:
        try:
            notify.report_posted(r)
        except Exception:  # noqa: BLE001
            log.exception("notify.report_posted failed")
    return r


def out(r: Report) -> dict:
    return {"id": r.id, "kind": r.kind, "author_id": r.author_id, "recruit_id": r.recruit_id, "mentor_id": r.mentor_id,
            "date": r.date.isoformat() if r.date else None, "data": r.data, "created_at": r.created_at.isoformat()}


def query(db: Session, user: User, kind: str | None = None, author_id: int | None = None, month: str | None = None):
    stmt = select(Report)
    if not at_least(user, "operator"):
        stmt = stmt.where((Report.author_id == user.id) | (Report.recruit_id == user.id) | (Report.mentor_id == user.id))
    elif author_id:
        stmt = stmt.where((Report.author_id == author_id) | (Report.recruit_id == author_id))
    if kind:
        stmt = stmt.where(Report.kind.in_(kind.split(",")))
    if month:
        y, m = month_bounds(month)
        stmt = stmt.where(extract("year", Report.date) == y, extract("month", Report.date) == m)
    return stmt.order_by(Report.created_at.desc())


def counters(db: Session, month: str | None = None, user: User | None = None) -> list[dict]:
    """Per recruit/agent this month: photo/showing/stickers reports + rental deals vs plans (Counters sheet)."""
    y, m = month_bounds(month)
    people = db.scalars(select(User).where(FIELD_SQL, User.is_active.is_(True)).order_by(User.name)).all()
    if user is not None and not at_least(user, "operator"):
        people = [p for p in people if p.id == user.id or p.mentor_id == user.id]
    rep = {(k, rid): n for k, rid, n in db.execute(
        select(Report.kind, Report.recruit_id, func.count()).where(
            extract("year", Report.date) == y, extract("month", Report.date) == m, Report.recruit_id.isnot(None))
        .group_by(Report.kind, Report.recruit_id)).all()}
    mo = f"{m}/{y}"
    deals = {}
    for aid, rid in db.execute(select(Deal.agent_id, Deal.recruit_id).where(Deal.month == mo, Deal.status != "cancelled")):
        for uid in {aid, rid} - {None}:
            deals[uid] = deals.get(uid, 0) + 1
    out_ = []
    for p in people:
        row = {"user": {"id": p.id, "name": p.name, "role": p.role, "mentor_id": p.mentor_id}, "month": mo}
        for k in ("photo", "showing", "stickers"):
            row[k] = {"done": rep.get((k, p.id), 0), "plan": _plan(p, k)}
        row["rental"] = {"done": deals.get(p.id, 0), "plan": _plan(p, "rental")}
        out_.append(row)
    return out_
