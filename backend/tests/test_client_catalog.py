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
    for status in ("pending_review", "rejected", "gone"):
        agency.status = status
        db.commit()
        assert not leads.client_listing_eligible(db, agency.id)


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
