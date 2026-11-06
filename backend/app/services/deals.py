"""Deals (#Rented), commission by level (apps-script saveRentalWithT0), T14 and contract reminders."""
import html
import logging
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from app.config import settings as env
from app.models import FIELD, STAFF, Client, Deal, Evaluation, Lead, Property, User
from app.services import evaluations, jobs, users
from app.services.common import TZ, get_or_404, log_event, now
from app.services.errors import DomainError, forbidden
from app.telegram import notify

log = logging.getLogger(__name__)

FIELDS = ("lead_id", "property_id", "agent_id", "recruit_id", "deal_type", "sub_type", "is_exclusive", "is_rerented",
          "price", "commission_pct", "commission_total", "platform_amount", "recruit_pct", "recruit_amount", "client_src",
          "agent_level", "cadastral", "ad_link", "address", "metro", "client_name", "client_phone", "client_tg",
          "client_birth", "owner_name", "owner_phone", "owner_birth", "rent_from", "rent_to", "source")
MONEY = ("price", "commission_pct", "commission_total", "platform_amount", "recruit_pct", "recruit_amount")
DATES = ("client_birth", "owner_birth", "rent_from", "rent_to")


def _d(v) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v).replace(",", ".").replace("$", "").replace(" ", ""))
    except InvalidOperation:
        return None


def _round(v: Decimal) -> Decimal:
    """JS Math.round for the non-negative amounts here: half up (Python round() is banker's rounding)."""
    return v.quantize(Decimal(1), rounding=ROUND_HALF_UP)


def _r(v: Decimal) -> float:
    return float(round(v, 2))


def parse_date(v) -> date | None:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def calc(price=None, commission_pct=None, commission_total=None, platform_amount=None, agent_level=50,
         recruit_pct=None, recruit_amount=None, client_src=None, is_rerented=False, **_ignored) -> dict:
    """comm = commission_total or round(price*pct/100); platform = given or comm*(100-level)/100;
    recruit = given or comm*recruit_pct/100; client_src (who passed the client) = 10% of comm;
    agent = comm - platform - recruit - client_src."""
    price_d = _d(price) or Decimal(0)
    pct = _d(commission_pct)
    pct = Decimal(50) if pct is None else pct
    level = int(agent_level or 50)
    if is_rerented:
        comm = Decimal(0)
    else:
        comm = _d(commission_total) or _round(price_d * pct / 100)
    plat = _d(platform_amount)
    if plat is None:
        plat = comm * (100 - level) / 100
    rpct = _d(recruit_pct) or Decimal(0)
    rec = _d(recruit_amount)
    if rec is None:
        rec = comm * rpct / 100
    src = _round(comm * Decimal("0.1")) if client_src else Decimal(0)
    agent = comm - plat - rec - src
    return {"price": _r(price_d), "commission_pct": _r(pct), "commission_total": _r(comm), "platform_amount": _r(plat),
            "platform_pct": round(float(plat / comm * 100)) if comm else 100 - level, "agent_level": level,
            "recruit_pct": _r(rpct), "recruit_amount": _r(rec), "client_src_amount": _r(src), "agent_amount": _r(agent)}


def _month(d: date | None) -> str:
    d = d or now().astimezone(TZ).date()
    return f"{d.month}/{d.year}"


def create(db: Session, data: dict, actor: User | None, commit: bool = True, notify_lead: bool = True) -> Deal:
    """C9b deal form → deals.status=pending. Optional T0 (`t0_h2o_client`.. or `t0: {...}`) stored as evaluation."""
    data = dict(data or {})
    agent = None
    if data.get("agent_id"):
        agent = get_or_404(db, User, int(data["agent_id"]), "agent")
    elif actor is not None and actor.role in FIELD:
        agent = actor
    if actor is not None and actor.role not in STAFF and agent is not None and agent.id != actor.id:
        raise forbidden("agents create only their own deals")
    picked = int(data["property_id"]) if data.get("property_id") else None
    lead = None
    if data.get("lead_id"):
        lead = get_or_404(db, Lead, int(data["lead_id"]), "lead", lock=True)
        if actor is not None and actor.role not in STAFF and lead.agent_id != actor.id:
            raise forbidden("not your lead")
        live = live_deal_for_lead(db, lead)
        if live is not None:  # double submit of the deal form: one live deal per lead
            return live
        agent = agent or (db.get(User, lead.agent_id) if lead.agent_id else None)
        data.setdefault("property_id", lead.property_id)
        staff = actor is None or actor.role in STAFF
        if not staff and lead.status not in ("time_chosen", "viewing_done", "thinking", "deal_pending"):
            raise DomainError("bad_status", "deal can be filled only after the viewing time is chosen")
        client = db.get(Client, lead.client_id)
        if client and (staff or lead.contacts_shared_at):
            data.setdefault("client_name", client.name)
            data.setdefault("client_phone", client.phone)
            data.setdefault("client_tg", f"@{client.username}" if client.username else None)
    from app.services import properties
    if data.get("new_object") and not data.get("property_id"):
        data["property_id"] = _new_object(db, data, agent, actor).id
    elif picked and actor is not None and not (lead and lead.property_id == picked):
        p = get_or_404(db, Property, picked, "property")
        blocked = properties.deal_block(db, p, actor)
        if blocked:
            raise DomainError("object_taken", blocked, 409)
    if data.get("property_id"):
        p = db.get(Property, int(data["property_id"]))
        if p:  # stage 7: the object's data win over the form (read-only there; changed via the object's requests)
            data.update(properties.deal_fields(db, p, actor, data.get("deal_type") or "rent"))
    if actor is not None and actor.role not in STAFF:  # payout inputs an agent must not set for themselves
        data.pop("agent_level", None)
        data.pop("platform_amount", None)
    level = int(data.get("agent_level") or users.effective_level(agent))
    nums = calc(price=data.get("price"), commission_pct=data.get("commission_pct"),
                commission_total=data.get("commission_total"), platform_amount=data.get("platform_amount"),
                agent_level=level, recruit_pct=data.get("recruit_pct"), recruit_amount=data.get("recruit_amount"),
                client_src=data.get("client_src"), is_rerented=bool(data.get("is_rerented")))
    deal = Deal(status="pending", source=data.get("source") or "crm", raw={k: str(v) for k, v in data.items()})
    for k in FIELDS:
        if k in data and data[k] not in (None, ""):
            v = data[k]
            if k in DATES:
                v = parse_date(v)
            elif k in ("is_exclusive", "is_rerented"):
                v = v in (True, "true", "1", 1, "on", "yes")
            setattr(deal, k, v)
    for k in ("commission_pct", "commission_total", "platform_amount", "agent_amount", "recruit_pct", "recruit_amount",
              "client_src_amount", "price"):
        setattr(deal, k, nums[k])
    deal.agent_level = level
    deal.agent_id = agent.id if agent else None
    if data.get("recruit") and not deal.recruit_id:
        r = db.scalar(select(User).where(User.name == str(data["recruit"]).lstrip("#")))
        deal.recruit_id = r.id if r else None
    deal.deal_type = deal.deal_type or "rent"
    deal.t14_date = deal.rent_from + timedelta(days=env.T14_DAYS) if deal.rent_from else None
    deal.t14_status = "pending"
    deal.month = data.get("month") or _month(deal.rent_from)
    db.add(deal)
    try:
        db.flush()
    except IntegrityError:  # deals_lead_live_uq: a concurrent submit for the same lead won
        db.rollback()
        live = live_deal_for_lead(db, db.get(Lead, int(data["lead_id"]))) if data.get("lead_id") else None
        if live is not None:
            return live
        raise
    t0 = data.get("t0") or {k[3:]: data[k] for k in data if k.startswith("t0_")}
    t0_ev = None
    if t0 and any(t0.get(k) not in (None, "") for k in ("h2o_client", "h2o_owner", "h2o_process", "result")):
        t0_ev = evaluations.create(db, {"deal_id": deal.id, "kind": "t0", "author": "agent", **t0}, actor, commit=False)
    if lead:
        from app.services import leads
        leads.attach_deal(db, lead, deal.id, actor)
    log_event(db, "deal", deal.id, "created", actor, price=nums["price"], commission=nums["commission_total"])
    if commit:
        db.commit()
        if t0_ev is not None:
            evaluations.notify_if_alarming(t0_ev)
    return deal


def _new_object(db: Session, data: dict, agent: User | None, actor: User | None) -> Property:
    """Stage 7 «Объекта нет в базе»: the object typed in the deal form joins the base as the agent's object (owner bound
    to him, stage 6.5) waiting for the operator's check (source_type=deal, no verified_at); the deal can't be confirmed
    before it. An object the matcher calls `same` must be picked instead. No commit."""
    from app.services import matching, properties
    if agent is None:
        raise DomainError("agent_required", "Укажите агента")
    if not (data.get("address") or "").strip():
        raise DomainError("address_required", "Укажите адрес объекта")
    same = matching.same_as(db, data.get("address"), data.get("cadastral"), data.get("owner_phone"), data.get("ad_link"))
    if same:
        m = same[0]
        raise DomainError("object_exists", f"Этот объект уже есть в базе: #{m['property_id']} {m['address'] or ''} "
                          f"({', '.join(m['reasons'])}). Выберите его из базы", 409)
    metro = str(data.get("metro") or "").lstrip("#")
    p = properties.take(db, None, agent, {
        "source_type": "deal", "address": data["address"].strip(), "cadastral_code": data.get("cadastral") or None,
        "source_url": data.get("ad_link") or None, "is_exclusive": data.get("is_exclusive") in (True, "true", "1", 1, "on"),
        "price": data.get("price"), "post_fields": {"metro": metro} if metro in properties.POST_ONE["metro"] else {}},
        commit=False)
    properties._set_owner_contact(db, p, data.get("owner_name"), data.get("owner_phone"), actor or agent)
    log_event(db, "property", p.id, "from_deal", actor or agent)
    return p


def live_deal_for_lead(db: Session, lead: Lead | None) -> Deal | None:
    if lead is None:
        return None
    if lead.deal_id:
        d = db.get(Deal, lead.deal_id)
        if d is not None and d.status != "cancelled":
            return d
    return db.scalar(select(Deal).where(Deal.lead_id == lead.id, Deal.status != "cancelled").order_by(Deal.id).limit(1))


def confirm(db: Session, deal_id: int, actor: User) -> Deal:
    """B6/C9c: operator confirms → exclusive counter/level, lead rented, #Rented to TOPIC_DEAL, T14 + contract jobs."""
    if actor.role not in STAFF:
        raise forbidden("only operators confirm deals")
    deal = get_or_404(db, Deal, int(deal_id), "deal", lock=True)
    if deal.status == "confirmed":
        return deal
    if deal.status != "pending":
        raise DomainError("bad_status", f"deal is {deal.status}")
    p = db.get(Property, deal.property_id) if deal.property_id else None
    if p is not None:
        from app.services import properties
        if properties.needs_check(p):
            raise DomainError("object_unverified", "Новый объект из сделки ещё не проверен: сначала проверьте объект", 409)
    agent = get_or_404(db, User, deal.agent_id, "agent", lock=True) if deal.agent_id and deal.is_exclusive else None
    if agent and deal.is_exclusive:
        agent.exclusive_deals = (agent.exclusive_deals or 0) + 1
        deal.agent_deal_num = agent.exclusive_deals
        new_level = users.level_for(agent.exclusive_deals)
        if new_level > (agent.level or 50):
            agent.level = new_level
            log_event(db, "user", agent.id, "level_up", actor, level=new_level)
    deal.status, deal.confirmed_by, deal.confirmed_at = "confirmed", actor.id, now()
    if deal.lead_id:
        from app.services import leads
        lead = db.get(Lead, deal.lead_id)
        if lead:
            leads.mark_rented(db, lead, actor)
    if deal.property_id:
        from app.services import properties
        properties.mark_rented(db, deal.property_id, actor)
    schedule_reminders(db, deal)
    log_event(db, "deal", deal.id, "confirmed", actor)
    db.commit()
    try:
        notify.deal_confirmed(deal)
    except Exception:  # noqa: BLE001
        log.exception("notify.deal_confirmed failed")
    return deal


def _at_10(d: date) -> datetime:
    return datetime.combine(d, time(10, 0), tzinfo=TZ)


def schedule_reminders(db: Session, deal: Deal, past_t14: bool = True) -> None:
    """T14 survey + contract-expiry jobs. past_t14=False skips an already overdue T14 (bulk import of history)."""
    if deal.t14_date and deal.t14_status == "pending" and (past_t14 or _at_10(deal.t14_date) > now()):
        due = max(_at_10(deal.t14_date), now())
        jobs.schedule(db, "t14_client", deal.id, due)
        jobs.schedule(db, "t14_agent", deal.id, due)
    if deal.rent_to:
        for days in env.contract_remind_days:
            due = _at_10(deal.rent_to - timedelta(days=days))
            if due > now():
                jobs.schedule(db, "contract_expiry", f"{deal.id}:{days}", due, {"days": days})


def cancel(db: Session, deal_id: int, actor: User, reason: str | None = None) -> Deal:
    if actor.role not in STAFF:
        raise forbidden("only operators cancel deals")
    deal = get_or_404(db, Deal, int(deal_id), "deal", lock=True)
    if deal.status == "cancelled":
        return deal
    was_confirmed = deal.status == "confirmed"
    deal.status = "cancelled"
    if was_confirmed and deal.is_exclusive and deal.agent_id:
        agent = db.get(User, deal.agent_id, with_for_update=True, populate_existing=True)
        if agent is not None:
            old = agent.exclusive_deals or 0
            agent.exclusive_deals = max(0, old - 1)
            auto_old, auto_new = users.level_for(old), users.level_for(agent.exclusive_deals)
            if auto_new < auto_old and agent.level == auto_old:  # undo an automatic level-up from this deal
                agent.level = auto_new
                log_event(db, "user", agent.id, "level_down", actor, level=auto_new)
    if deal.lead_id:
        from app.services import leads
        lead = db.get(Lead, deal.lead_id, with_for_update=True, populate_existing=True)
        if lead is not None:
            leads.detach_deal(db, lead, deal, actor, was_confirmed)
    if was_confirmed and deal.property_id:
        from app.services import properties
        properties.unmark_rented(db, deal.property_id, actor)
    for k in ("t14_client", "t14_agent"):
        jobs.cancel(db, k, deal.id)
    jobs.cancel(db, "contract_expiry", ref_prefix=f"{deal.id}:")
    log_event(db, "deal", deal.id, "cancelled", actor, reason=reason)
    db.commit()
    return deal


def _fmt(d: date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else "—"


def _num(v) -> str:
    if v is None:
        return "0"
    f = float(v)
    return f"{f:g}" if f != int(f) else str(int(f))


def rented_text(deal: Deal, db: Session | None = None) -> str:
    """#Rented post (apps-script saveRentalWithT0 text) for TOPIC_DEAL."""
    db = db or object_session(deal)
    e = lambda v: html.escape(str(v)) if v not in (None, "") else "—"  # noqa: E731
    agent = db.get(User, deal.agent_id) if (db and deal.agent_id) else None
    recruit = db.get(User, deal.recruit_id) if (db and deal.recruit_id) else None
    t0 = db.scalar(select(Evaluation).where(Evaluation.deal_id == deal.id, Evaluation.kind == "t0")) if db else None
    if deal.is_rerented:
        return (f"🔄 <b>#Rerented | Повторная сдача</b>\n👤 #{e(agent.name if agent else '?')}\nLink: {e(deal.ad_link)}\n"
                f"Цена: {_num(deal.price)}$ | Комиссия: <b>0$</b>\n\nClient: {e(deal.client_name)} {deal.client_phone or ''}\n"
                f"Owner: {e(deal.owner_name)} {deal.owner_phone or ''}\nRent: {_fmt(deal.rent_from)} – {_fmt(deal.rent_to)}")
    lvl = deal.agent_level or 50
    comm = Decimal(deal.commission_total or 0)
    plat_pct = round(float(Decimal(deal.platform_amount or 0) / comm * 100)) if comm else 100 - lvl
    lines = [f"💰 <b>#Rented{f' | Сделка #{deal.agent_deal_num}' if deal.is_exclusive and deal.agent_deal_num else ''}</b>",
             f"{'#Эксклюзив' if deal.is_exclusive else '#Неэксклюзив'} | {'Продажа' if deal.deal_type == 'sale' else 'Аренда'}"
             f" | Agent {lvl}% | 👤 #{e(agent.name if agent else '?')}",
             f"Link: {e(deal.ad_link)}", f"Cadastral: <code>{e(deal.cadastral)}</code>", "",
             f"<b>Цена: {_num(deal.price)}$</b>", f"Комиссия: {_num(deal.commission_total)}$ ({_num(deal.commission_pct)}%)", "",
             "📊 Распределение:", f"• Платформа ({plat_pct}%): {_num(deal.platform_amount)}$",
             f"• Агент/Наставник ({lvl}%): {_num(deal.agent_amount)}$"]
    if recruit or (deal.recruit_amount and float(deal.recruit_amount) > 0):
        lines.append(f"• #{e(recruit.name if recruit else '')} ({_num(deal.recruit_pct)}%): {_num(deal.recruit_amount)}$")
    if deal.client_src:
        lines.append(f"• Передал клиента: {e(deal.client_src)} (10%): {_num(deal.client_src_amount)}$")
    lines += ["", f"Client: {e(deal.client_name)} {deal.client_phone or ''}", f"Owner: {e(deal.owner_name)} {deal.owner_phone or ''}",
              f"Rent: {_fmt(deal.rent_from)} – {_fmt(deal.rent_to)}"]
    if recruit:
        lines.append(f"Рекрут: #{e(recruit.name)}")
    lines += ["", f"⏰ T14: <b>{_fmt(deal.t14_date)}</b>"]
    if t0:
        lines += ["", f"🔍 T0 | H2O: {t0.h2o_client}/{t0.h2o_owner}/{t0.h2o_process}",
                  "✅ Чистая" if t0.result == "clean" else "🔴 Грязная" if t0.result == "dirty" else "—"]
        if t0.comment:
            lines.append(f"💬 {e(t0.comment)}")
    return "\n".join(lines)


def out(deal: Deal, user: User | None = None) -> dict:
    d = {}
    for c in Deal.__table__.columns.keys():
        v = getattr(deal, c)
        if isinstance(v, Decimal):
            v = float(v)
        elif isinstance(v, (date, datetime)):
            v = v.isoformat()
        d[c] = v
    if user is not None and user.role not in STAFF and deal.agent_id != user.id and deal.recruit_id != user.id:
        for k in ("client_phone", "client_tg", "owner_phone", "client_birth", "owner_birth"):
            d[k] = None
    return d


def query(db: Session, user: User, month: str | None = None, agent_id: int | None = None, status: str | None = None):
    stmt = select(Deal)
    if user.role not in STAFF:
        stmt = stmt.where((Deal.agent_id == user.id) | (Deal.recruit_id == user.id))
    elif agent_id:
        stmt = stmt.where(Deal.agent_id == agent_id)
    if month:
        stmt = stmt.where(Deal.month == month)
    if status:
        stmt = stmt.where(Deal.status.in_(status.split(",")))
    return stmt.order_by(Deal.created_at.desc())


def expiring(db: Session, user: User, days: int = 30) -> list[Deal]:
    today = now().astimezone(TZ).date()
    stmt = query(db, user, status="confirmed").where(Deal.rent_to.isnot(None), Deal.rent_to >= today,
                                                      Deal.rent_to <= today + timedelta(days=days))
    return list(db.scalars(stmt.order_by(None).order_by(Deal.rent_to)))


def find(db: Session, cadastral: str) -> list[Deal]:
    c = (cadastral or "").strip()
    if not c:
        return []
    return list(db.scalars(select(Deal).where(Deal.cadastral.ilike(f"%{c}%")).order_by(Deal.created_at.desc()).limit(50)))
