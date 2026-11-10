"""The client catalog cannot include raw parser inventory or resurrect an old unvalidated card."""
import pytest

from app.models import Lead, Property, Reaction, Setting
from app.services import leads, settings
from app.services.errors import DomainError
from tests.conftest import auth, make_client, make_client_listing, make_listing, make_user


def test_client_catalog_requires_agency_or_confirmed_publication(db):
    customer = make_client(db)
    raw = make_listing(db)
    assert not leads.client_listing_eligible(db, raw.id)
    with pytest.raises(DomainError, match="не опубликован"):
        leads.create_from_like(db, customer.id, raw.id)
    assert db.query(Lead).count() == db.query(Reaction).count() == 0
    prop = Property(listing_id=raw.id, status="published")
    db.add(prop)
    db.commit()
    assert not leads.client_listing_eligible(db, raw.id)  # failed Telegram publication is not enough
    prop.post_url, prop.post_message_id = "https://t.me/rent_tbilisi_ge/777", "777"
    db.commit()
    assert leads.client_listing_eligible(db, raw.id)
    assert leads.create_from_like(db, customer.id, raw.id).property_id == prop.id
    for status in ("review", "returned", "rented", "archived"):
        prop.status = status
        db.commit()
        assert not leads.client_listing_eligible(db, raw.id)
    agency = make_client_listing(db)
    assert leads.client_listing_eligible(db, agency.id)
    agency.status = "rejected"  # dismissed by a person
    db.commit()
    assert not leads.client_listing_eligible(db, agency.id)
    for status in ("pending_review", "gone"):  # the source ad changed, the published object and its post are ours
        agency.status = status
        db.commit()
        assert leads.client_listing_eligible(db, agency.id)


@pytest.mark.parametrize("old_status", ["new", "refused"])
def test_old_reaction_or_lead_cannot_bypass_catalog(db, old_status):
    customer, listing = make_client(db), make_client_listing(db)
    lead = leads.create_from_like(db, customer.id, listing.id)
    lead.status = old_status
    listing.source = "myhome.ge"  # old broad-catalog record, no validated property
    db.query(Property).filter_by(listing_id=listing.id).delete()
    db.add(Setting(key="client_catalog", value="listings"))
    db.commit()
    with pytest.raises(DomainError) as error:
        leads.create_from_like(db, customer.id, listing.id)
    assert error.value.code == "listing_unavailable"
    db.refresh(lead)
    assert lead.status == old_status and db.query(Lead).count() == 1
    assert db.query(Reaction).count() == 1


def test_obsolete_catalog_setting_hidden_and_rejected(db, client):
    admin = make_user(db, "superadmin")
    db.add(Setting(key="client_catalog", value="listings"))
    db.commit()
    assert "client_catalog" not in settings.all(db)
    r = client.put("/api/settings", headers=auth(admin), json={"client_catalog": "listings"})
    assert r.status_code == 400 and r.json()["code"] == "bad_setting"


def test_bot_catalog_is_the_crm_objects(db):
    """Stanislav 2026-11-10: the bot searches the CRM's published objects (their catalog rows), never scraped ads."""
    import json
    from sqlalchemy import text
    from app.models import Listing, PropertyMedia
    from app.services import catalog
    customer = make_client(db)
    agent = make_user(db, "agent", "Zaza", agent_tag="Zaza")
    p = Property(status="published", source_type="legacy", district="Vake", address="5 Abashidze St", price=700,
                 bedrooms=2, area=80, floor=4, total_floors=12, agent_id=agent.id, post_url="https://t.me/rent_tbilisi_ge/9",
                 post_message_id="9", post_fields={"prop_type": "Apartment", "building": "NewBuilding", "design": "New",
                                                   "heating": "CentralHeating", "pets": "ByAgreement",
                                                   "periods": ["6Month", "12Month"], "amenities": ["Balcony", "WiFi"]})
    ad = make_listing(db, "ss5001")  # a scraped ad nobody took
    db.add(p)
    db.flush()
    db.add_all([PropertyMedia(property_id=p.id, path="legacy/team-chat/2.jpg", kind="photo", position=1),
                PropertyMedia(property_id=p.id, path="legacy/team-chat/1.heic", kind="photo", position=0),
                PropertyMedia(property_id=p.id, path="legacy/team-chat/3.mp4", kind="video", position=2)])
    db.commit()
    assert catalog.sync_all(db) == 1 and catalog.sync_all(db) == 0  # unchanged rows are not rewritten
    row = db.get(Listing, catalog.catalog_id(p.id))
    d = json.loads(row.details)
    assert (row.source, row.status, row.price_usd, row.bedrooms, row.floor) == ("object", "published", 700, 2, 4)
    assert json.loads(row.photos) == ["s3:legacy/team-chat/1.heic", "s3:legacy/team-chat/2.jpg"]
    assert d["videos"] == [{"key": "legacy/team-chat/3.mp4"}] and d["floors"] == 12
    assert d["address_en"] == "5 Abashidze St"
    assert (d["building"], d["heating"], d["pets"], d["terms"]) == ("new", "central", "by_agreement", [6, 12])
    search = db.execute(text("SELECT l.id FROM listings l WHERE " + _bot_rule("CLIENT_SEARCH"))).scalars().all()
    assert search == [row.id]  # the scraped ad is not in the bot's search
    assert not leads.client_listing_eligible(db, ad.id)
    lead = leads.create_from_like(db, customer.id, row.id)
    assert lead.property_id == p.id
    p.status = "archived"
    db.commit()
    assert not leads.client_listing_eligible(db, row.id)  # at once, before the sync
    assert catalog.sync_all(db) == 1 and db.get(Listing, row.id).status == "gone"
    assert not db.execute(text("SELECT l.id FROM listings l WHERE " + _bot_rule("CLIENT_SEARCH"))).all()
    p.status = "published"
    db.query(PropertyMedia).filter_by(property_id=p.id, kind="photo").delete()
    db.commit()
    assert catalog.sync_all(db) == 1 and db.get(Listing, row.id).status == "gone"  # no photo: no client card
    assert not leads.client_listing_eligible(db, row.id)


def _bot_rule(name):
    """bot/core.py's SQL rule (the bot's own process can't be imported here: it needs its llm/geo stack)."""
    import ast
    from tests.conftest import BACKEND
    tree = ast.parse((BACKEND.parent / "bot" / "core.py").read_text())
    env = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) in ("CLIENT_SEARCH", "CLIENT_ELIGIBLE"):
            env[node.targets[0].id] = eval(compile(ast.Expression(node.value), "core.py", "eval"), {}, env)
    return env[name]
