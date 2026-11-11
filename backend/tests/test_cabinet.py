from datetime import timedelta

from app.models import Deal, Property
from app.services.common import TZ, now
from tests.conftest import auth, make_user


def test_cabinet_monthly_stats_and_access(db, client):
    ag, other, op = make_user(db, "agent"), make_user(db, "agent"), make_user(db, "operator")
    t = now().astimezone(TZ)
    mo = f"{t.month}/{t.year}"
    db.add_all([
        Deal(agent_id=ag.id, deal_type="rent", month=mo, status="confirmed", commission_total=500, platform_amount=250,
             agent_amount=250),
        Deal(agent_id=ag.id, deal_type="sale", month=mo, status="pending", commission_total=100, platform_amount=50,
             agent_amount=50),
        Deal(agent_id=ag.id, deal_type="rent", month=mo, status="cancelled", agent_amount=999),
        Deal(agent_id=other.id, recruit_id=ag.id, deal_type="rent", month=mo, status="confirmed", agent_amount=300,
             recruit_amount=40, platform_amount=60),
        Property(agent_id=ag.id, address="A"),
        Property(agent_id=ag.id, address="B", status="rented", rent_to=t.date() + timedelta(days=10)),
        Property(agent_id=other.id, address="C", rent_to=t.date() + timedelta(days=10)),
    ])
    db.commit()

    cab = client.get("/api/me/cabinet", headers=auth(ag)).json()
    cur = cab["monthly"][0]
    assert len(cab["monthly"]) == 12 and cur["month"] == mo
    assert (cur["deals"], cur["rentals"], cur["earned"], cur["platform"], cur["commission"], cur["added"]) == \
        (2, 1, 340, 300, 600, 2)
    assert [x["address"] for x in cab["expiring_leases"]] == ["B"]

    # an agent never opens another agent's cabinet; staff do
    assert client.get(f"/api/me/cabinet?user_id={other.id}", headers=auth(ag)).status_code == 403
    assert client.get(f"/api/me/cabinet?user_id={ag.id}", headers=auth(ag)).status_code == 200
    staff = client.get(f"/api/me/cabinet?user_id={ag.id}", headers=auth(op)).json()
    assert staff["user"]["id"] == ag.id and staff["monthly"][0]["earned"] == 340
    assert client.get("/api/me/cabinet?user_id=999999", headers=auth(op)).status_code == 404
