"""CRM: cars, the site's own objects besides real estate (sales are properties now). Agents add and edit them
(recruits and the Daily Rent manager have no access). «Опубликовать» (POST /car/{id}/publish) goes to the checked
targets: the site and / or Telegram (operators); /status takes it off the site (draft) or into the archive."""
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db import get_db
from app.models import CAR_DEALS, OFFER_STATUSES, Car, User, can_cars
from app.services import offers as O
from app.services.common import get_or_404
from app.services.errors import DomainError

router = APIRouter(prefix="/offers", tags=["offers"])
Kind = Literal["car"]


class _In(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = None
    price: float | None = Field(None, ge=0)
    currency: Literal["USD", "GEL", "EUR"] | None = None
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    is_exclusive: bool | None = None
    post_url: str | None = None
    agent_id: int | None = None  # operator and up; ignored for an agent


class CarIn(_In):
    deal: Literal[CAR_DEALS] | None = None
    period: Literal["day", "month"] | None = None
    deposit: float | None = Field(None, ge=0)
    year: int | None = Field(None, ge=1950, le=2100)
    gearbox: str | None = None
    fuel: str | None = None
    body: str | None = None
    seats: int | None = Field(None, ge=1, le=60)
    mileage: int | None = Field(None, ge=0)
    pickup: str | None = None
    post_fields: dict | None = None  # the post generator's fields (services/car_post.FIELDS)


class StatusIn(BaseModel):
    status: Literal[OFFER_STATUSES]


class PublishIn(BaseModel):
    text: str | None = Field(None, max_length=4096)  # the caption as edited; empty = built from the fields
    tg: bool = True  # «В Telegram»
    site: bool = True  # «На сайт»
    keys: list[str] | None = None  # the files in the post, in album order; None = all


def _model(kind: str):
    return Car


def _body(kind: str, body: dict, new: bool) -> dict:
    try:
        data = CarIn.model_validate(body).model_dump(exclude_unset=True)
    except ValidationError as e:
        raise DomainError("bad_request", "Проверьте поля: " + ", ".join(str(x["loc"][0]) for x in e.errors()), 422)
    if new and not data.get("title"):
        raise DomainError("bad_request", "Укажите модель", 422)
    if new and not data.get("deal"):
        raise DomainError("bad_request", "Укажите направление", 422)
    if not new and data.get("title", "x") is None:
        data.pop("title")
    return data


def _agent_or_car_operator(user: User = Depends(current_user)) -> User:
    """Superadmin, admin and the «Оператор авто» only (Stanislav 11-16, like Daily Rent; models.can_cars)."""
    if not can_cars(user):
        raise DomainError("forbidden", "cars: admins and the car operator only")
    return user


Agent = Depends(_agent_or_car_operator)
Db = Depends(get_db, scope="function")


@router.get("/{kind}")
def offers_list(kind: Kind, status: Literal[OFFER_STATUSES] | None = None, q: str | None = None,
                limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Db, user: User = Agent):
    return O.listing(db, _model(kind), status, q, limit, offset)


@router.post("/{kind}")
def offers_create(kind: Kind, body: dict, db: Session = Db, user: User = Agent):
    return O.out(O.create(db, _model(kind), _body(kind, body, True), user))


@router.post("/{kind}/preview")
def offers_preview(kind: Kind, body: dict, db: Session = Db, user: User = Agent):
    """The post the generator builds from the form as it is now (nothing saved): the form's live preview."""
    return {"text": O.preview_text(_body(kind, body, False))}


@router.get("/{kind}/{oid}")
def offers_get(kind: Kind, oid: int, db: Session = Db, user: User = Agent):
    return O.out(get_or_404(db, _model(kind), oid, "object"))


@router.patch("/{kind}/{oid}")
def offers_update(kind: Kind, oid: int, body: dict, db: Session = Db, user: User = Agent):
    return O.out(O.update(db, _model(kind), oid, _body(kind, body, False), user))


@router.post("/{kind}/{oid}/status")
def offers_status(kind: Kind, oid: int, body: StatusIn, db: Session = Db, user: User = Agent):
    return O.out(O.set_status(db, _model(kind), oid, body.status, user))


@router.post("/{kind}/{oid}/media")
async def offers_media(kind: Kind, oid: int, files: list[UploadFile] = File(...), db: Session = Db, user: User = Agent):
    payload = [(f.filename or "file", await f.read()) for f in files]
    o, keys = O.upload(db, _model(kind), oid, payload, user)
    return {**O.out(o), "uploaded_keys": keys}  # the UI puts parallel uploads back in the agent's selection order


@router.put("/{kind}/{oid}/media/order")
def offers_media_order(kind: Kind, oid: int, body: dict, db: Session = Db, user: User = Agent):
    return O.out(O.reorder(db, _model(kind), oid, body.get("keys") or [], user))


@router.delete("/{kind}/{oid}/media")
def offers_media_delete(kind: Kind, oid: int, key: str, db: Session = Db, user: User = Agent):
    return O.out(O.delete_file(db, _model(kind), oid, key, user))


@router.get("/{kind}/{oid}/post")
def offers_post(kind: Kind, oid: int, db: Session = Db, user: User = Agent):
    return {"text": O.post_text(db, get_or_404(db, _model(kind), oid, "object"))}


@router.post("/{kind}/{oid}/publish")
def offers_publish(kind: Kind, oid: int, body: PublishIn, db: Session = Db, user: User = Agent):
    return O.out(O.publish(db, _model(kind), oid, user, body.text, body.tg, body.site, body.keys))
