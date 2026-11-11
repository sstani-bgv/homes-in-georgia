"""Deals (#Rented), commission by level (apps-script saveRentalWithT0), T14 and contract reminders."""
import html
import logging
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from app.config import settings as env
from app.models import FIELD, STAFF, Client, Deal, Evaluation, Lead, Owner, Property, User
from app.services import evaluations, jobs, leads, users
from app.services.common import TZ, clean_cadastral, get_or_404, log_event, now
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


AXES = ("make", "participate", "transfer", "create", "external")
# participate option → (agent's share %, the other side's role) — old CRM MOLECULA_PARTICIPATE
PARTICIPATE = {
    "object_recruit_joint": (20, "Рекрут / участник"), "object_recruit_sign": (10, "Рекрут / участник"),
    "object_agent_joint": (20, "Участник"), "object_agent_sign": (10, "Участник"),
    "client_joint_showing": (20, "Источник клиента"), "client_transfer": (10, "Источник клиента"),
    "client_external": (30, "Внешний источник"), "take_exclusive": (40, "Куратор цикла"),
}


def scenario_rows(sc: dict, level: int = 50, is_exclusive: bool = True) -> list[dict]:
    """Old CRM moleculeRows. `who`: agent = the deal's agent, partner = the second participant (recruit_id), platform = the
    remainder. Port of frontend/src/pages/Calc.tsx moleculeRows."""
    axis, opt, role = sc.get("axis"), sc.get("opt") or "object_recruit_joint", sc.get("role") or ""
    lev = 70 if int(sc.get("lev") or 50) == 70 else 50
    n = max(0, int(sc.get("n") or 0))
    row = lambda who, r, pct: {"who": who, "role": r, "pct": pct}  # noqa: E731
    if axis == "make":  # the personal ladder; non-exclusive is always 50 (old CRM `excl ? lvl : 50`)
        return [row("agent", "Агент · собственный цикл", level if is_exclusive else 50)]
    if axis == "participate":
        if opt not in PARTICIPATE:
            raise DomainError("bad_scenario", f"unknown participation: {opt}", 422)
        share, other = PARTICIPATE[opt]
        return [row("agent", "Участник сделки", share), row("partner", other if opt.startswith("client") else "Владелец объекта / агент", 50)]
    if axis == "transfer":
        if role == "stream":
            share = 15 if lev == 70 else 25
            return [row("partner", "Передающий поток", share), row("agent", "Агент полного цикла", lev)]
        if role == "keeper":
            return [row("agent", "Хранитель потока", min(5, max(1, n // 100 or 1)))]
        return [row("agent", "Наставник · полный цикл", 50)]
    if axis == "create":
        if role == "after":
            return [row("partner", "Создатель направления", 15 if lev == 70 else 25), row("agent", "Агент полного цикла", lev)]
        return [row("agent", "Инициатор теста", 50)]
    if axis == "external":
        return [row("agent", "Внешний агент", 50), row("partner", "Внутренний куратор", 25)]
    raise DomainError("bad_scenario", f"unknown axis: {axis}", 422)


def people_rule(sc: dict | None) -> str:
    """Old CRM moleculePeopleRule: which participants a scenario needs: agent | either (agent or recruit) | both."""
    axis, opt = (sc or {}).get("axis"), (sc or {}).get("opt") or "object_recruit_joint"
    if axis == "participate":
        return "both" if opt.startswith("object_") else "either" if opt.startswith("client_") else "agent"
    return "both" if axis == "transfer" else "agent"


def calc(price=None, commission_pct=None, commission_total=None, platform_amount=None, agent_level=50,
         recruit_pct=None, recruit_amount=None, client_src=None, is_rerented=False, is_exclusive=True,
         scenario=None, split=None, **_ignored) -> dict:
    """comm = commission_total or round(price*pct/100); the agent's level share applies only to an exclusive object (a
    non-exclusive one pays 50, as in the old CRM). platform = given or comm*(100-share)/100;
    recruit = given or comm*recruit_pct/100; client_src (who passed the client) = 10% of comm;
    agent = comm - platform - recruit - client_src.
    `scenario` (Molecula axis) or `split` (staff's manual rows [{who, role, pct}], sum 100) replace that scheme:
    every row gets pct% of comm, the platform gets the remainder. `distribution` lists the rows with amounts."""
    price_d = _d(price) or Decimal(0)
    pct = _d(commission_pct)
    pct = Decimal(50) if pct is None else pct
    level = int(agent_level or 50)
    share = level if is_exclusive else 50
    comm = Decimal(0) if is_rerented else _d(commission_total) or _round(price_d * pct / 100)
    rows = None
    if not is_rerented and split:
        rows = [{"who": r.get("who") or "other", "role": str(r.get("role") or "")[:80], "pct": float(r.get("pct") or 0)} for r in split]
        if abs(sum(r["pct"] for r in rows) - 100) > 0.01:
            raise DomainError("bad_split", "Распределение должно давать 100%", 422)
    elif not is_rerented and scenario:
        rows = scenario_rows(scenario, level, is_exclusive)
        rest = 100 - sum(r["pct"] for r in rows)
        if rest < 0:
            raise DomainError("bad_scenario", "Доли сценария превышают 100%", 422)
        rows.append({"who": "platform", "role": "Платформа", "pct": rest})
    if rows is not None:
        amounts = [_round(comm * Decimal(str(r["pct"])) / 100) for r in rows]
        pi = next((i for i, r in enumerate(rows) if r["who"] == "platform"), None)
        if pi is not None:  # the platform takes the rounding remainder: the rows always add up to the commission
            amounts[pi] = comm - sum(a for i, a in enumerate(amounts) if i != pi)
        sum_of = lambda who: sum((a for r, a in zip(rows, amounts) if r["who"] == who), Decimal(0))  # noqa: E731
        plat, agent, rec, src = sum_of("platform"), sum_of("agent"), sum_of("partner"), Decimal(0)
        rpct = Decimal(str(sum(r["pct"] for r in rows if r["who"] == "partner")))
        dist = [{**r, "amount": _r(a)} for r, a in zip(rows, amounts)]
        return {"price": _r(price_d), "commission_pct": _r(pct), "commission_total": _r(comm), "platform_amount": _r(plat),
                "platform_pct": round(float(plat / comm * 100)) if comm else 100 - share, "agent_level": level,
                "recruit_pct": _r(rpct), "recruit_amount": _r(rec), "client_src_amount": 0.0, "agent_amount": _r(agent),
                "distribution": dist}
    plat = _d(platform_amount)
    if plat is None:
        plat = comm * (100 - share) / 100
    rpct = _d(recruit_pct) or Decimal(0)
    rec = _d(recruit_amount)
    if rec is None:
        rec = comm * rpct / 100
    src = _round(comm * Decimal("0.1")) if client_src else Decimal(0)
    agent = comm - plat - rec - src
    p = lambda a: round(float(a / comm * 100), 2) if comm else 0  # noqa: E731
    dist = [{"who": "platform", "role": "Платформа", "pct": p(plat), "amount": _r(plat)},
            {"who": "agent", "role": "Агент", "pct": p(agent), "amount": _r(agent)}]
    if rec:
        dist.append({"who": "partner", "role": "Рекрут", "pct": p(rec), "amount": _r(rec)})
    if src:
        dist.append({"who": "other", "role": "Источник клиента", "pct": p(src), "amount": _r(src)})
    return {"price": _r(price_d), "commission_pct": _r(pct), "commission_total": _r(comm), "platform_amount": _r(plat),
            "platform_pct": round(float(plat / comm * 100)) if comm else 100 - share, "agent_level": level,
            "recruit_pct": _r(rpct), "recruit_amount": _r(rec), "client_src_amount": _r(src), "agent_amount": _r(agent),
            "distribution": dist}


def default_commission_pct(deal_type: str | None, sub_type: str | None) -> int:
    """The fixed commission of an agent's deal, % of the price (a month's rent): sale 3, commercial 100, else 50 (the deal
    form's defaults). Only staff set another percent or a manual amount."""
    return 3 if deal_type == "sale" else 100 if sub_type == "commercial" else 50


def _month(d: date | None) -> str:
    d = d or now().astimezone(TZ).date()
    return f"{d.month}/{d.year}"


def create(db: Session, data: dict, actor: User | None, commit: bool = True, notify_lead: bool = True) -> Deal:
    """C9b deal form → deals.status=pending. Optional T0 (`t0_h2o_client`.. or `t0: {...}`) stored as evaluation."""
    data = dict(data or {})
    if oid := data.pop("owner_id", None):  # picked from the owners list (autocomplete): the base's contact, not a retype
        o = get_or_404(db, Owner, int(oid), "owner")
        data["owner_name"], data["owner_phone"] = o.name or data.get("owner_name"), o.phone or data.get("owner_phone")
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
        # «Договор» → «Сделка» (Stanislav 11-11): the deal form opens once the lead's contract is signed
        signed = leads.signed_contract(db, lead) is not None
        if actor is not None and lead.status == "contract" and not signed:
            raise DomainError("contract_not_signed", "Сделку оформляют после подписания договора", 409)
        if not staff and lead.status != "deal_pending" and not (signed and lead.status in leads.DEAL_STAGES):
            raise DomainError("bad_status", "deal is filled after the contract is signed (lead stage «Договор»)")
        client = db.get(Client, lead.client_id)
        if client and (staff or lead.contacts_shared_at):
            data.setdefault("client_name", client.name)
            data.setdefault("client_phone", client.phone)
            data.setdefault("client_tg", f"@{client.username}" if client.username else None)
    from app.services import properties
    if actor is not None and (cad := (data.get("cadastral") or "").strip()):  # the form's code; the object's own one stays
        known = db.get(Property, int(data["property_id"])) if data.get("property_id") else None
        if cad != ((known.cadastral_code if known else None) or "").strip():
            data["cadastral"] = clean_cadastral(cad)
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
            # what the object lacks (channel posts carry no owner or cadastral code) is kept from the deal form
            if not p.owner_id and (actor or agent) and (data.get("owner_name") or data.get("owner_phone")):
                properties._set_owner_contact(db, p, data.get("owner_name"), data.get("owner_phone"), actor or agent)
            if not p.cadastral_code and (data.get("cadastral") or "").strip():
                p.cadastral_code = data["cadastral"].strip()
            data.update(properties.deal_fields(db, p, actor, data.get("deal_type") or "rent"))
    if actor is not None and actor.role not in STAFF:  # payout inputs an agent must not set for themselves
        data.pop("split", None)
        data.pop("agent_level", None)
        data.pop("platform_amount", None)
        data.pop("commission_total", None)
        data["commission_pct"] = default_commission_pct(data.get("deal_type"), data.get("sub_type"))
    level = int(data.get("agent_level") or users.effective_level(agent))
    sc = data.get("scenario") or None
    if sc:
        sc = {k: sc.get(k) for k in ("axis", "opt", "role", "lev", "n") if sc.get(k) not in (None, "")}
        mode, has_a, has_r = people_rule(sc), bool(agent), bool(data.get("recruit_id"))
        if (mode == "both" and not (has_a and has_r)) or (mode == "either" and not (has_a or has_r)) or not has_a and mode == "agent":
            raise DomainError("people_required", {"both": "Нужны агент и рекрут", "either": "Укажите агента или рекрута"}.get(mode, "Укажите агента"), 422)
    nums = calc(price=data.get("price"), commission_pct=data.get("commission_pct"),
                commission_total=data.get("commission_total"), platform_amount=data.get("platform_amount"),
                agent_level=level, recruit_pct=data.get("recruit_pct"), recruit_amount=data.get("recruit_amount"),
                client_src=data.get("client_src"), is_rerented=bool(data.get("is_rerented")),
                is_exclusive=data.get("is_exclusive") in (True, "true", "1", 1, "on", "yes"), scenario=sc, split=data.get("split"))
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
    deal.scenario, deal.distribution = sc, nums["distribution"]
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
        leads.attach_deal(db, lead, deal.id, actor)
    log_event(db, "deal", deal.id, "created", actor, price=nums["price"], commission=nums["commission_total"])
    if commit:
        db.commit()
        if t0_ev is not None:
            evaluations.notify_if_alarming(t0_ev)
    return deal


def _num_or_none(v) -> float | None:
    try:
        return float(str(v).replace(" ", "").replace(",", ".")) if str(v or "").strip() else None
    except ValueError:
        return None


def prefill_from_contract(kind: str | None, data: dict | None) -> dict:
    """The signed contract's wizard data (CxData keys) → deal form fields, so nothing is typed twice. Terms that the
    deal has no column for (deposit, pay day, term) come back under `terms` for the deal page to show."""
    d, sale = data or {}, kind == "sale"
    out = {"deal_type": "sale" if sale else "rent", "sub_type": None if sale else "commercial" if kind == "comm" else "long",
           "price": _num_or_none(d.get("sale_price" if sale else "price_usd")),
           "address": d.get("street_house"), "cadastral": d.get("cadastral_code"),
           "client_name": d.get("client_full_name"), "owner_name": d.get("owner_full_name"),
           "client_birth": d.get("client_birth_day"), "owner_birth": d.get("owner_birth_day"),
           "rent_from": None if sale else d.get("start_rent"), "rent_to": None if sale else d.get("end_rent")}
    out = {k: v.strip() if isinstance(v, str) else v for k, v in out.items()}
    terms = {k: d.get(k) for k in ("deposit_usd", "pay_day", "rent_duration_months", "transfer_date", "pet_deposit", "timestamp")}
    return {**{k: v for k, v in out.items() if v not in (None, "")}, "terms": {k: v for k, v in terms.items() if v not in (None, "")}}


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
    """B6/C9c: operator confirms → exclusive counter/level, lead rented, T14 + contract jobs."""
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
    from app.services import contracts
    contracts.mark_deal(db, deal, actor)
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
    dist = deal.distribution or []
    ag = next((r for r in dist if r["who"] == "agent"), None)
    lines = [f"💰 <b>#Rented{f' | Сделка #{deal.agent_deal_num}' if deal.is_exclusive and deal.agent_deal_num else ''}</b>",
             f"{'#Эксклюзив' if deal.is_exclusive else '#Неэксклюзив'} | {'Продажа' if deal.deal_type == 'sale' else 'Аренда'}"
             f" | Agent {_num(ag['pct']) if ag else lvl}% | 👤 #{e(agent.name if agent else '?')}",
             f"Link: {e(deal.ad_link)}", f"Cadastral: <code>{e(deal.cadastral)}</code>", "",
             f"<b>Цена: {_num(deal.price)}$</b>", f"Комиссия: {_num(deal.commission_total)}$ ({_num(deal.commission_pct)}%)", ""]
    if dist:  # the saved split rows (scenario or classic), the platform first
        lines.append("📊 Распределение:")
        who = lambda r: (f" #{recruit.name}" if r["who"] == "partner" and recruit else f" {deal.client_src}" if r["role"] == "Источник клиента" and deal.client_src else "")  # noqa: E731
        lines += [f"• {e(('Агент/Наставник' if r['role'] == 'Агент' else r['role']) + who(r))} ({_num(r['pct'])}%): {_num(r['amount'])}$"
                  for r in sorted(dist, key=lambda r: r["who"] != "platform")]
    else:
        lines += ["📊 Распределение:", f"• Платформа ({plat_pct}%): {_num(deal.platform_amount)}$",
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
