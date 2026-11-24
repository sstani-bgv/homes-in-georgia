"""Irakli 11-24: a short address in the post, the channel's own metro tags (#STUniversity = State University)."""
from app.services import properties as P


def test_short_address_drops_the_geocoder_tail():
    full = "Biota Park, 20, Anna Politkovskaya Street, Saburtalo, Vazha-Pshavela Blocks, Vake District, Tbilisi, 0168"
    assert P.short_address(full, "Сабуртало") == "Biota Park · Anna Politkovskaya St 20"
    assert P.short_address("27 Tashkenti St") == "27 Tashkenti St"
    # David 11-24: all English — #15577 went out as «Vake-Saburtalo · ცაგარელის ქ. 21»
    assert P.short_address("Vake-Saburtalo, Saburtalo, ცაგარელის ქ. 21", "Сабуртало") == "Tsagareli St 21"
    assert P.short_address("ул. Чавчавадзе 33, Ваке") == "Chavchavadze St 33"
    assert P.short_address("ჭავჭავაძის გამზ. 5, Vake") == "Chavchavadze Ave 5"


def test_state_university_is_st_and_technical_is_tc():
    assert P.METRO_NAME["State University"] == "STUniversity" and P.METRO_NAME["Technical University"] == "TCUniversity"
    assert P.nearest_metro(41.7228, 44.7186) == "STUniversity"  # Universiteti station, Saburtalo's end
    assert P.nearest_metro(41.7204, 44.7766) == "TCUniversity"  # Technical University, Kostava / Pekini
