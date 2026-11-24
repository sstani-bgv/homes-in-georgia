"""Irakli 11-24: a short address in the post, the channel's own metro tags (#STUniversity = State University)."""
from app.services import properties as P


def test_short_address_drops_the_geocoder_tail():
    full = "Biota Park, 20, Anna Politkovskaya Street, Saburtalo, Vazha-Pshavela Blocks, Vake District, Tbilisi, 0168"
    assert P.short_address(full, "Сабуртало") == "Biota Park · Anna Politkovskaya St 20"
    assert P.short_address("27 Tashkenti St") == "27 Tashkenti St"
    assert P.short_address("ул. Чавчавадзе 33, Ваке") == "ул. Чавчавадзе 33, Ваке"


def test_state_university_is_st_and_technical_is_tc():
    assert P.METRO_NAME["State University"] == "STUniversity" and P.METRO_NAME["Technical University"] == "TCUniversity"
    assert P.nearest_metro(41.7228, 44.7186) == "STUniversity"  # Universiteti station, Saburtalo's end
    assert P.nearest_metro(41.7204, 44.7766) == "TCUniversity"  # Technical University, Kostava / Pekini
