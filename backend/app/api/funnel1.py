"""Funnel 1: new listings, properties, owners."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import progress
from app.api.deps import current_user, paginate, require_level
from app.db import get_db
from app.models import PUBLICATION_CHANNELS, Lead, Listing, Owner, Property, User, at_least
from app.services.errors import DomainError, forbidden
from app.services import change_requests as CR
from app.services import demand, geo, matching, similar
from app.services import properties as P
from app.services.leads import ACTIVE
from app.services.common import get_or_404, jload, log_event
from app.services.dedup import norm_address, norm_phone, owner_phones, phone_rows, set_owner_phones

router = APIRouter(tags=["objects"])


# ---------------------------------------------------------------- listings

@router.get("/listings/new")
def listings_new(q: str | None = None, source: list[str] | None = Query(None), flag: str | None = None,
                 district: list[str] | None = Query(None), metro: str | None = None, bedrooms: str | None = None,
                 price_min: int | None = Query(None, ge=0), price_max: int | None = Query(None, ge=0),
                 limit: int = Query(50, ge=0, le=500), offset: int = Query(0, ge=0), known: int = 0, competitor: int = 0,
                 prop_type: Literal["apartment", "house", "commercial"] | None = None,
                 db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.new_listings(db, q, source, flag, limit, offset, district, metro, price_min, price_max, bedrooms, bool(known),
                          bool(competitor), prop_type)


@router.get("/listings/new/facets")
def listings_new_facets(competitor: int = 0, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.new_listing_facets(db, bool(competitor))


@router.get("/listings/{listing_id}")
def listing_get(listing_id: str, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    listing = get_or_404(db, Listing, listing_id, "listing")
    prop = db.scalar(select(Property).where(Property.listing_id == listing_id))
    # The assigned viewing agent also needs the source contact to arrange the viewing.
    assigned = db.scalar(select(Lead.id).where(Lead.listing_id == listing_id, Lead.agent_id == user.id,
                                              Lead.status.in_(ACTIVE)).limit(1))
    full = at_least(user, "operator") or assigned is not None or (prop is not None and prop.agent_id == user.id and prop.status != "archived")
    d = P.listing_out(listing, full=full)
    d["contacts_visible"] = full
    if not full and (prop is None or prop.status == "archived"):  # nobody's ad yet: its link, to check it before taking
        d["urls"] = jload(listing.urls, []) or []
    if full and prop and prop.owner_id:
        owner = db.get(Owner, prop.owner_id)
        if owner:
            d["owner"] = {"name": owner.name, "phone": owner.phone, "phones": owner_phones(owner), "is_agent": owner.is_agent}
    d["property"] = {"id": prop.id, "status": prop.status, "agent_id": prop.agent_id} if prop else None
    d["similar"] = P.similar_for(db, [listing.id]).get(listing.id, [])
    d["refused"] = P.refusals_for(db, [listing.id]).get(listing.id)
    return d


class ListingAgentIn(BaseModel):
    owner_name: str | None = None
    owner_phone: str | None = None


@router.post("/listings/{listing_id}/dismiss")
def listing_dismiss(listing_id: str, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    P.dismiss_listing(db, listing_id, user)
    return {"ok": True}


@router.post("/listings/{listing_id}/agent")
def listing_agent(listing_id: str, body: ListingAgentIn, db: Session = Depends(get_db, scope="function"),
                  user: User = Depends(current_user)):
    P.mark_listing_agent(db, listing_id, user, body.owner_name, body.owner_phone)
    return {"ok": True}


class MatchDecisionIn(BaseModel):
    same: bool


class SimilarDecisionIn(BaseModel):
    a: str = Field(max_length=200)
    b: str = Field(max_length=200)
    same: bool | None = None
    verdict: Literal["same", "different", "review"] | None = None  # «Нужна проверка»: review (David 11-19)


@router.get("/listings/{listing_id}/similar")
def listing_similar(listing_id: str, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """«Похожие объекты» of an ad: our objects (the archive too) and the live ads of every site (services/similar.py)."""
    return {"items": similar.find(db, similar.key("l", listing_id), user)}


@router.get("/properties/{pid}/similar")
def property_similar(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    p = P.get_visible(db, pid, user)
    return {"items": P.similar_with_sites(db, p, similar.find(db, similar.key("p", pid), user))}


@router.post("/similar/decide")
def similar_decide(body: SimilarDecisionIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """«Дубль» / «Не дубль» / «Нужна проверка» on a pair of «Похожие объекты» (an ad called the same as an object is
    linked to it). Any staff member: the agent who found the twin decides, even when the other object is a colleague's."""
    if body.verdict is None and body.same is None:
        raise DomainError("bad_request", "same or verdict")
    return similar.decide(db, body.a, body.b, body.same, user, body.verdict)


@router.post("/listings/{listing_id}/matches/{pid}")
def listing_match_decide(listing_id: str, pid: int, body: MatchDecisionIn, db: Session = Depends(get_db, scope="function"),
                         user: User = Depends(require_level("operator"))):
    """«Тот же объект» (same=true: the ad is linked to the object) / «Другой» on a «Похож на объект #N» card."""
    m = matching.decide(db, listing_id, pid, user, body.same)
    return {"ok": True, "status": m.status}


# ---------------------------------------------------------------- properties

class PhoneIn(BaseModel):
    phone: str | None = None
    note: str | None = Field(None, max_length=100)  # «сестра», «WhatsApp»


class PropertyIn(BaseModel):
    listing_id: str | None = None
    force: bool | None = None  # «Беру в работу» after «Это другой объект» (possible_duplicate)
    address: str | None = None
    district: str | None = None
    lat: float | None = None  # the address field's point (OSM / Google Maps link / a dragged marker)
    lng: float | None = None
    cadastral_code: str | None = None
    price: float | None = None
    currency: str | None = None
    bedrooms: int | None = None
    rooms: int | None = None
    area: float | None = None
    floor: int | None = None
    total_floors: int | None = None
    description: str | None = None
    is_exclusive: bool | None = None
    source_url: str | None = None
    source_type: str | None = None
    owner_name: str | None = None
    owner_phone: str | None = None
    owner_phones: list[PhoneIn] | None = None  # the owner's whole list, the main first (replaces owner_phone then)
    owner_id: int | None = None  # picked from the owners list
    owner_separate: bool | None = None  # the new phone is another owner's: keep this owner, don't re-link
    post_fields: dict | None = None  # services.properties.POST_ONE / POST_MANY / POST_NUM
    rent_from: str | None = None  # «Сдан» by hand: YYYY-MM-DD, "" clears
    rent_to: str | None = None
    tenant_name: str | None = None
    tenant_phone: str | None = None
    tenant_tg: str | None = None
    reason: str | None = None  # why: goes with a «Запрос оператору» for locked fields (stage 6)


class CallIn(BaseModel):
    reached: bool


class OwnerStepIn(BaseModel):
    is_owner: bool
    owner_name: str | None = None
    owner_phone: str | None = None
    owner_phones: list[PhoneIn] | None = None  # David 11-18: the ad's number and the one given on the call


class ShootIn(BaseModel):
    shoot_at: datetime


class ReviewIn(BaseModel):
    approve: bool
    comment: str | None = None


class DupIn(BaseModel):
    phone: str | None = None
    address: str | None = None
    url: str | None = None
    cadastral: str | None = None


@router.get("/properties")
def properties_list(status: str | None = None, agent_id: int | None = None, q: str | None = None, mine: int = 0,
                    base: int = 0, rented: int = 0, freeing: int = 0, limit: int = 50, offset: int = 0,
                    source: list[str] | None = Query(None), district: list[str] | None = Query(None), metro: str | None = None,
                    bedrooms: str | None = None, price_min: int | None = Query(None, ge=0), price_max: int | None = Query(None, ge=0),
                    archive_reason: str | None = None, deal: Literal["rent", "sale"] | None = None,
                    db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """base=1: «База объектов» — only objects shot and verified by the operator (P.BASE_STATUSES, published at least once).
    deal: rent | sale (post_fields.deal), the «Аренда / Продажа» switch of «Объекты»."""
    stmt = P.query(db, user, status, agent_id, q, bool(mine), base=bool(base), rented=bool(rented), source=source,
                   district=district, metro=metro, bedrooms=bedrooms, price_min=price_min, price_max=price_max,
                   archive_reason=archive_reason, deal=deal, freeing=bool(freeing))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.scalars(stmt.limit(max(1, min(limit, 500))).offset(max(0, offset)))
    return {"items": P.outs(db, rows, user), "total": total}


@router.get("/properties/facets")
def properties_facets(status: str | None = None, agent_id: int | None = None, mine: int = 0, base: int = 0,
                      deal: Literal["rent", "sale"] | None = None,
                      db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """the base tabs' filter options with counts (same scope params as /properties)"""
    return P.facets(db, user, status=status, agent_id=agent_id, mine=bool(mine), base=bool(base), deal=deal)


@router.get("/properties/counts")
def properties_counts(deal: Literal["rent", "sale"] | None = None, db: Session = Depends(get_db, scope="function"),
                      user: User = Depends(current_user)):
    """objects per status, for «В базе N объектов» (counts only, nothing personal)"""
    return P.status_counts(db, deal)


@router.get("/properties/board")
def properties_board(deal: Literal["rent", "sale"] | None = None, db: Session = Depends(get_db, scope="function"),
                     user: User = Depends(current_user)):
    return P.board(db, user, deal=deal)


@router.post("/properties/board/hide")
def properties_board_hide(body: dict, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return {"hidden": P.board_hide(db, user, str(body.get("status") or ""))}


@router.get("/properties/deal-search")
def properties_deal_search(q: str | None = None, pid: int | None = None, deal_type: str = "rent", lead_id: int | None = None,
                           db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """Stage 7: the deal form's object picker (agents see another agent's object as blocked, without its data)."""
    lead = db.get(Lead, lead_id) if lead_id else None
    allow = lead.property_id if lead is not None and (at_least(user, "operator") or lead.agent_id == user.id) else None
    return {"items": P.deal_search(db, user, q, pid, deal_type, allow)}


@router.post("/properties/check-duplicate")
def check_duplicate(body: DupIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    res = P.check_duplicate(db, body.phone, body.address, body.url, body.cadastral)
    if not at_least(user, "operator"):
        mine = set(db.scalars(select(Property.owner_id).where(Property.agent_id == user.id, Property.owner_id.isnot(None))))
        for r in res.get("results", []):
            if r.get("source") == "owners" and r.get("owner_id") not in mine:
                r["phone"] = r["phones"] = r["owner"] = None
            if r.get("property_id") and not P.can_open(db, P._prop(db, r["property_id"]), user):
                r["property_id"] = None  # no link to a card he can't open; the match itself still shows
    return res


@router.post("/properties")
def properties_create(body: PropertyIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    data = body.model_dump(exclude_none=True)
    listing_id = data.pop("listing_id", None)
    force = data.pop("force", False)
    phones = data.pop("owner_phones", None)  # Irakli 11-19: «+ Ещё номер» in «Объект вручную»
    walk_in = not listing_id and data.get("owner_phone")  # «Объект вручную»: the owner came himself, no call step
    # «Беру в работу» on an ad that may already be our object (Stanislav 11-17: never work one flat twice): the agent
    # sees the objects first; «Это другой объект — беру» (force) records them as not this one and takes it
    warn = similar.objects_to_warn(db, listing_id, user) if listing_id else []
    if warn and not force:
        raise DomainError("possible_duplicate", "Похоже, эта квартира уже есть в нашей базе — проверьте перед тем, как брать",
                          409, data={"items": warn})
    p = P.take(db, listing_id, user, data, commit=not walk_in)
    if warn:
        similar.mark_different(db, similar.key("l", listing_id), [x["key"] for x in warn], user)
        db.commit()
    if walk_in:  # one transaction: a bad phone leaves no half-made object behind
        p = P.owner_or_agent(db, p.id, user, True, data.get("owner_name"), data["owner_phone"], phones)
    return P.out(db, p, user, full=True)


@router.get("/geo/search")
def geo_search(q: str = Query("", max_length=500), user: User = Depends(current_user)):
    """The address field's suggestions: up to 5 OSM candidates in Georgia, or the pasted Google Maps point."""
    return geo.search(q)


@router.get("/geo/point")
def geo_point(lat: float = Query(..., ge=40, le=44), lng: float = Query(..., ge=39, le=47), user: User = Depends(current_user)):
    """District, street + house and nearest metro (with metres) for the address field's point; the agent can still change them."""
    return geo.point_facts(lat, lng)


@router.get("/properties/{pid}")
def properties_get(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.get_visible(db, pid, user), user, full=True)


@router.patch("/properties/{pid}")
def properties_patch(pid: int, body: PropertyIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    data = body.model_dump(exclude_unset=True)
    data.pop("listing_id", None)
    p = P.update(db, pid, data, user, data.pop("reason", None))
    return _with_request(db, p, user)


def _with_request(db: Session, p: Property, user: User) -> dict:
    """The card, plus the «Запрос оператору» this call created (locked fields after verification), if any."""
    cr = getattr(p, "change_request", None)
    return {**P.out(db, p, user, full=True), "change_request": CR.outs(db, [cr])[0] if cr is not None else None}


@router.post("/properties/{pid}/call")
def properties_call(pid: int, body: CallIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.call_result(db, pid, user, body.reached), user, full=True)


@router.get("/properties/{pid}/demand")
def properties_demand(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """Stage 10: active clients this object fits (agents: anonymized profiles, staff: who)."""
    return demand.for_property(db, P.get_visible(db, pid, user), user)


@router.post("/properties/{pid}/fast-track")
def properties_fast_track(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """«Собственник согласен показать клиентам»: client-eligible with the ad's photos, sent to the matched clients."""
    p, sent = demand.fast_track(db, pid, user)
    return {**P.out(db, p, user, full=True), "sent": len(sent)}


@router.post("/properties/{pid}/owner")
def properties_owner(pid: int, body: OwnerStepIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.owner_or_agent(db, pid, user, body.is_owner, body.owner_name, body.owner_phone,
                                        [x.model_dump() for x in body.owner_phones or []]), user, full=True)


class RefusedIn(BaseModel):
    comment: str | None = None


class RentedSelfIn(BaseModel):
    comment: str | None = None
    rent_from: str | None = None
    rent_to: str | None = None
    price: str | None = None


@router.post("/properties/{pid}/refused")
def properties_refused(pid: int, body: RefusedIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.owner_refused(db, pid, user, body.comment), user, full=True)


class ThinkingIn(BaseModel):
    until: datetime
    comment: str | None = None


@router.post("/properties/{pid}/thinking")
def properties_thinking(pid: int, body: ThinkingIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.owner_thinking(db, pid, user, body.until, body.comment), user, full=True)


class ResumeIn(BaseModel):
    republish: bool = False  # a rented object: «Опубликовать снова» (staff) instead of «Вернуть в работу»


@router.post("/properties/{pid}/resume")
def properties_resume(pid: int, body: ResumeIn | None = None, db: Session = Depends(get_db, scope="function"),
                      user: User = Depends(current_user)):
    return P.out(db, P.resume(db, pid, user, (body or ResumeIn()).republish), user, full=True)


@router.post("/properties/{pid}/unpost")
def properties_unpost(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """«Снять только из канала»: the posts leave the channel, the site and the status stay (properties.unpost_channel)."""
    return P.out(db, P.unpost_channel(db, pid, user), user, full=True)


@router.post("/properties/{pid}/unpublish")
def properties_unpublish(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """«Снять с публикации»: off the site and the channel (properties.unpublish)."""
    return P.out(db, P.unpublish(db, pid, user), user, full=True)


@router.post("/properties/{pid}/rented-self")
def properties_rented_self(pid: int, body: RentedSelfIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.owner_rented_self(db, pid, user, body.comment, body.rent_from, body.rent_to, body.price), user, full=True)


@router.post("/properties/{pid}/shoot")
def properties_shoot(pid: int, body: ShootIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    p, dup = P.schedule_shoot(db, pid, user, body.shoot_at)
    return {**P.out(db, p, user, full=True), "dup_check": dup}


@router.post("/properties/{pid}/media")
def properties_media(pid: int, files: list[UploadFile] = File(...), room: str | None = Form(None),
                     db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    # sync (threadpool): storage puts and HEIC previews took the one event loop of the api, the whole CRM froze for seconds
    payload = [(f.filename or "file", f.file.read()) for f in files]
    ids = [m.id for m in P.upload_media(db, pid, payload, room, user)]
    # uploaded_ids: the UI puts parallel uploads back in the agent's selection order (positions follow completion order)
    return {**P.out(db, get_or_404(db, Property, pid), user, full=True), "uploaded_ids": ids}


@router.post("/properties/{pid}/media/part")
def properties_media_part(pid: int, file: UploadFile = File(...), upload_id: str = Form(...), name: str = Form(...),
                          offset: int = Form(...), total: int = Form(...), room: str | None = Form(None),
                          db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """A big file in parts (P.upload_part): {received} until the last part, then the card like /media."""
    r = P.upload_part(db, pid, upload_id, name, offset, total, file.file.read(), room, user)
    if isinstance(r, int):
        return {"received": r}
    return {**P.out(db, get_or_404(db, Property, pid), user, full=True), "uploaded_ids": [m.id for m in r]}


@router.post("/properties/{pid}/exclusive-contract")
async def properties_contract(pid: int, file: UploadFile = File(...), db: Session = Depends(get_db, scope="function"),
                              user: User = Depends(current_user)):
    P.upload_contract(db, pid, file.filename or "contract.pdf", await file.read(), user)
    return P.out(db, get_or_404(db, Property, pid), user, full=True)


@router.post("/properties/{pid}/exclusive-no-esign")
async def properties_no_esign(pid: int, reason: str = Form(""), file: UploadFile | None = File(None),
                              db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """«Без электронной подписи»: a request to the operator (reason + an optional scan / photo of a paper contract)."""
    scan = (file.filename or "scan", await file.read()) if file is not None else None
    CR.request_no_esign(db, pid, reason, user, scan)
    return P.out(db, get_or_404(db, Property, pid), user, full=True)


@router.post("/properties/{pid}/owner-docs/{kind}")
async def properties_owner_doc(pid: int, kind: str, file: UploadFile = File(...), db: Session = Depends(get_db, scope="function"),
                               user: User = Depends(current_user)):
    """kind: owner_id_doc (passport/ID) | registry_extract — private, staff and the object's agent only."""
    P.upload_owner_doc(db, pid, kind, file.filename or "", await file.read(), user)
    return P.out(db, get_or_404(db, Property, pid), user, full=True)


# the owner's exclusive confirmation page (frontend /sign/<token>): no CRM login, the token is the access
@router.get("/public/exclusive/{token}")
def public_exclusive(token: str, db: Session = Depends(get_db, scope="function")):
    return P.sign_view(db, token)


@router.get("/public/exclusive/{token}/registry")
def public_exclusive_registry(token: str, personal_id: str | None = None, cadastral: str | None = None, page: int = 1,
                              db: Session = Depends(get_db, scope="function")):
    """Public Registry applications by the owner's personal ID (services/registry.py): only with a live signing link,
    not an open proxy."""
    from app.services import registry
    P.sign_view(db, token)  # 404 for a bad / dead link
    return registry.search(page, token, cadastral=cadastral, personal_id=personal_id)


@router.post("/public/exclusive/{token}/extract-code")
async def public_exclusive_extract_code(token: str, file: UploadFile = File(...), db: Session = Depends(get_db, scope="function")):
    """The cadastral code read from the extract the owner picked (he doesn't type it); null if the PDF has no text."""
    return P.draft_extract(db, token, await file.read(), file.filename or "extract.pdf")


@router.put("/public/exclusive/{token}/draft")
def public_exclusive_draft(token: str, body: dict, db: Session = Depends(get_db, scope="function")):
    """The sign page autosaves what the owner typed: reopening the link continues it (P.save_sign_draft)."""
    return P.save_sign_draft(db, token, body)


@router.post("/public/exclusive/{token}/id-doc")
async def public_exclusive_id_doc(token: str, file: UploadFile = File(...), db: Session = Depends(get_db, scope="function")):
    """The passport / ID photo, kept with the draft the moment he picks it."""
    return P.draft_id_doc(db, token, file.filename or "", await file.read())


@router.get("/registry/applications")
def registry_applications(personal_id: str | None = None, cadastral: str | None = None, page: int = 1,
                          user: User = Depends(current_user)):
    """The contract generator: the owner's NAPR applications by his personal ID; the agent opens one on NAPR (its
    CAPTCHA) and copies the cadastral code from there."""
    from app.services import registry
    return registry.search(page, f"user:{user.id}", cadastral=cadastral, personal_id=personal_id, limit=120)


@router.get("/registry/known")
def registry_known(personal_id: str, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """The contract generator: the owner's objects the CRM already has by his personal ID (earlier contracts, e-signatures,
    exclusive confirmations) — the cadastral code fills in at once, without the registry."""
    from app.services import registry
    return {"items": registry.known(db, personal_id)}


class RegistryTextIn(BaseModel):
    text: str = Field(max_length=500_000)


@router.post("/registry/parse-text")
def registry_parse_text(body: RegistryTextIn, user: User = Depends(current_user)):
    """The contract generator: the agent copied the NAPR application page (after its CAPTCHA) or the extract's text and
    pasted it here → the code and every field found (registry.parse_extract)."""
    from app.services import registry
    fields = registry.parse_extract(body.text)
    return {"cadastral_code": fields.get("cadastral_code"), "fields": fields}


class RegistryUrlIn(BaseModel):
    url: str = Field(max_length=2000)


@router.post("/registry/extract-from-url")
def registry_extract_from_url(body: RegistryUrlIn, user: User = Depends(current_user)):
    """The contract generator: the agent pasted the link to the extract (NAPR's GetBlob PDF) → the server downloads it
    (NAPR hosts only), keeps the PDF in private storage and returns every field found; `file` goes into the contract's
    data (extract_file) with the link (extract_url) — the card offers both. Empty fields: a scan without text."""
    from app.services import registry
    pdf = registry.download_extract(body.url)
    fields = registry.parse_extract(registry.text_of_pdf(pdf) or "")
    return {"cadastral_code": fields.get("cadastral_code"), "fields": fields, "file": registry.store_extract(pdf),
            "url": body.url.strip()}


@router.post("/public/exclusive/{token}/extract-from-url")
def public_exclusive_extract_from_url(token: str, body: RegistryUrlIn, db: Session = Depends(get_db, scope="function")):
    """As in the contract wizard: the owner pastes the extract's link (NAPR's GetBlob PDF) → the server downloads it
    (NAPR hosts only), keeps it in the page's draft and returns the code and fields found."""
    from app.services import registry
    P.sign_view(db, token)  # a dead link downloads nothing
    return P.draft_extract(db, token, registry.download_extract(body.url), "extract.pdf", body.url.strip())


@router.post("/public/exclusive/{token}")
async def public_exclusive_sign(token: str, request: Request, file: UploadFile = File(...),
                                id_doc: UploadFile | None = File(None), extract: UploadFile | None = File(None), full_name: str = Form(""),
                                personal_id: str = Form(""), phone: str = Form(""), cadastral_code: str = Form(""),
                                consent: bool = Form(False), registry_app: str = Form(""),
                                db: Session = Depends(get_db, scope="function")):
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client and request.client.host)
    P.sign_contract(db, token, {"full_name": full_name, "personal_id": personal_id, "phone": phone,
                                "cadastral_code": cadastral_code, "consent": consent,
                                "registry_app": registry_app},
                    await file.read(), ip, request.headers.get("user-agent"),
                    {P.OWNER_ID_DOC: (id_doc.filename or "", await id_doc.read()) if id_doc else None,
                     P.REGISTRY_EXTRACT: (extract.filename or "", await extract.read()) if extract else None})
    return P.sign_view(db, token)


@router.post("/properties/{pid}/myhome-copies")
def properties_myhome_copies(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """«Найти копии» (David 11-22): the worker searches myhome / ss.ge / korter (by the link's photos too) within a
    minute, then the duplicates; «Где ещё выложен» shows the copies."""
    from app.services import jobs
    from app.services.common import now
    p = P.get_visible(db, pid, user)
    jobs.schedule(db, "dup_scan", p.id, now(), {"trigger": "manual"})  # replaces a pending base-only scan
    db.commit()
    return {"ok": True}


class CompareLink(BaseModel):
    url: str


@router.post("/properties/{pid}/compare-link")
def properties_compare_link(pid: int, body: CompareLink, db: Session = Depends(get_db, scope="function"),
                            user: User = Depends(current_user)):
    """«Сравнить по ссылке» (David 11-22): a myhome / ss.ge ad scored against the object (myhome_copies.compare_link)."""
    from app.services import myhome_copies
    from app.services.errors import DomainError
    p = P.get_visible(db, pid, user)
    try:
        return myhome_copies.compare_link(db, p.id, body.url, user)
    except ValueError as e:
        raise DomainError("bad_link", str(e)) from e
    except Exception as e:  # noqa: BLE001 — the site or Apify did not answer
        raise DomainError("link_failed", f"Не удалось открыть объявление: {str(e)[:120]}", 502) from e


@router.get("/properties/{pid}/originals")
def properties_originals(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """«Скачать оригиналы» (Stanislav 11-22, David 11-21): the object's photos as uploaded — no watermark, that one is put
    only on the channel post — in the gallery order, one zip, for posting on myhome / ss by hand."""
    import io
    import os
    import zipfile

    from fastapi.responses import Response

    from app import storage
    from app.models import PropertyMedia
    if not at_least(user, "operator"):  # Irakli 11-22: superadmin, admin and operators only
        raise forbidden("operators only")
    p = P.get_visible(db, pid, user)
    keys = db.scalars(select(PropertyMedia.path).where(PropertyMedia.property_id == p.id, PropertyMedia.kind == "photo")
                      .order_by(PropertyMedia.position, PropertyMedia.id)).all()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:  # JPEGs: nothing to deflate
        for n, k in enumerate((k for k in keys if not k.startswith(P.PRIVATE_PREFIX)), 1):
            if (data := storage.read(k)) is not None:
                z.writestr(f"{p.id}_{n:02d}{os.path.splitext(k)[1].lower() or '.jpg'}", data)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="object_{p.id}_originals.zip"'})


@router.put("/properties/{pid}/media/order")
def properties_media_order(pid: int, body: dict, db: Session = Depends(get_db, scope="function"),
                           user: User = Depends(current_user)):
    P.reorder_media(db, pid, body.get("ids") or [], user)
    return P.out(db, get_or_404(db, Property, pid), user, full=True)


@router.patch("/properties/{pid}/media/{mid}")
def properties_media_room(pid: int, mid: int, body: dict, db: Session = Depends(get_db, scope="function"),
                          user: User = Depends(current_user)):
    m = P.set_media_room(db, pid, mid, body.get("room"), user)
    return {"id": m.id, "room": m.room}


@router.delete("/properties/{pid}/media/{mid}")
def properties_media_delete(pid: int, mid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    P.delete_media(db, pid, mid, user)
    return {"ok": True}


@router.post("/properties/{pid}/checklist")
def properties_checklist(pid: int, body: dict, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    return P.out(db, P.set_checklist(db, pid, body.get("checklist", body), user), user, full=True)


@router.post("/properties/{pid}/submit")
def properties_submit(pid: int, other_flat: bool = False, db: Session = Depends(get_db, scope="function"),
                      user: User = Depends(current_user)):
    """?other_flat=1: «Это другая квартира» after an object_exists answer (the owner's second flat in the house)."""
    return P.out(db, P.submit_for_review(db, pid, user, other_flat), user, full=True)


@router.post("/properties/{pid}/review")
def properties_review(pid: int, body: ReviewIn, db: Session = Depends(get_db, scope="function"),
                      user: User = Depends(require_level("operator"))):
    return P.out(db, P.review(db, pid, user, body.approve, body.comment), user, full=True)


@router.post("/properties/{pid}/verify")
def properties_verify(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("operator"))):
    return P.out(db, P.verify(db, pid, user), user, full=True)


@router.get("/properties/{pid}/post")
def properties_post(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """The card's post generator: the channel caption built from the object, its hashtags, the channels to post to.
    Staff, and the object's agent (he fills the generator and sees the post before sending it to moderation)."""
    p = P.get_visible(db, pid, user)
    if not at_least(user, "operator") and p.agent_id != user.id:
        raise forbidden("only the object's agent or staff")
    text = P.post_text(db, p)
    return {"text": text, "hashtags": P.hashtags(text), "channels": list(PUBLICATION_CHANNELS)}


class PublishIn(BaseModel):
    text: str | None = None
    media_ids: list[int] | None = None  # picked files in album order; None = all
    channel: str = "tg"
    comment: str | None = None
    tg: bool = True  # «В Telegram»
    site: bool = True  # «На сайт»


@router.post("/properties/{pid}/publish")
def properties_publish(pid: int, body: PublishIn | None = None, db: Session = Depends(get_db, scope="function"),
                       user: User = Depends(current_user)):
    b = body or PublishIn()  # an agent (his own published object, P.publish checks) reposts from the fields: no own text
    try:
        return P.out(db, P.publish(db, pid, user, b.text, b.media_ids, b.channel, b.comment, b.tg, b.site), user, full=True)
    finally:
        progress.done(f"publish:{pid}")


@router.get("/properties/{pid}/publish/progress")
def properties_publish_progress(pid: int, user: User = Depends(current_user)):
    """What the publish of this object is doing now ({step, n, of, chat}; {} when none runs): the card polls it."""
    return progress.get(f"publish:{pid}")


class SiteIn(BaseModel):
    on: bool


@router.post("/properties/{pid}/site")
def properties_site(pid: int, body: SiteIn, db: Session = Depends(get_db, scope="function"),
                    user: User = Depends(current_user)):
    return P.out(db, P.set_site(db, pid, user, body.on), user, full=True)


@router.get("/properties/{pid}/elsewhere")
def properties_elsewhere(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """Where else the object is advertised (the exclusive toggle shows it). Links only for staff / the object's agent."""
    p = P.get_visible(db, pid, user)
    show = P.can_see_owner(p, user)
    return {"items": [{**a, "url": a["url"] if show else None} for a in matching.elsewhere(db, p)]}


@router.post("/properties/{pid}/listings/{listing_id}/removed")
def properties_ad_removed(pid: int, listing_id: str, db: Session = Depends(get_db, scope="function"),
                          user: User = Depends(require_level("operator"))):
    return P.out(db, P.ad_removed(db, pid, listing_id, user), user, full=True)


class ArchiveIn(BaseModel):
    reason: str | None = None  # models.ARCHIVE_REASONS
    comment: str | None = None
    duplicate_of: int | None = None
    rent_from: str | None = None  # «Собственник сдал»: the owner's lease, required
    rent_to: str | None = None
    price: str | None = None


@router.post("/properties/{pid}/archive")
def properties_archive(pid: int, body: ArchiveIn | None = None, db: Session = Depends(get_db, scope="function"),
                       user: User = Depends(current_user)):
    b = body or ArchiveIn()
    return _with_request(db, P.archive(db, pid, user, b.reason, b.comment, b.duplicate_of, b.rent_from, b.rent_to, b.price), user)


# ---------------------------------------------------------------- «Запросы» (stage 6)

class DecideIn(BaseModel):
    approve: bool
    comment: str | None = None


@router.get("/change-requests")
def change_requests_list(status: str | None = "pending", limit: int = 100, offset: int = 0,
                         db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("operator"))):
    stmt = CR.query(db, user, status)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = list(db.scalars(stmt.limit(max(1, min(limit, 500))).offset(max(0, offset))))
    return {"items": CR.outs(db, rows), "total": total}


@router.post("/change-requests/{rid}/decide")
def change_requests_decide(rid: int, body: DecideIn, db: Session = Depends(get_db, scope="function"),
                           user: User = Depends(require_level("operator"))):
    return CR.outs(db, [CR.decide(db, rid, user, body.approve, body.comment)])[0]


@router.get("/properties/{pid}/history")
def properties_history(pid: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    P.get_visible(db, pid, user)
    return P.history(db, pid)


# ---------------------------------------------------------------- owners

class OwnerIn(BaseModel):
    name: str | None = None
    phone: str | None = None
    phones: list[PhoneIn] | None = None  # the whole list, the main first (replaces phone then)
    address: str | None = None
    link: str | None = None
    is_agent: bool | None = None
    notes: str | None = None


def owner_out(o: Owner, show_phone: bool = True) -> dict:
    return {"id": o.id, "name": o.name, "phone": o.phone if show_phone else None,
            "phones": owner_phones(o) if show_phone else None, "address": o.address, "link": o.link,
            "is_agent": o.is_agent, "notes": o.notes, "created_by": o.created_by, "agent_id": o.agent_id,
            "created_at": o.created_at.isoformat() if o.created_at else None}


@router.get("/owners")
def owners_list(q: str | None = None, scope: str = "all", limit: int = 50, offset: int = 0,
                db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    """scope: base = owners of objects the agent shot and sent (review / published / rented / verified); unlinked = the old
    Owner_Base contacts with no object yet (agents pick them in forms, the link appears then); agents; all."""
    stmt = select(Owner)
    in_base = select(Property.owner_id).where(Property.owner_id.isnot(None), or_(
        Property.status.in_(("review", "published", "rented")), Property.verified_at.isnot(None)))
    if scope == "base":
        stmt = stmt.where(Owner.id.in_(in_base), Owner.is_agent.is_(False))
    elif scope == "unlinked":
        stmt = stmt.where(~Owner.id.in_(select(Property.owner_id).where(Property.owner_id.isnot(None))), Owner.is_agent.is_(False))
    elif scope == "agents":
        stmt = stmt.where(Owner.is_agent.is_(True))
    if q:
        like = f"%{q.strip()}%"
        pn = norm_phone(q)
        conds = [Owner.name.ilike(like), Owner.address.ilike(like)]
        if at_least(user, "operator") and len(pn) > 3:
            conds.append(P.owner_phone_like(pn))
        elif len(pn) >= 9:  # agents: full number only, no digit-by-digit search of hidden phones
            conds.append(P.owner_has_phone(pn))
        stmt = stmt.where(or_(*conds))
    if at_least(user, "operator"):
        return paginate(db, stmt.order_by(Owner.id.desc()), owner_out, limit, offset)
    mine = set(db.scalars(select(Property.owner_id).where(Property.agent_id == user.id, Property.owner_id.isnot(None))))
    return paginate(db, stmt.order_by(Owner.id.desc()), lambda o: owner_out(o, o.id in mine or o.agent_id == user.id),
                    limit, offset)


@router.post("/owners")
def owners_create(body: OwnerIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)):
    o, _ = P.upsert_owner(db, body.name, body.phone, body.address, body.link, bool(body.is_agent), user,
                          [x.model_dump() for x in body.phones or []])
    if body.notes:
        o.notes = body.notes
    db.commit()
    return owner_out(o)


@router.patch("/owners/{oid}")
def owners_patch(oid: int, body: OwnerIn, db: Session = Depends(get_db, scope="function"), user: User = Depends(require_level("operator"))):
    o = get_or_404(db, Owner, oid, "owner")
    data = body.model_dump(exclude_unset=True)
    phones = data.pop("phones", None)
    phone = data.pop("phone", None) if "phone" in data else None
    for k, v in data.items():
        setattr(o, k, v)
    if phones is not None:
        set_owner_phones(o, phone_rows(phones))
        data["phones"] = phones
    elif "phone" in body.model_fields_set:  # the main number only: his other numbers stay
        set_owner_phones(o, phone_rows([phone, *owner_phones(o)[1:]]) if (phone or "").strip() else [])
        data["phone"] = phone
    if "address" in data:
        o.address_norm = norm_address(o.address)
    log_event(db, "owner", o.id, "updated", user, fields=sorted(data))
    db.commit()
    return owner_out(o)


class TransferIn(BaseModel):
    agent_id: int
    reason: str | None = None


@router.post("/owners/{oid}/transfer")
def owners_transfer(oid: int, body: TransferIn, db: Session = Depends(get_db, scope="function"),
                    user: User = Depends(require_level("operator"))):
    """«Передать собственника агенту…» (stage 6.5): the owner and his live objects move to another agent."""
    return owner_out(P.transfer_owner(db, oid, body.agent_id, user, body.reason))


@router.post("/properties/{pid}/transfer-request")
def properties_transfer_request(pid: int, body: TransferIn, db: Session = Depends(get_db, scope="function"),
                                user: User = Depends(current_user)):
    """An agent's «Запрос оператору» to move the object's owner to another agent (staff: transferred right away)."""
    cr = CR.request_transfer(db, pid, body.agent_id, user, body.reason)
    return {"change_request": CR.outs(db, [cr])[0] if cr else None}


class OwnerRequestIn(BaseModel):
    phone: str
    reason: str | None = None


@router.post("/properties/{pid}/owner-request")
def properties_owner_request(pid: int, body: OwnerRequestIn, db: Session = Depends(get_db, scope="function"),
                             user: User = Depends(current_user)):
    """«Запросить собственника» (David 11-24): the typed owner is another agent's — the operator moves him over."""
    return {"change_request": CR.outs(db, [CR.request_owner(db, pid, body.phone, user, body.reason)])[0]}


@router.post("/properties/{pid}/listings/{listing_id}/takedown")
def properties_ad_takedown(pid: int, listing_id: str, db: Session = Depends(get_db, scope="function"),
                           user: User = Depends(current_user)):
    """«Попросить снять» on a copy in «Где ещё выложен»: the agent's task to have it taken down."""
    return P.out(db, P.ad_takedown(db, pid, listing_id, user), user, full=True)
