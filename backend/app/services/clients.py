"""Stage 8: the full client card. What is happening now (status), one timeline of everything (questionnaire, searches,
shown cards, reactions, leads with every lead event, deals) and the refusals: who, why, at what stage."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FIELD, Client, ClientView, Deal, Event, Lead, Listing, Reaction, Search, User
from app.services.common import aware, jload
from app.telegram import texts

LEAD_STATUS = {"new": "Новая", "operator_taken": "У оператора", "agent_assigned": "Назначен агент", "slots_sent": "Время отправлено",
               "time_chosen": "Просмотр назначен", "viewing_done": "Просмотр прошёл", "thinking": "Думает",
               "contract": "Договор", "deal_pending": "Оформление сделки", "rented": "Сдано", "refused": "Отказ"}
REASON = {k: v["ru"] for k, v in texts.REFUSAL.items()} | {"cancelled": "Отменена оператором"}
OUTCOME = {"rented": "снимает", "refused": "отказ", "thinking": "думает", "reschedule": "перенос"}
LABEL = {"client": "Клиент", "bot": "Бот", "operator": "Оператор", "agent": "Агент", "system": "Система"}
# lead event → (timeline type, text); outcome_* are decided by their outcome
LEAD_EVENT = {
    "created": ("lead", "Заявка создана"), "reopened": ("lead", "Заявка снова открыта"),
    "operator_taken": ("lead", "Оператор взял заявку"), "agent_assigned": ("lead", "Назначен агент"),
    "message_to_client": ("message", "Сообщение клиенту"), "message_from_client": ("message", "Сообщение от клиента"),
    "slots_sent": ("viewing", "Клиенту отправлено время просмотра"), "time_chosen": ("viewing", "Клиент выбрал время"),
    "client_own_time": ("viewing", "Клиент предложил своё время"), "reschedule": ("viewing", "Перенос просмотра"),
    "client_late": ("viewing", "Клиент опаздывает"), "contacts_shared": ("viewing", "Контакты переданы агенту"),
    "viewing_done": ("viewing", "Просмотр прошёл"), "cancelled": ("refusal", "Заявка отменена"),
    "sla_overdue": ("lead", "Срок шага истёк"),
}
LEAD_SKIP = ("deal_created", "deal_cancelled", "rented")  # the deal's own events say it
DEAL_EVENT = {"created": "Заполнена форма сделки", "confirmed": "Сделка подтверждена · сдано", "cancelled": "Сделка отменена",
              "telegram_parsed": "Сделка из Telegram"}


def _at(v) -> datetime | None:
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v)
        except ValueError:
            return None
    return v and aware(v).astimezone(timezone.utc)


def _beds(n):
    return None if n is None else "студия" if n == 0 else f"{n} сп."


def obj_label(l: Listing | None) -> str:
    """Ваке · 2 сп. · $850"""
    if not l:
        return "объект снят"
    return " · ".join(x for x in (l.district, _beds(l.bedrooms), l.price_usd and texts.money(l.price_usd)) if x)


def agent_label(u: User | None) -> str:
    return "—" if not u else f"#{u.agent_tag}" if u.agent_tag else u.name


def _item(at, kind, text, actor=None, action=None, **links):
    return {"at": _at(at), "type": kind, "action": action, "actor": actor, "text": text,
            "links": {k: v for k, v in links.items() if v is not None}}


def _lead_text(e: Event, users: dict) -> tuple[str, str]:
    d = e.data or {}
    if e.action.startswith("outcome_"):
        out = d.get("outcome")
        who = "клиента" if e.action == "outcome_client" else "агента"
        reason = REASON.get(d.get("reason"), d.get("reason"))
        return ("refusal" if out == "refused" else "viewing",
                f"Итог {who}: {OUTCOME.get(out, out)}" + (f" · {reason}" if reason else ""))
    kind, text = LEAD_EVENT.get(e.action, ("lead", e.action))
    if e.action == "agent_assigned" and d.get("agent_id"):
        text += f": {agent_label(users.get(d['agent_id']))}"
    elif e.action in ("time_chosen", "cancelled", "message_to_client", "message_from_client", "client_own_time") and d.get("note"):
        text += f": {d['note']}"
    if e.action.startswith("message_") and d.get("via") == "dm":
        text += " (личка оператора)"
    return kind, text


def card(db: Session, c: Client) -> dict:
    """{status, timeline (newest first), refusals} for /clients/{id}."""
    leads = list(db.scalars(select(Lead).where(Lead.client_id == c.id).order_by(Lead.created_ts)))
    lead_ids = [x.id for x in leads]
    deals = list(db.scalars(select(Deal).where(Deal.lead_id.in_(lead_ids)))) if lead_ids else []
    views = list(db.scalars(select(ClientView).where(ClientView.client_id == c.id)))
    reactions = list(db.scalars(select(Reaction).where(Reaction.client_id == c.id)))
    lids = {x.listing_id for x in (*views, *reactions, *leads) if x.listing_id}
    listings = {x.id: x for x in db.scalars(select(Listing).where(Listing.id.in_(lids)))} if lids else {}
    lead_of = {x.id: x for x in leads}
    ev = list(db.scalars(select(Event).where(
        ((Event.entity == "client") & (Event.entity_id == str(c.id)))
        | ((Event.entity == "lead") & Event.entity_id.in_([str(i) for i in lead_ids]))
        | ((Event.entity == "deal") & Event.entity_id.in_([str(d.id) for d in deals]))).order_by(Event.at, Event.id)))
    uids = {e.actor_id for e in ev} | {(e.data or {}).get("agent_id") for e in ev} | {x.agent_id for x in leads}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_({i for i in uids if i})))}

    def actor(e: Event) -> str:
        u = users.get(e.actor_id)
        return (u and (agent_label(u) if u.role in FIELD else u.name)) or LABEL.get(e.actor_label, e.actor_label)

    items = [_item(c.created_at, "profile", "Первый контакт с ботом", "Клиент", "first_contact")]
    for s in db.scalars(select(Search).where(Search.client_id == c.id)):
        items.append(_item(s.at, "search", f"Поиск: показано вариантов {s.results}", "Бот", "search"))
    for v in views:
        what = "Бот отправил объект (собственник согласен показать)" if v.source == "push" else "Показан объект"
        items.append(_item(v.shown_at, "shown", f"{what}: {obj_label(listings.get(v.listing_id))}", "Бот", "push" if v.source == "push" else "shown",
                           listing_id=v.listing_id, property_id=v.property_id))
    for r in reactions:
        items.append(_item(r.created_at, "reaction", f"{'❤️ Нравится' if r.reaction == 'like' else '👎 Не то'}: "
                           f"{obj_label(listings.get(r.listing_id))}", "Клиент", r.reaction, listing_id=r.listing_id))
    deal_lead = {d.id: d.lead_id for d in deals}
    for e in ev:
        if e.entity == "client":
            prof = texts.profile_summary((e.data or {}).get("profile") or {}, "ru", one_line=True)
            items.append(_item(e.at, "profile", "Анкета сохранена" + (f": {prof}" if prof else ""), actor(e), e.action))
        elif e.entity == "deal":
            if e.action in DEAL_EVENT:
                did = int(e.entity_id)
                items.append(_item(e.at, "deal", f"{DEAL_EVENT[e.action]} #{did}", actor(e), e.action, deal_id=did,
                                   lead_id=deal_lead.get(did)))
        elif e.action not in LEAD_SKIP:
            lead = lead_of[int(e.entity_id)]
            kind, text = _lead_text(e, users)
            if e.action in ("created", "reopened"):
                text += f": {obj_label(listings.get(lead.listing_id))}"
            items.append(_item(e.at, kind, f"#{lead.id} · {text}", actor(e), e.action, lead_id=lead.id,
                               listing_id=lead.listing_id, property_id=lead.property_id))
    far = datetime.min.replace(tzinfo=timezone.utc)
    items = [x for _, x in sorted(enumerate(items), key=lambda t: (t[1]["at"] or far, t[0]), reverse=True)]
    return {"status": status_line(leads, users, deals, listings), "timeline": items,
            "refusals": refusals(leads, ev, users, listings)}


def refusals(leads: list[Lead], ev: list[Event], users: dict, listings: dict) -> list[dict]:
    """Every refusal of a lead: who (client / agent / operator), why, at what stage (the lead status before it)."""
    out, seen = [], set()
    for lead in leads:
        prev = "new"
        for e in (e for e in ev if e.entity == "lead" and e.entity_id == str(lead.id)):
            d = e.data or {}
            refused = e.action == "cancelled" or (e.action.startswith("outcome_") and d.get("outcome") == "refused")
            if refused:
                u = users.get(e.actor_id)
                by = "operator" if e.action == "cancelled" else e.action.removeprefix("outcome_")
                reason = d.get("reason") or d.get("note") or (lead.refusal_reason if lead.status == "refused" else None)
                out.append({"lead_id": lead.id, "listing_id": lead.listing_id, "property_id": lead.property_id,
                            "object": obj_label(listings.get(lead.listing_id)), "at": _at(e.at), "by": by,
                            "by_name": (u and (agent_label(u) if u.role in FIELD else u.name))
                            or (agent_label(users.get(lead.agent_id)) if by == "agent" else LABEL[by]),
                            "reason": REASON.get(reason, reason), "note": lead.client_note if reason == "other" else None,
                            "stage": prev})
                seen.add(lead.id)
            prev = d.get("status") or prev
        if lead.status == "refused" and lead.id not in seen:  # no event (imported / old lead): what the row says
            by = ("operator" if lead.refusal_reason == "cancelled" else "agent" if lead.agent_outcome == "refused" else "client")
            out.append({"lead_id": lead.id, "listing_id": lead.listing_id, "property_id": lead.property_id,
                        "object": obj_label(listings.get(lead.listing_id)), "at": _at(lead.updated_at), "by": by,
                        "by_name": agent_label(users.get(lead.agent_id)) if by == "agent" else LABEL[by],
                        "reason": REASON.get(lead.refusal_reason, lead.refusal_reason), "note": lead.client_note, "stage": None})
    out.sort(key=lambda x: x["at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out


def status_line(leads: list[Lead], users: dict, deals: list[Deal], listings: dict) -> str:
    """«Сейчас: ждёт просмотра 12.11 в 18:00 с агентом #Zaza» from the latest live lead (else the latest one)."""
    if not leads:
        return "Сейчас: смотрит подборку, заявок нет"
    live = [x for x in leads if x.status not in ("rented", "refused")]
    lead = max(live or leads, key=lambda x: (_at(x.updated_at) or _at(x.created_ts), x.id))
    ag = agent_label(users.get(lead.agent_id)) if lead.agent_id else None
    when = lead.viewing_at and texts.local(lead.viewing_at).strftime("%d.%m в %H:%M")
    deal = next((d for d in deals if d.lead_id == lead.id and d.status != "cancelled"), None)
    s = {"new": "заявка ждёт оператора", "operator_taken": "оператор подбирает агента",
         "agent_assigned": f"агент {ag} договаривается с собственником", "slots_sent": "выбирает время просмотра",
         "time_chosen": f"ждёт просмотра {when} с агентом {ag}", "viewing_done": f"просмотр с агентом {ag} прошёл, ждём итог",
         "thinking": "думает после просмотра", "contract": "готовится договор аренды", "deal_pending": f"оформляется сделка{f' #{deal.id}' if deal else ''}",
         "rented": f"снял квартиру{f' (сделка #{deal.id})' if deal else ''}",
         "refused": f"отказ: {REASON.get(lead.refusal_reason, lead.refusal_reason or 'без причины')}"}.get(lead.status, lead.status)
    obj = f"объект #{lead.property_id}, " if lead.property_id else ""
    return f"Сейчас: {s} · заявка #{lead.id}: {obj}{obj_label(listings.get(lead.listing_id))}"
