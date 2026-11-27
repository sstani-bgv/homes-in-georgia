"""Irakli 11-27: required fields follow the object type (a commercial 18 m² was blocked by «спальни, дом, дизайн…»)."""
from app.models import Property
from app.services import properties


def _missing(prop_type, deal="Sale"):
    return set(properties.missing_fields(Property(post_fields={"deal": deal, "prop_type": prop_type})))


def test_missing_fields_by_type():
    card = {"район", "адрес", "цена", "площадь", "собственник"}
    assert _missing("Apartment", "Rent") == card | {"спальни", "этаж", "этажей в доме", "дом", "дизайн", "отопление",
                                                    "животные", "сроки аренды"}
    assert _missing(None) == card | {"спальни", "этаж", "этажей в доме", "тип", "дом", "дизайн", "отопление"}
    assert _missing("Commercial") == _missing("Cafe", "Rent") == card | {"этаж"}
    assert _missing("Garage") == _missing("Land", "Rent") == card


def test_post_has_no_bedrooms_for_garage(db):
    p = Property(bedrooms=0, area=18, post_fields={"deal": "Sale", "prop_type": "Garage"})
    text = properties.post_text(db, p)
    assert "#Studio" not in text and "#Garage for #Sale" in text
