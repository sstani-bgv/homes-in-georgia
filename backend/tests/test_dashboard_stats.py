"""Stanislav 11-11: «Статистика» on «Главная» — an agent counts only his own, staff the whole agency."""
from datetime import timedelta

from app.models import Deal, Lead
from app.services import properties
from app.services.common import TZ, now, now_text
from tests.conftest import auth, make_client, make_listing, make_user


def test_stats_scoped_by_role(db, client):
    ag, other, op = make_user(db, "agent"), make_user(db, "agent"), make_user(db, "operator")
    mine, theirs = (properties.take(db, make_listing(db, lid).id, a) for lid, a in (("ss1", ag), ("ss2", other)))
    theirs.status, theirs.published_at = "published", properties.now()
    make_listing(db, "ss3")  # still in the feed: shared, not scoped
    c, t = make_client(db), now_text()
    for i, a in enumerate((ag, other)):
        db.add(Lead(client_id=c.id, listing_id=f"ss{i + 1}", agent_id=a.id, status="agent_assigned", created_at=t, updated_at=t,
                    viewing_at=now()))
        db.add(Deal(agent_id=a.id, status="confirmed", month=f"{now().astimezone(TZ).month}/{now().astimezone(TZ).year}",
                    rent_to=now().astimezone(TZ).date() + timedelta(days=10)))
    db.commit()
    get = lambda u: client.get("/api/analytics/dashboard", headers=auth(u)).json()["stats"]
    s, o = get(ag), get(op)
    assert s["scope"] == "mine" and o["scope"] == "agency"
    assert (s["objects"]["work"], s["objects"]["published"]) == (1, 0)
    assert (o["objects"]["work"], o["objects"]["published"]) == (1, 1)
    assert s["objects"]["feed_today"] == o["objects"]["feed_today"] == 1
    assert (s["leads"]["new"], s["leads"]["viewings_today"]) == (1, 1)
    assert o["leads"]["viewings_today"] == 2
    assert (s["deals"]["month"], o["deals"]["month"]) == (1, 2)
    assert (s["contracts_expiring"], o["contracts_expiring"]) == (1, 2)
