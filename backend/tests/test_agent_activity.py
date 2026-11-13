from datetime import timedelta

from app.models import ChannelPost, Deal, Property
from app.services import analytics
from app.services.common import now
from tests.conftest import make_user


def test_agent_activity_score(db):
    assert analytics.activity_score(30, 40, 2) == 52  # the user's example: 20 + 20 + 12
    assert analytics.activity_score(200, 200, 20) == 100  # each part capped at its target
    ag = make_user(db, "agent")
    old = now() - timedelta(days=40)
    linked = Property(agent_id=ag.id, status="published")  # created now, has a post → counted once via the post
    db.add_all([linked, Property(agent_id=ag.id, status="new"), Property(agent_id=ag.id, status="archived")])
    db.flush()
    db.add_all([ChannelPost(message_id="1", agent_id=ag.id, date=now(), property_id=linked.id),
                ChannelPost(message_id="2", agent_id=ag.id, date=now()), ChannelPost(message_id="3", agent_id=ag.id, date=old),
                Deal(agent_id=ag.id, status="confirmed", confirmed_at=now()),
                Deal(agent_id=ag.id, status="confirmed", confirmed_at=old),
                Deal(agent_id=ag.id, status="pending")])
    db.commit()
    a = next(r for r in analytics.agents(db) if r["id"] == ag.id)["activity"]
    # new: 2 posts + 2 CRM objects without a post (new + archived); active: 2; deals: 1
    assert (a["new"], a["active"], a["deals"]) == (4, 2, 1)
    assert a["score"] == analytics.activity_score(4, 2, 1) and a["targets"]["new"] == 60 and a["days"] == 30
