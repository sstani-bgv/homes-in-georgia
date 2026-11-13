from datetime import timedelta

from app.models import ChannelPost, Deal, Property
from app.services import analytics
from app.services.common import now
from tests.conftest import make_user


def test_agent_activity_score(db):
    assert analytics.activity_score(30, 40, 2) == 52  # the user's example: 20 + 20 + 12
    assert analytics.activity_score(200, 200, 20) == 100  # each part capped at its target
    ag = make_user(db, "agent")
    old = analytics.ACTIVITY_START - timedelta(days=1)  # before the new CRM start → never counted
    db.add_all([Property(agent_id=ag.id, status="new", source_type="feed"),  # new + active
                Property(agent_id=ag.id, status="new"),  # manual (no source_type): new + active
                Property(agent_id=ag.id, status="archived", source_type="feed"),  # new, not active
                Property(agent_id=ag.id, status="new", source_type="legacy"),  # old-CRM import: ignored
                Property(agent_id=ag.id, status="new", source_type="feed", created_at=old),
                ChannelPost(message_id="1", agent_id=ag.id, date=now()),  # channel posts don't count
                Deal(agent_id=ag.id, status="confirmed", confirmed_at=now()),
                Deal(agent_id=ag.id, status="confirmed", confirmed_at=now(), source="import"),
                Deal(agent_id=ag.id, status="confirmed", confirmed_at=old),
                Deal(agent_id=ag.id, status="pending")])
    db.commit()
    a = next(r for r in analytics.agents(db) if r["id"] == ag.id)["activity"]
    assert (a["new"], a["active"], a["deals"]) == (3, 2, 1)
    assert a["score"] == analytics.activity_score(3, 2, 1) and a["targets"]["new"] == 60 and a["days"] == 30
    if now() - timedelta(days=30) < analytics.ACTIVITY_START:
        assert a["since"] == "2026-11-13"
