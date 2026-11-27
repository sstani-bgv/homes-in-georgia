"""Preparing an object (Stanislav 2026-11-11): cadastral code format, 9 photos + 1 video, rooms optional."""
import pytest

from app.models import Property
from app.services import properties, settings
from app.services.common import clean_cadastral
from app.services.errors import DomainError
from tests.conftest import auth, make_listing, make_user


@pytest.mark.parametrize("raw,clean", [
    ("01.14.12.003.045", "01.14.12.003.045"),
    ("01.10.10.02.20.0.01.01.84A", "01.10.10.02.20.0.01.01.84A"),
    ("01.10.13.015.029.01.150ა", "01.10.13.015.029.01.150ა"),
    (" 01,14 12..003.045. ", "01.14.12.003.045"),
    ("N01.14.02.025.242.01.01.285", "01.14.02.025.242.01.01.285"),
    ("01.14.11.020.001.01.088 а", "01.14.11.020.001.01.088а"),
    ("01.13.06.008", "01.13.06.008"),
    ("01.14.12.003.045/2", "01.14.12.003.045/2"),
    ("", None), (None, None),
])
def test_cadastral_ok(raw, clean):
    assert clean_cadastral(raw) == clean


@pytest.mark.parametrize("raw", ["Test", "?", "1", "01.", "2010-10-01", "17 Tashkenti Street", "01.14.12",
                                 "01.10.14.005.009.01.011\t03.09.2025", "01.14.11.037.012.01.01.035 | 01.14.11.037"])
def test_cadastral_garbage(raw):
    with pytest.raises(DomainError) as e:
        clean_cadastral(raw)
    assert e.value.code == "bad_cadastral" and e.value.status == 422 and "01.14.12.003.045" in e.value.message


def _object(db, lid="ss901"):
    ag = make_user(db, "agent")
    p = properties.take(db, make_listing(db, lid).id, ag)
    properties.call_result(db, p.id, ag, True)
    properties.owner_or_agent(db, p.id, ag, True, "Нана", "599 11 22 33")
    return ag, p


def test_cadastral_checked_only_when_changed(db, client):
    ag, p = _object(db)
    p.cadastral_code = "старый код из таблицы"  # a legacy value: saving other fields keeps working
    db.commit()
    r = client.patch(f"/api/properties/{p.id}", headers=auth(ag), json={"cadastral_code": "старый код из таблицы", "area": 50})
    assert r.status_code == 200, r.text
    r = client.patch(f"/api/properties/{p.id}", headers=auth(ag), json={"cadastral_code": "мусор"})
    assert r.status_code == 422 and r.json()["code"] == "bad_cadastral"
    r = client.patch(f"/api/properties/{p.id}", headers=auth(ag), json={"cadastral_code": "01 14 12 003 045"})
    assert r.status_code == 200 and r.json()["cadastral_code"] == "01.14.12.003.045"


def test_deal_form_cadastral(db, client):
    ag = make_user(db, "agent")
    body = {"agent_id": ag.id, "price": 800, "rent_from": "2030-01-01", "address": "Пекини 1"}
    r = client.post("/api/deals", headers=auth(ag), json={**body, "cadastral": "нет"})
    assert r.status_code == 422 and r.json()["code"] == "bad_cadastral"
    r = client.post("/api/deals", headers=auth(ag), json={**body, "cadastral": "01,14,12,003,045"})
    assert r.status_code == 200 and r.json()["cadastral"] == "01.14.12.003.045"


def test_media_limit_40_photos_1_video(db):
    ag, p = _object(db, "ss902")
    properties.upload_media(db, p.id, [(f"{i}.jpg", b"\xff\xd8x") for i in range(40)], None, ag)
    properties.upload_media(db, p.id, [("v.mp4", b"\0\0video")], None, ag)
    for extra in ("41.jpg", "v2.mov"):
        with pytest.raises(DomainError) as e:
            properties.upload_media(db, p.id, [(extra, b"\xff\xd8x")], None, ag)
        assert e.value.code == "media_limit" and e.value.status == 422
    # a batch that overflows adds nothing
    m = properties.media(db, p.id)
    properties.delete_media(db, p.id, m[0].id, ag)
    with pytest.raises(DomainError):
        properties.upload_media(db, p.id, [("a.jpg", b"\xff\xd8x"), ("b.jpg", b"\xff\xd8x")], None, ag)
    assert len(properties.media(db, p.id)) == 40


def test_upload_lost_the_last_slot_to_a_parallel_one_leaves_no_files(db, monkeypatch):
    """Files go to the storage before the row lock (3 parallel uploads don't queue, Irakli 11-18); the limit is checked
    again under the lock, and the loser's files are deleted."""
    from pathlib import Path
    from app.config import settings as env
    ag, p = _object(db, "ss903")
    properties.upload_media(db, p.id, [("v.mp4", b"\0\0video")], None, ag)
    m = properties.media(db, p.id)
    properties.delete_media(db, p.id, m[0].id, ag)
    real, calls = properties._media_check, []

    def race(db_, p_, actor, kinds):
        calls.append(1)
        if len(calls) == 2:  # under the lock: a parallel upload has taken the video slot meanwhile
            properties.upload_media(db_, p_.id, [("other.mp4", b"\0\0v2")], None, actor)
        return real(db_, p_, actor, kinds)
    monkeypatch.setattr(properties, "_media_check", race)
    with pytest.raises(DomainError) as e:
        properties.upload_media(db, p.id, [("v.mp4", b"\0\0video")], None, ag)
    assert e.value.code == "media_limit"
    left = [f.name for f in Path(env.MEDIA_DIR, "properties", str(p.id)).iterdir()]
    assert len(left) == 1 and [x.path for x in properties.media(db, p.id)] == [f"properties/{p.id}/{left[0]}"]


def test_min_9_photos_to_review_and_publish(db, monkeypatch):
    monkeypatch.setattr(properties, "MIN_PHOTOS", 9)
    ag, p = _object(db, "ss904")
    properties.upload_media(db, p.id, [(f"{i}.jpg", b"\xff\xd8x") for i in range(8)] + [("v.mp4", b"\0\0video")], None, ag)
    with pytest.raises(DomainError) as e:
        properties.require_min_photos(db, p)
    assert e.value.code == "few_photos" and "минимум 9 фото, сейчас 8" in e.value.message
    properties.upload_media(db, p.id, [("9.jpg", b"\xff\xd8x")], None, ag)
    properties.require_min_photos(db, p)  # 9 photos: ok


def test_album_first_9_photos_and_the_video():
    from app.telegram.notify import _album as album
    items = [("photo", n) for n in range(15)] + [("video", "v")]
    assert album(items) == [("photo", n) for n in range(9)] + [("video", "v")]
    assert album([("video", "v")] + items[:12]) == [("video", "v")] + items[:9]
    assert album(items[:3]) == items[:3]


def test_rooms_optional_and_switchable(db):
    ag, p = _object(db, "ss903")
    properties.upload_media(db, p.id, [("a.jpg", b"\xff\xd8x")], None, ag)  # no room
    p = db.get(Property, p.id)
    assert not any("комнат" in x for x in properties.missing_fields(p))
    assert properties.out(db, p, ag, full=True)["room_picker"] is True
    settings.set(db, "room_picker", "0")
    assert properties.out(db, p, ag, full=True)["room_picker"] is False



def test_photo_rooms_editable_and_bathroom_renamed(db):
    """David 11-23: new room tags, «Ванная» → «Санузел» (old tags kept), the list edited in Управление → Настройки."""
    ag, p = _object(db, "ss905")
    properties.upload_media(db, p.id, [("a.jpg", b"\xff\xd8x")], "Ванная", ag)
    p = db.get(Property, p.id)
    p.checklist = {"Ванная": True}
    db.commit()
    o = properties.out(db, p, ag, full=True)
    assert {"Санузел", "Прихожая", "Фасад", "Гардероб", "Терраса", "Кабинет"} <= set(o["photo_rooms"])
    assert "Ванная" not in o["photo_rooms"]
    assert o["media"][0]["room"] == "Санузел" and o["checklist"] == {"Санузел": True}
    settings.set(db, "photo_rooms", "Кухня\n  \nМансарда\n")
    assert properties.out(db, p, ag, full=True)["photo_rooms"] == ["Кухня", "Мансарда"]
    m = properties.set_media_room(db, p.id, o["media"][0]["id"], "  Постирочная " + "x" * 80, ag)  # an agent's own tag
    assert m.room.startswith("Постирочная x") and len(m.room) == 60
    assert "Постирочная" not in settings.get(db, "photo_rooms")

def test_media_order_saved_and_rooms_photo_only(db, client):
    ag, p = _object(db, "ss904")
    properties.upload_media(db, p.id, [("a.jpg", b"\xff\xd8x"), ("b.jpg", b"\xff\xd8x")], "Кухня", ag)
    properties.upload_media(db, p.id, [("v.mp4", b"\0v")], "Кухня", ag)
    a, b, v = properties.media(db, p.id)
    assert (a.room, v.room) == ("Кухня", None)  # a video never gets a room
    assert properties.set_media_room(db, p.id, v.id, "Спальня 1", ag).room is None
    r = client.put(f"/api/properties/{p.id}/media/order", headers=auth(ag), json={"ids": [v.id, b.id, a.id]})
    assert r.status_code == 200, r.text
    assert [m["id"] for m in r.json()["media"]] == [v.id, b.id, a.id]
    db.expire_all()
    assert [m.id for m in properties.media(db, p.id)] == [v.id, b.id, a.id]  # the album (notify.media_paths) reads position
    r = client.put(f"/api/properties/{p.id}/media/order", headers=auth(ag), json={"ids": [a.id, b.id]})
    assert r.status_code == 409 and r.json()["code"] == "bad_order"
    other = make_user(db, "agent")
    assert client.put(f"/api/properties/{p.id}/media/order", headers=auth(other), json={"ids": [a.id, b.id, v.id]}).status_code == 403
    r = client.post(f"/api/properties/{p.id}/media", headers=auth(ag), files={"files": ("c.jpg", b"\xff\xd8x", "image/jpeg")})
    assert r.status_code == 200, r.text
    assert r.json()["uploaded_ids"] == [r.json()["media"][-1]["id"]]  # the UI maps parallel uploads to the selection order


def test_big_video_in_parts_survives_a_dropped_part(db, client):
    """Irakli 11-18: an iPhone .MOV never got in (one 2-3 minute request died midway). The CRM sends it in parts: a retried
    part overwrites itself, a lost one is reported with where to go on from, the last part stores the video."""
    from pathlib import Path
    from app.config import settings as env
    ag, p = _object(db, "ss904")
    video = bytes(range(256)) * 400  # 100 KB
    uid, url, h = "a" * 32, f"/api/properties/{p.id}/media/part", auth(ag)

    def part(off, end, **kw):
        return client.post(url, headers=h, files={"file": ("IMG_0412.MOV", video[off:end], "video/quicktime")},
                           data={"upload_id": uid, "name": "IMG_0412.MOV", "offset": off, "total": len(video), **kw})
    assert part(0, 40_000).json() == {"received": 40_000}
    assert part(0, 40_000).json() == {"received": 40_000}  # the response was lost: the same part again
    r = part(80_000, len(video))  # a part went missing
    assert r.status_code == 409 and r.json()["code"] == "upload_gap" and r.json()["data"] == {"have": 40_000}
    assert part(40_000, 80_000).json() == {"received": 80_000}
    r = part(80_000, len(video)).json()
    assert [m["kind"] for m in r["media"]] == ["video"] and r["uploaded_ids"]
    m = properties.media(db, p.id)[0]
    assert (Path(env.MEDIA_DIR) / m.path).read_bytes() == video and not list((Path(env.MEDIA_DIR) / "uploads").iterdir())
    # the limits before the bytes travel: a second video is refused on its first part
    r = client.post(url, headers=h, files={"file": ("b.mov", b"x", "video/quicktime")},
                    data={"upload_id": "b" * 32, "name": "b.mov", "offset": 0, "total": 10})
    assert r.status_code == 422 and r.json()["code"] == "media_limit"
    r = client.post(url, headers=h, files={"file": ("c.mov", b"x", "video/quicktime")},
                    data={"upload_id": "../../etc", "name": "c.mov", "offset": 0, "total": 1})
    assert r.json()["code"] == "bad_upload"


def test_staff_add_photos_to_a_published_object(db, client):
    """Irakli 11-27: on a published object staff could delete a photo in the gallery but not add one."""
    ag, p = _object(db, "ss907")
    properties.upload_media(db, p.id, [("a.jpg", b"\xff\xd8x")], None, ag)
    p.status = "published"
    db.commit()
    op = make_user(db, "operator")
    file = {"files": ("b.jpg", b"\xff\xd8x", "image/jpeg")}
    assert client.post(f"/api/properties/{p.id}/media", headers=auth(ag), files=file).status_code == 409
    r = client.post(f"/api/properties/{p.id}/media", headers=auth(op), files=file)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert len(properties.media(db, p.id)) == 2 and db.get(Property, p.id).status == "published"
