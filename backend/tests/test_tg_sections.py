"""David / Stanislav 11-23: posts go to the channel of their kind — flats / houses for rent → the agency channel, sale →
@sale_in_tbilisi, commercial → @homesingeorgia, cars (channel unknown yet) → the agency channel (Управление → Настройки
publish_chat_<kind>); the bot not admin there → the agency channel and a
warning on the object card. Telegram is mocked (tg.call)."""
from types import SimpleNamespace

import pytest

from app.models import Property
from app.services import properties
from app.telegram import client as tg
from app.telegram import notify, store

MAIN = "-1001832464267"
REAL_NOTIFY = dict(vars(notify))  # module import time: before the autouse `sent` fixture stubs notify.*


@pytest.fixture
def bot(monkeypatch):
    """tg.call: getChatMember answers `admins` (chat -> member) else refuses; every sent message recorded."""
    for k, v in REAL_NOTIFY.items():
        if callable(v) and not k.startswith("__"):
            monkeypatch.setattr(notify, k, v)
    monkeypatch.setenv("PUBLISH_CHAT_ID", MAIN)
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setattr(notify, "storage", SimpleNamespace(fetch=lambda key, tmp: None))
    st = SimpleNamespace(admins={}, sent=[], settings={})
    monkeypatch.setattr(store, "setting", lambda k: st.settings.get(k))

    def call(method, files=None, **p):
        if method == "getChatMember":
            assert p["user_id"] == "123"
            return st.admins.get(p["chat_id"])
        st.sent.append((method, p.get("chat_id")))
        return {"message_id": 5, "chat": {"id": -100, "username": str(p.get("chat_id")).lstrip("@")}}
    monkeypatch.setattr(tg, "call", call)
    monkeypatch.setattr(tg, "send", lambda chat, text, *a, **kw: call("sendMessage", chat_id=chat))
    return st


ADMIN = {"status": "administrator", "can_post_messages": True, "can_delete_messages": True}


def _post(kind):
    return SimpleNamespace(id=None, post_channel=kind, post_text="x", post_media_keys=[], owner_id=None)


def test_kinds_route_to_their_channels(bot):
    bot.admins["@homesingeorgia"] = bot.admins["@sale_in_tbilisi"] = ADMIN
    assert notify.publish_post(_post("rent"))[2:] == (MAIN, [5], None)
    assert notify.publish_post(_post("cars"))[2:] == (MAIN, [5], None)  # an empty setting: the agency channel
    for kind, chat in (("sale", "sale_in_tbilisi"), ("commercial", "homesingeorgia")):
        ref = notify.publish_post(_post(kind))
        assert ref[1] == f"https://t.me/{chat}/5" and ref[2] == f"@{chat}" and ref[4] is None
    bot.settings["publish_chat_sale"] = ""  # cleared by hand in Настройки: the agency channel, not the default
    assert notify.publish_post(_post("sale"))[2] == MAIN
    bot.settings["publish_chat_cars"] = "@cars_tbilisi"
    bot.admins["@cars_tbilisi"] = ADMIN
    assert notify.publish_post(_post("cars"))[2] == "@cars_tbilisi"


def test_bot_not_admin_falls_back_to_the_main_channel_with_a_warning(bot):
    ref = notify.publish_post(_post("sale"))  # getChatMember refused: «member list is inaccessible»
    assert ref[2] == MAIN and "@sale_in_tbilisi" in ref[4] and bot.sent == [("sendMessage", MAIN)]
    bot.admins["@sale_in_tbilisi"] = {"status": "administrator", "can_post_messages": True, "can_delete_messages": False}
    assert notify.publish_post(_post("sale"))[2] == MAIN  # no delete right: unpost could not take it down later


def test_channel_kind():
    p = lambda **f: Property(post_fields=f)  # noqa: E731
    assert properties.channel_kind(p()) == "rent"
    assert properties.channel_kind(p(deal="Rent", prop_type="House")) == "rent"
    assert properties.channel_kind(p(deal="Sale", prop_type="Apartment")) == "sale"
    assert properties.channel_kind(p(deal="Rent", prop_type="Commercial")) == "commercial"
    assert properties.channel_kind(p(prop_type="Warehouses")) == "commercial"


def test_publish_records_the_channel_and_the_warning(db, client, monkeypatch):
    from tests.test_publish_card import _submitted
    seen = []

    def publish_post(prop):
        seen.append(prop.post_channel)
        return 7, "https://t.me/rent_tbilisi_ge/7", MAIN, [7], "Бот не админ в @homesingeorgia"
    monkeypatch.setattr(notify, "publish_post", publish_post)
    _, nino, pid, _ = _submitted(db, client)
    p = db.get(Property, pid)
    p.post_fields = {**(p.post_fields or {}), "deal": "Sale"}
    db.commit()
    properties.publish(db, pid, nino)
    assert seen == ["sale"]
    pub = db.query(properties.PropertyPublication).filter_by(property_id=pid).one()
    assert pub.messages == {"chat": MAIN, "ids": [7], "warning": "Бот не админ в @homesingeorgia"}
    assert properties.card(db, db.get(Property, pid), nino)["publications"][0]["warning"] == "Бот не админ в @homesingeorgia"


# Irakli 11-23: «HIG Original Photos» — the photos without the watermark after a publication, once per photo set


def test_originals_post_photos_then_text_then_video(bot, monkeypatch, tmp_path):
    big, small = tmp_path / "big.mp4", tmp_path / "small.mp4"
    small.write_bytes(b"v")
    with open(big, "wb") as f:
        f.truncate(notify.BOT_UPLOAD_MAX + 1)
    rows = [{"kind": "photo", "path": f"k{n}"} for n in range(12)] + [{"kind": "video", "path": "small"}, {"kind": "video", "path": "big"}]
    monkeypatch.setattr(store, "q", lambda sql, args: rows)
    paths = {"small": str(small), "big": str(big)}
    monkeypatch.setattr(notify, "storage", SimpleNamespace(fetch=lambda key, tmp: paths.get(key, f"/tmp/{key}.jpg")))
    texts = []
    monkeypatch.setattr(tg, "send", lambda chat, text, *a, **kw: texts.append(text) or bot.sent.append(("sendMessage", chat)) or {"message_id": 1})
    from app.telegram import userbot
    via = []
    monkeypatch.setattr(userbot, "send_media", lambda chat, key, caption=None: via.append((chat, key)) or 77)
    assert notify.originals_post(5, "-4001", "🏠 x") == 14  # the big video: through the operator account (11-24)
    assert bot.sent == [("sendMediaGroup", "-4001"), ("sendMediaGroup", "-4001"), ("sendMessage", "-4001"), ("sendDocument", "-4001")]
    assert texts == ["🏠 x"] and via == [("-4001", "big")]
    monkeypatch.setattr(userbot, "send_media", lambda chat, key, caption=None: None)  # the account couldn't: a note
    texts.clear()
    assert notify.originals_post(5, "-4001", "🏠 x") == 13 and "больше 50 МБ" in texts[-1]


def test_originals_once_per_photo_set(db, client, monkeypatch):
    from app import worker
    from app.models import PropertyMedia
    from app.services import settings
    from tests.test_publish_card import _submitted
    sent = []
    monkeypatch.setattr(notify, "publish_post", lambda prop: (7, "https://t.me/rent_tbilisi_ge/7", MAIN, [7]))
    monkeypatch.setattr(notify, "originals_post", lambda pid, chat, text: sent.append((pid, chat, text)) or 2)
    monkeypatch.setattr(notify, "post_html", REAL_NOTIFY["post_html"])
    _, nino, pid, _ = _submitted(db, client)
    properties.publish(db, pid, nino)
    worker.tick()
    assert sent == []  # no chat set yet
    settings.set(db, "originals_chat_id", "-4001")
    properties.publish(db, pid, nino)
    worker.tick()
    assert len(sent) == 1 and sent[0][1] == "-4001" and f"CRM #{pid}" in sent[0][2] and "t.me/rent_tbilisi_ge/7" in sent[0][2]
    assert sent[0][2].startswith(notify.post_html(properties.post_text(db, db.get(Property, pid)), pid)[:40])
    properties.publish(db, pid, nino)  # a repost, the same photos: nothing
    worker.tick()
    assert len(sent) == 1
    db.add(PropertyMedia(property_id=pid, kind="photo", path="new.jpg", position=99))
    db.commit()
    properties.publish(db, pid, nino)  # a photo added: again
    worker.tick()
    assert len(sent) == 2
