"""«Посуточная аренда» (Daily Rent): ApartFlow inside the CRM — apartments, stays, expenses, cleanings, staff, payouts,
reports, the busy-date calendar and the Airbnb / Booking iCal feeds (services/daily_rent.py, daily_rent_ical.py).

Superadmin, admin and the Daily Rent manager only (models.can_daily_rent). The manager's whole CRM is /api/daily-rent/*
(api/deps.current_user). The iCal export a platform polls is public, by its secret token: /api/public/daily-rent/ical/."""
import datetime as dt
import os
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import storage
from app.api.deps import current_user
from app.config import settings
from app.db import get_db
from app.models import (DR_EXPENSE_CATS, DR_PAYMENT, DR_PLATFORMS, DR_STATUS, DrApartment, DrBlock, DrBooking, DrChannel,
                        DrCleaning, DrExpense, DrStaff, User, can_daily_rent)
from app.services import daily_rent as R
from app.services import daily_rent_ical as C
from app.services import properties as P
from app.services.common import get_or_404, log_event
from app.services.errors import DomainError

router = APIRouter(prefix="/daily-rent", tags=["daily-rent"])
public = APIRouter(tags=["daily-rent"])
MAX_PHOTOS = 20


def dr_user(user: User = Depends(current_user)) -> User:
    if not can_daily_rent(user):
        raise DomainError("forbidden", "Посуточная аренда: только суперадмин, админ и менеджер посуточной аренды")
    return user


Db = Depends(get_db, scope="function")
Me = Depends(dr_user)
Cur = Literal["USD", "GEL"]
Month = Query(None, pattern=r"^\d{4}-\d{2}$")


class AptIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    address: str | None = None
    owner_name: str | None = None
    owner_contact: str | None = None
    owner_account: str | None = None
    platform_pct: float | None = Field(None, ge=0, le=100)
    platforms: list[Literal[DR_PLATFORMS]] | None = None
    status: Literal[DR_STATUS] | None = None
    active: bool | None = None
    rooms: int | None = Field(None, ge=0, le=50)
    notes: str | None = None
    util_rates: dict | None = None
    price_night: float | None = Field(None, ge=0)
    currency: Literal["USD", "GEL", "EUR"] | None = None
    description: str | None = None
    district: str | None = None
    report_lang: Literal["ru", "en", "ka"] | None = None


class BookingIn(BaseModel):
    apartment_id: int | None = None
    platform: Literal[DR_PLATFORMS] | None = None
    guest_name: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    gross: float | None = Field(None, ge=0)
    currency: Cur | None = None
    tax_pct: float | None = Field(None, ge=0, le=100)
    booking_fee: float | None = Field(None, ge=0)
    platform_pct: float | None = Field(None, ge=0, le=100)
    payment_status: Literal[DR_PAYMENT] | None = None
    assigned_staff: str | None = None
    comment: str | None = None


class ExpenseIn(BaseModel):
    booking_id: int | None = None
    apartment_id: int | None = None
    category: Literal[DR_EXPENSE_CATS] | None = None
    amount: float | None = Field(None, gt=0, le=50000)
    currency: Cur | None = None
    payer: Literal["platform", "owner"] | None = None
    comment: str | None = None
    date: dt.date | None = None


class CleaningIn(BaseModel):
    apartment_id: int | None = None
    cleaner: str | None = None
    date: dt.date | None = None
    started_at: str | None = Field(None, pattern=r"^(\d{2}:\d{2})?$")
    finished_at: str | None = Field(None, pattern=r"^(\d{2}:\d{2})?$")
    hours: float | None = Field(None, ge=0, le=24)
    hourly_rate: float | None = Field(None, ge=0)
    amount: float | None = Field(None, ge=0)
    notes: str | None = None


class StaffIn(BaseModel):
    name: str | None = Field(None, min_length=1)
    role: str | None = None
    contact: str | None = None
    active: bool | None = None


class BlockIn(BaseModel):
    apartment_id: int
    start: date
    end: date
    reason: str | None = None
    force: bool = False


class ChannelIn(BaseModel):
    apartment_id: int | None = None
    channel: Literal["airbnb", "booking", "other"] | None = None
    import_url: str | None = None


class PayIn(BaseModel):
    booking_ids: list[int] | None = None
    apartment_id: int | None = None
    paid: bool = True


SCHEMA = {DrApartment: AptIn, DrBooking: BookingIn, DrExpense: ExpenseIn, DrCleaning: CleaningIn, DrStaff: StaffIn}


def _body(model, body: dict) -> dict:
    try:
        return SCHEMA[model].model_validate(body).model_dump(exclude_unset=True)
    except ValidationError as e:
        raise DomainError("bad_request", "Проверьте поля: " + ", ".join(str(x["loc"][0]) for x in e.errors()), 422)


def _live(db: Session, model, order=None):
    return list(db.scalars(select(model).where(model.deleted_at.is_(None)).order_by(*(order or [model.id]))))


def _apt_out(db: Session, a: DrApartment) -> dict:
    return {**R.out(a), "owner_pct": 100 - float(a.platform_pct),
            "channels": [_channel_out(ch) for ch in C.channels(db, a.id)]}


def _channel_out(ch: DrChannel) -> dict:
    return {**R.out(ch), "export_url": f"{settings.PUBLIC_URL.rstrip('/')}/api/public/daily-rent/ical/{ch.export_token}.ics"}


# ---------------------------------------------------------------- everything the section shows at once

@router.get("/data")
def dr_data(db: Session = Db, user: User = Me):
    """Apartments, staff and the rate — the small lists every screen needs."""
    return {"apartments": [_apt_out(db, a) for a in _live(db, DrApartment)],
            "staff": [R.out(s) for s in _live(db, DrStaff)], "rate": R.usd_gel()}


@router.get("/summary")
def dr_summary(month_from: str | None = Month, month_to: str | None = Month, db: Session = Db, user: User = Me):
    return R.summary(db, month_from, month_to)


@router.get("/today")
def dr_today(db: Session = Db, user: User = Me):
    """«Сегодня»: who lives now, arrivals / departures, stays without money, cleanings, occupancy, alerts (daily_rent_auto)."""
    from app.services import daily_rent_auto
    return daily_rent_auto.today(db)


@router.get("/journal")
def dr_journal(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Db, user: User = Me):
    return R.journal(db, limit, offset)


# ---------------------------------------------------------------- apartments

@router.post("/apartments")
def apt_create(body: dict, db: Session = Db, user: User = Me):
    data = _body(DrApartment, body)
    if not data.get("name"):
        raise DomainError("bad_request", "Укажите название", 422)
    return _apt_out(db, R.create(db, DrApartment, data, user))


@router.get("/apartments/{aid}")
def apt_get(aid: int, month_from: str | None = Month, month_to: str | None = Month, label: str = "", db: Session = Db, user: User = Me):
    """The apartment with its stays (all, newest first), the period's KPIs and the period report text."""
    a = get_or_404(db, DrApartment, aid, "apartment")
    rows = R.bookings(db, aid)
    per = [b for b in rows if b["check_in"] and (not month_from or b["check_in"][:7] >= month_from)
           and (not month_to or b["check_in"][:7] <= month_to)]
    return {**_apt_out(db, a), "bookings": rows, "econ": R.econ(per, R.month_days(month_from, month_to)),
            "period_report": R.period_report(a, per, R.period_label(month_from, month_to), a.report_lang),
            "period_reports": {lg: R.period_report(a, per, R.period_label(month_from, month_to, lg), lg) for lg in R.REPORT_LANGS}}


@router.patch("/apartments/{aid}")
def apt_update(aid: int, body: dict, db: Session = Db, user: User = Me):
    data = _body(DrApartment, body)
    if data.get("name", "x") is None:
        data.pop("name")
    return _apt_out(db, R.update(db, DrApartment, aid, data, user))


@router.delete("/apartments/{aid}")
def apt_delete(aid: int, db: Session = Db, user: User = Me):
    return R.delete(db, DrApartment, aid, user)


@router.post("/apartments/{aid}/photos")
async def apt_photos(aid: int, files: list[UploadFile] = File(...), db: Session = Db, user: User = Me):
    a = get_or_404(db, DrApartment, aid, "apartment", lock=True)
    payload = [(f.filename or "photo.jpg", await f.read()) for f in files]
    if len(a.photos) + len(payload) > MAX_PHOTOS:
        raise DomainError("media_limit", f"Максимум {MAX_PHOTOS} фото", 422)
    for name, content in payload:
        if (os.path.splitext(name)[1].lower() or ".jpg") not in P.PHOTO_EXT or len(content) > P.MAX_UPLOAD:
            raise DomainError("bad_file", f"Можно загружать только фото: {name}")
    keys = []
    for name, content in payload:
        key = f"daily/{a.id}/{uuid.uuid4().hex}{os.path.splitext(name)[1].lower() or '.jpg'}"
        storage.put(key, content)
        storage.put_preview(key, content)
        keys.append(key)
    a.photos = [*a.photos, *keys]
    log_event(db, "dr_apartment", a.id, "photos_added", user, keys=keys)
    db.commit()
    return {**_apt_out(db, a), "uploaded_keys": keys}


@router.put("/apartments/{aid}/photos/order")
def apt_photos_order(aid: int, body: dict, db: Session = Db, user: User = Me):
    a = get_or_404(db, DrApartment, aid, "apartment", lock=True)
    keys = body.get("keys") or []
    if sorted(keys) != sorted(a.photos):
        raise DomainError("bad_order", "Порядок фото устарел — обновите страницу", 409)
    a.photos = list(keys)
    log_event(db, "dr_apartment", a.id, "photos_reordered", user)
    db.commit()
    return _apt_out(db, a)


@router.delete("/apartments/{aid}/photos")
def apt_photo_delete(aid: int, key: str, db: Session = Db, user: User = Me):
    a = get_or_404(db, DrApartment, aid, "apartment", lock=True)
    if key not in a.photos:
        raise DomainError("not_found", "photo not found")
    a.photos = [k for k in a.photos if k != key]
    log_event(db, "dr_apartment", a.id, "photo_deleted", user, key=key)
    db.commit()
    try:
        storage.delete(key)
    except Exception:  # noqa: BLE001 — a stray file is harmless, the row is the truth
        pass
    return _apt_out(db, a)


# ---------------------------------------------------------------- stays and their expenses

@router.get("/bookings")
def bk_list(apartment_id: int | None = None, month_from: str | None = Month, month_to: str | None = Month,
            db: Session = Db, user: User = Me):
    return {"items": R.bookings(db, apartment_id, month_from, month_to)}


@router.get("/bookings/defaults")
def bk_defaults(apartment_id: int, platform: Literal[DR_PLATFORMS] | None = None, db: Session = Db, user: User = Me):
    return R.new_booking_defaults(get_or_404(db, DrApartment, apartment_id, "apartment"), platform)


def _bk_full(db: Session, bid: int) -> dict:
    b = get_or_404(db, DrBooking, bid, "booking")
    row = R.booking_out(b, R._expenses_by_booking(db, [b.id]).get(b.id, []), R.usd_gel())
    a = db.get(DrApartment, b.apartment_id)
    lang = a.report_lang if a else "ru"
    return {**row, "report": R.booking_report(a, row, lang), "report_lang": lang,
            "reports": R.reports_all(R.booking_report, a, row)}


@router.post("/bookings")
def bk_create(body: dict, db: Session = Db, user: User = Me):
    return _bk_full(db, R.create_booking(db, _body(DrBooking, body), user).id)


@router.get("/bookings/{bid}")
def bk_get(bid: int, db: Session = Db, user: User = Me):
    return _bk_full(db, bid)


@router.patch("/bookings/{bid}")
def bk_update(bid: int, body: dict, db: Session = Db, user: User = Me):
    R.update(db, DrBooking, bid, _body(DrBooking, body), user)
    return _bk_full(db, bid)


@router.delete("/bookings/{bid}")
def bk_delete(bid: int, db: Session = Db, user: User = Me):
    return R.delete(db, DrBooking, bid, user)


@router.post("/payouts")
def bk_pay(body: PayIn, db: Session = Db, user: User = Me):
    """«Выплачено собственнику»: one stay, several, or every ready stay of an apartment; paid=false undoes."""
    if not body.booking_ids and not body.apartment_id:
        raise DomainError("bad_request", "Укажите проживания или квартиру", 422)
    return R.pay(db, body.booking_ids, body.apartment_id, body.paid, user)


@router.post("/expenses")
def exp_create(body: dict, db: Session = Db, user: User = Me):
    data = _body(DrExpense, body)
    if not data.get("booking_id") or not data.get("amount"):
        raise DomainError("bad_request", "Расход: проживание и сумма", 422)
    b = get_or_404(db, DrBooking, data["booking_id"], "booking")
    data.setdefault("apartment_id", b.apartment_id)
    data.setdefault("date", R.today())
    data.setdefault("created_by", user.name)
    R.create(db, DrExpense, data, user)
    return _bk_full(db, b.id)


@router.patch("/expenses/{eid}")
def exp_update(eid: int, body: dict, db: Session = Db, user: User = Me):
    e = R.update(db, DrExpense, eid, _body(DrExpense, body), user)
    return _bk_full(db, e.booking_id)


@router.delete("/expenses/{eid}")
def exp_delete(eid: int, db: Session = Db, user: User = Me):
    e = get_or_404(db, DrExpense, eid, "expense")
    R.delete(db, DrExpense, eid, user)
    return _bk_full(db, e.booking_id)


# ---------------------------------------------------------------- cleanings and staff

@router.get("/cleanings")
def cl_list(db: Session = Db, user: User = Me):
    return {"items": [R.out(c) for c in _live(db, DrCleaning, [DrCleaning.date.desc().nullslast(), DrCleaning.id.desc()])]}


@router.post("/cleanings")
def cl_create(body: dict, db: Session = Db, user: User = Me):
    data = _body(DrCleaning, body)
    if not data.get("apartment_id"):
        raise DomainError("bad_request", "Выберите квартиру", 422)
    return R.out(R.create(db, DrCleaning, data, user))


@router.patch("/cleanings/{cid}")
def cl_update(cid: int, body: dict, db: Session = Db, user: User = Me):
    return R.out(R.update(db, DrCleaning, cid, _body(DrCleaning, body), user))


@router.delete("/cleanings/{cid}")
def cl_delete(cid: int, db: Session = Db, user: User = Me):
    return R.delete(db, DrCleaning, cid, user)


@router.post("/staff")
def st_create(body: dict, db: Session = Db, user: User = Me):
    data = _body(DrStaff, body)
    if not data.get("name"):
        raise DomainError("bad_request", "Укажите имя", 422)
    return R.out(R.create(db, DrStaff, data, user))


@router.patch("/staff/{sid}")
def st_update(sid: int, body: dict, db: Session = Db, user: User = Me):
    return R.out(R.update(db, DrStaff, sid, _body(DrStaff, body), user))


@router.delete("/staff/{sid}")
def st_delete(sid: int, db: Session = Db, user: User = Me):
    return R.delete(db, DrStaff, sid, user)


# ---------------------------------------------------------------- calendar, blocks, platform calendars

@router.get("/calendar")
def cal(start: date, end: date, db: Session = Db, user: User = Me):
    if (end - start).days > 400 or end <= start:
        raise DomainError("bad_request", "Период: до 400 дней", 422)
    return C.calendar(db, start, end)


@router.post("/blocks")
def block_create(body: BlockIn, db: Session = Db, user: User = Me):
    return R.out(C.save_block(db, body.model_dump(), user))


@router.patch("/blocks/{block_id}")
def block_update(block_id: int, body: BlockIn, db: Session = Db, user: User = Me):
    return R.out(C.save_block(db, body.model_dump(), user, block_id))


@router.delete("/blocks/{block_id}")
def block_cancel(block_id: int, db: Session = Db, user: User = Me):
    return R.out(C.cancel_block(db, block_id, user))


@router.post("/channels")
def ch_create(body: ChannelIn, db: Session = Db, user: User = Me):
    return _channel_out(C.save_channel(db, body.model_dump(), user))


@router.patch("/channels/{cid}")
def ch_update(cid: int, body: ChannelIn, db: Session = Db, user: User = Me):
    return _channel_out(C.save_channel(db, body.model_dump(), user, cid))


@router.delete("/channels/{cid}")
def ch_delete(cid: int, db: Session = Db, user: User = Me):
    return C.delete_channel(db, cid, user)


@router.post("/channels/{cid}/sync")
def ch_sync(cid: int, db: Session = Db, user: User = Me):
    return {**C.sync_channel(db, cid), "channel": _channel_out(db.get(DrChannel, cid))}


@public.get("/public/daily-rent/ical/{file}")
def ical_export(file: str, db: Session = Db):
    """Our busy dates for one apartment × platform (Airbnb / Booking poll it). No login: the token is the secret."""
    feed = C.export_feed(db, file.removesuffix(".ics"))
    if feed is None:
        return Response("not found", status_code=404, media_type="text/plain")
    return Response(feed, media_type="text/calendar; charset=utf-8", headers={"Cache-Control": "no-cache"})
