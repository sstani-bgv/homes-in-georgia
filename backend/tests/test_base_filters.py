"""Stanislav 11-13: the base tabs got the feed's filter block (source, districts, metro, bedrooms, price, archive reason)."""
from app.models import Property
from tests.conftest import auth, make_user


def test_base_filters_and_facets(db, client):
    op = make_user(db, "operator")
    rows = [Property(source_type="legacy", district="Ваке", bedrooms=1, price=500, post_fields={"metro": "Rustaveli"}),
            Property(source_type="direct", district="Сабуртало", bedrooms=4, price=900, post_fields={}),
            Property(source_type="direct", district="Ваке", bedrooms=0, price=1500, post_fields={"metro": "Delisi"},
                     status="archived", archive_reason="duplicate")]
    db.add_all(rows)
    db.commit()
    a, b, c = (p.id for p in rows)
    ids = lambda qs: sorted(x["id"] for x in client.get(f"/api/properties?{qs}", headers=auth(op)).json()["items"])
    assert ids("district=Ваке&district=Сабуртало") == [a, b, c]
    assert ids("district=Ваке&source=direct") == [c]
    assert ids("metro=Rustaveli") == [a]
    assert ids("bedrooms=0,4") == [b, c]  # studio and 4+
    assert ids("price_min=600&price_max=1000") == [b]
    assert ids("status=archived&archive_reason=duplicate") == [c]
    f = client.get("/api/properties/facets?status=archived", headers=auth(op)).json()
    assert f == {"sources": [{"value": "direct", "count": 1}], "districts": [{"value": "Ваке", "count": 1}],
                 "metros": [{"value": "Delisi", "count": 1}]}
