"""Stage 10: demand for an object before it is verified. «Под этот объект подходят N активных клиентов» from the clients'
questionnaires; «Собственник согласен показать клиентам» fast-tracks the object (client-eligible with the ad's photos:
leads.client_listing_eligible / bot CLIENT_ELIGIBLE) and sends it once to the clients it fits at that moment."""
import math
import re
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import districts
from app.models import STAFF, Client, ClientView, Deal, Lead, Listing, Property, Reaction, User
from app.services.common import get_or_404, jload, log_event, now
from app.services.errors import DomainError, forbidden
from app.telegram import notify, texts

ACTIVE_DAYS = 30  # a client who wrote to the bot / tapped something within this many days
PUSH_CAP = 10  # clients one «Собственник согласен» sends the object to
TERM_MONTHS = {"1-3": 3, "6": 6, "12": 12, "12+": 12}  # questionnaire terms → the longest stay they mean
NO_PETS = {"нет", "NotAllowed"}
FAST_TRACK_FROM = ("taken", "call_failed", "owner_confirmed", "shoot_scheduled", "media_uploaded", "review", "returned")


# ---------------------------------------------------------------- the match itself (pure)

def _months(period: str) -> int:
    """post_fields periods: 1Day → 0, 6Month → 6."""
    m = re.match(r"(\d+)Month", period or "")
    return int(m[1]) if m else 0


def fits(profile: dict, o: dict) -> bool:
    """Does the object o fit this questionnaire? Same hard conditions as the bot's search (core.hard_filter): budget +10%,
    bedrooms (3 = 3+), districts or the map point (radius ×1.5), pets, plus the term against the object's rent periods.
    o: {price_usd, bedrooms, district, lat, lng, pets_allowed: bool | None, periods: [...] | None}. Unknown values pass,
    except place: a wanted district / point can't be checked without the object's district / coordinates."""
    p = profile or {}
    price, beds = o.get("price_usd"), o.get("bedrooms")
    if p.get("budget_max") and price and float(price) > p["budget_max"] * 1.1:
        return False
    want = {int(b) for b in p.get("bedrooms") or []}
    if want and beds is not None and beds not in want and not (beds >= 3 and 3 in want):
        return False
    if pin := p.get("pin"):
        if o.get("lat") is None or o.get("lng") is None:
            return False
        km = 111 * math.hypot(o["lat"] - pin["lat"], (o["lng"] - pin["lng"]) * 0.75)
        if km > (p.get("radius_km") or 2.0) * 1.5:
            return False
    elif ds := [d.lower() for d in p.get("districts") or [] if d != "any"]:
        d, t = (o.get("district") or "").lower(), districts.tag(o.get("district"))
        if not d or not any(w in d or d in w or (t and districts.tag(w) == t) for w in ds):  # «Вере» = «Вера» = Vera
            return False
    if any(x != "none" for x in p.get("pets") or []) and o.get("pets_allowed") is False:
        return False
    terms = [TERM_MONTHS[t] for t in p.get("terms") or [] if t in TERM_MONTHS]
    periods = [_months(x) for x in o.get("periods") or []]
    if terms and periods and min(periods) > max(terms):  # the owner wants a longer contract than the client stays
        return False
    return True


def listing_facts(l: Listing) -> dict:
    return {"price_usd": l.price_usd, "bedrooms": l.bedrooms, "district": l.district, "lat": l.lat, "lng": l.lng,
            "pets_allowed": None if l.pets in (None, "", "не указано", "по договорённости") else l.pets not in NO_PETS,
            "periods": None}


def property_facts(p: Property, l: Listing | None) -> dict:
    """The object's own fields (agent / operator edited) over its ad's."""
    o = listing_facts(l) if l else {"lat": None, "lng": None, "pets_allowed": None}
    pf = p.post_fields or {}
    usd = p.price is not None and (p.currency or "USD") == "USD"
    o.update(price_usd=float(p.price) if usd else o.get("price_usd"), bedrooms=p.bedrooms if p.bedrooms is not None else o.get("bedrooms"),
             district=p.district or o.get("district"), periods=pf.get("periods") or None)
    if pf.get("pets"):
        o["pets_allowed"] = None if pf["pets"] == "ByAgreement" else pf["pets"] not in NO_PETS
    return o


# ---------------------------------------------------------------- who is active

def active_clients(db: Session) -> list[Client]:
    """A finished questionnaire, something in the bot within ACTIVE_DAYS, no flat rented through us yet."""
    since = (now() - timedelta(days=ACTIVE_DAYS)).isoformat(timespec="seconds")
    done = select(Lead.client_id).outerjoin(Deal, Deal.lead_id == Lead.id).where(
        or_(Lead.status == "rented", Deal.status == "confirmed"))
    rows = db.scalars(select(Client).where(Client.profile_done == 1, Client.updated_at >= since, Client.id.not_in(done))
                      .order_by(Client.updated_at.desc(), Client.id.desc()))
    return [c for c in rows if jload(c.profile, {})]


def matched(clients: list[Client], o: dict) -> list[Client]:
    return [c for c in clients if fits(jload(c.profile, {}), o)]


def listing_counts(db: Session, listings: list[Listing]) -> dict[str, int]:
    """«Новые объявления»: listing id → active clients it fits."""
    if not listings:
        return {}
    clients = active_clients(db)
    return {l.id: len(matched(clients, listing_facts(l))) for l in listings}


def _person(c: Client, staff: bool) -> dict:
    """Staff see who; an agent sees only what the client wants (his argument for the owner, not a contact list)."""
    d = {"profile": texts.profile_summary(jload(c.profile, {}), "ru", one_line=True), "lang": c.lang}
    if staff:
        d.update(id=c.id, name=c.name, username=c.username)
    return d


def for_property(db: Session, p: Property, user: User) -> dict:
    l = db.get(Listing, p.listing_id) if p.listing_id else None
    clients = matched(active_clients(db), property_facts(p, l))
    sent = set(db.scalars(select(ClientView.client_id).where(ClientView.listing_id == p.listing_id,
                                                             ClientView.source == "push"))) if l else set()
    staff = user.role in STAFF
    return {"count": len(clients), "active_days": ACTIVE_DAYS, "fast_track_at": p.fast_track_at, "pushed": len(sent),
            "can_fast_track": _fast_track_error(p, l) is None and (staff or p.agent_id == user.id),
            "clients": [{**_person(c, staff), "sent": c.id in sent} for c in clients]}


# ---------------------------------------------------------------- owner first: fast track + push

def _fast_track_error(p: Property, l: Listing | None) -> str | None:
    if p.fast_track_at is not None:
        return "already"
    if l is None:
        return "no_listing"
    if l.status != "published":
        return "listing_gone"
    if p.status not in FAST_TRACK_FROM:
        return "bad_status"
    return None


def fast_track(db: Session, pid: int, actor: User) -> tuple[Property, list[Client]]:
    """«Собственник согласен показать клиентам» (after the call): the object becomes client-eligible with the ad's photos
    and goes to the clients it fits now. Explicit, so it is sent even with auto_push_variants=0."""
    p = get_or_404(db, Property, int(pid), "property", lock=True)
    if actor.role not in STAFF and p.agent_id != actor.id:
        raise forbidden("object is taken by another agent")
    l = db.get(Listing, p.listing_id) if p.listing_id else None
    if err := _fast_track_error(p, l):
        raise DomainError(err, {"already": "Объект уже показан клиентам", "no_listing": "У объекта нет объявления с фото",
                                "listing_gone": "Объявление снято или ждёт модерации",
                                "bad_status": f"Нельзя из статуса {p.status}"}[err], 409)
    p.fast_track_at, p.fast_track_by = now(), actor.id
    recipients = push(db, p, l, actor)
    log_event(db, "property", p.id, "fast_track", actor, clients=len(recipients))
    db.commit()
    for c in recipients:  # after the commit: client_views is the log, a failed send is logged by notify itself
        try:
            notify.object_to_client({"chat_id": c.chat_id, "channel": c.channel, "external_id": c.external_id,
                                     "lang": c.lang}, l.id)
        except Exception:  # noqa: BLE001 - notify never raises; a Telegram bug must not undo the fast track
            pass
    return p, recipients


def push(db: Session, p: Property, l: Listing, actor: User | None, cap: int = PUSH_CAP) -> list[Client]:
    """Clients the object fits who never got / reacted to / requested it, newest active first, at most cap.
    Logged in client_views (source=push); the caller commits and sends."""
    seen = set(db.scalars(select(ClientView.client_id).where(ClientView.listing_id == l.id)))
    seen |= set(db.scalars(select(Reaction.client_id).where(Reaction.listing_id == l.id)))
    seen |= set(db.scalars(select(Lead.client_id).where(Lead.listing_id == l.id)))
    out = [c for c in matched(active_clients(db), property_facts(p, l)) if c.id not in seen][:cap]
    for c in out:
        db.add(ClientView(client_id=c.id, listing_id=l.id, property_id=p.id, source="push"))
    return out
