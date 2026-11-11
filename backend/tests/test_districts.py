"""The channel's district list (Irakli 11-11): old values resolve, the caption tag, the frontend copy is the same list."""
import re
from pathlib import Path

from app import districts
from app.services.properties import district_tag


def test_old_values_map_to_tags():
    for old, tag in [("Вере", "Vera"), ("Ваке-Багеби", "Bagebi"), ("Ваке-Сабуртало", "Vake"), ("Saburtalo", "Saburtalo"),
                     ("#DidiDigomi", "DidiDigomi"), ("Chughureti", "Chugureti"), ("Ваке", "Vake"), ("Цхнети", "Tskhneti"),
                     ("Tskneti", "Tskneti"), ("Дигоми 1–9", "Digomi1to9"), ("ვაკე", "Vake")]:
        assert districts.tag(old) == tag, old
    assert districts.tag("Старый город") is None and districts.label("Старый город") == "Старый город"  # kept as is
    assert districts.label("Вере") == "Вера" and districts.label("Saburtalo") == "Сабуртало"
    assert districts.find("Old Tbilisi") is None and districts.find("Tbilisi") is None  # not #Lisi
    assert len({d[0] for d in districts.DISTRICTS}) == len({d[1] for d in districts.DISTRICTS}) == 39


def test_caption_tag():
    assert district_tag("Сабуртало") == "#Saburtalo" and district_tag("Вере") == "#Vera"
    assert district_tag("Нижняя Поничала") == "#QvemoPonichala" and district_tag("Ваке-Багеби") == "#Bagebi"
    assert district_tag(None) == "#Tbilisi"


def test_frontend_list_is_the_same():
    ts = (Path(__file__).parents[2] / "frontend/src/lib/format.ts").read_text()
    rows = re.findall(r"\['(\w+)', '([^']+)', '([^']+)'\]", ts.split("DISTRICT_LIST")[1].split("];")[0])
    assert rows == [(t, ru, g) for t, ru, _, _, g in districts.DISTRICTS]
