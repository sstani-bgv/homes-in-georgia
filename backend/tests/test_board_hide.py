"""Stanislav 11-11: «Убрать с доски» for published / rented cards; the objects keep their status."""
from app.models import Property
from app.services import properties
from tests.conftest import auth, make_listing, make_user


def test_board_hide_published_keeps_status_and_comes_back_on_next_step(db, client):
    ag, other, op = make_user(db, "agent"), make_user(db, "agent"), make_user(db, "operator")
    mine, theirs = (properties.take(db, make_listing(db, lid).id, a) for lid, a in (("ss1", ag), ("ss2", other)))
    mine.status = theirs.status = "published"
    db.commit()
    board = lambda u: [x["id"] for x in client.get("/api/properties/board", headers=auth(u)).json()["published"]]
    assert client.post("/api/properties/board/hide", json={"status": "review"}, headers=auth(ag)).status_code >= 400
    assert client.post("/api/properties/board/hide", json={"status": "published"}, headers=auth(ag)).json() == {"hidden": 1}
    assert board(op) == [theirs.id]  # an agent hides only his own
    db.refresh(mine)
    assert mine.status == "published"  # still in the channel, the bot and the lists
    assert client.post("/api/properties/board/hide", json={"status": "published"}, headers=auth(op)).json() == {"hidden": 1}
    assert board(op) == []
    properties._status(db, db.get(Property, mine.id), "rented", ag)
    db.commit()
    assert [x["id"] for x in client.get("/api/properties/board", headers=auth(op)).json()["rented"]] == [mine.id]
