"""The contract generator: the owner's cadastral code by his personal ID from the CRM's own records (registry.known) and
from an uploaded registry extract."""
from app.models import Contract, ContractSigner, Event, Property
from app.services.common import now
from tests.conftest import auth, make_user

PDF_PAGE = "<html><body><p>x</p></body></html>"


def test_known_by_personal_id(db, client):
    ag = make_user(db, "agent")
    old = Contract(kind="rent", lang="GEO", html=PDF_PAGE, created_by=ag.id, cadastral="01.14.12.003.045",
                   data={"owner_personal_number": "010 0101 2345", "cadastral_code": "01.14.12.003.045",
                         "street_house": "Chavchavadze 5", "floor": "4", "apartment_number": "12", "apartment_area": "65",
                         "owner_full_name": "Giorgi Beridze"})
    other = Contract(kind="rent", lang="GEO", html=PDF_PAGE, created_by=ag.id, data={"owner_personal_number": "99999999999",
                                                                                      "cadastral_code": "01.10.10.010.010"})
    db.add_all([old, other])
    p = Property(status="published", cadastral_code="01.15.11.001.002.01.501", address="Pekini 12", floor=7)
    db.add(p)
    db.flush()
    db.add(Event(entity="property", entity_id=str(p.id), action="exclusive_contract_signed", actor_label="x",
                 data={"personal_id": "01001012345", "cadastral_code": "01.15.11.001.002.01.501", "full_name": "Giorgi B."}, at=now()))
    signed = Contract(kind="rent", lang="RU", html=PDF_PAGE, created_by=ag.id, cadastral="01.14.12.003.045", data={})
    db.add(signed)
    db.flush()
    db.add(ContractSigner(contract_id=signed.id, role="owner", token="t" * 30, doc_sha256="h", personal_id="01001012345",
                          full_name="Giorgi Beridze", signed_at=now()))
    db.commit()

    r = client.get("/api/registry/known?personal_id=01001012345", headers=auth(ag))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    codes = [i["cadastral"] for i in items]
    assert sorted(codes) == ["01.14.12.003.045", "01.15.11.001.002.01.501"]  # one row per code, not the other owner's
    flat = next(i for i in items if i["cadastral"] == "01.14.12.003.045")
    assert flat["address"] and flat["owner_name"]
    excl = next(i for i in items if i["cadastral"].startswith("01.15"))
    assert excl["address"] == "Pekini 12" and excl["floor"] == "7" and "эксклюзив" in excl["source"]
    assert client.get("/api/registry/known?personal_id=123", headers=auth(ag)).status_code == 422
    assert client.get("/api/registry/known?personal_id=11111111111", headers=auth(ag)).json()["items"] == []
    assert client.get("/api/registry/known?personal_id=01001012345").status_code == 401


def test_extract_upload(db, client, monkeypatch):
    from app.services import registry
    ag = make_user(db, "agent")
    monkeypatch.setattr(registry, "text_of_pdf", lambda pdf: FLAT_PAGE)
    r = client.post("/api/registry/extract-code", files={"file": ("e.pdf", b"%PDF-1.4 x", "application/pdf")}, headers=auth(ag))
    assert r.json()["cadastral_code"] == "01.14.12.003.045.01.512" and r.json()["fields"]["floor"] == "4"
    assert client.post("/api/registry/extract-code", files={"file": ("e.pdf", b"nope", "application/pdf")}, headers=auth(ag)).status_code == 400


def test_code_from_pasted_registry_page(db, client):
    ag = make_user(db, "agent")
    page = ("საჯარო რეესტრის ეროვნული სააგენტო\\nგანცხადება № 882013116537\\nნაკვეთის საკადასტრო კოდი: 01.14.12.003.045\\n"
            "უძრავი ნივთის საკადასტრო კოდი 01.14.12.003.045.01.512 ფართი 65.30 კვ.მ.\\nთარიღი 19.03.2013 10:15")
    r = client.post("/api/registry/parse-text", json={"text": page}, headers=auth(ag))
    assert r.json()["cadastral_code"] == "01.14.12.003.045.01.512"  # the flat's, not the plot's; not the date
    assert client.post("/api/registry/parse-text", json={"text": "nothing here 19.03.2013"}, headers=auth(ag)).json() == {"cadastral_code": None, "fields": {}}
    assert client.post("/api/registry/parse-text", json={"text": "x"}).status_code == 401


# an old NAPR extract (EMS QuickPdf, legacy «Latin-mapped» Georgian font), names and numbers made up
LEGACY_PLOT = """*882013116537*
ÌÉßÉÓ (ÖÞÒÀÅÉ ØÏÍÄÁÉÓ) ÓÀÊÀÃÀÓÔÒÏ ÊÏÃÉ N 66.56.06.005
ÀÌÏÍÀßÄÒÉ ÓÀãÀÒÏ ÒÄÄÓÔÒÉÃÀÍ
N 882013116537    -  19/03/2013 16:59:20
ÌÉÓÀÌÀÒÈÉ: ÒÀÉÏÍÉ ÂÏÒÉ , ÓÏ×ÄËÉ ÓÀÌÄ , N 3
ÃÀÆÖÓÔÄÁÖËÉ ×ÀÒÈÏÁÉ:4745.00 ÊÅ.Ì.
ÌÄÓÀÊÖÈÒÄÄÁÉ:
ÂÉÏÒÂÉ ÔÄÓÔÏÅÉ ,P/N: 01001099999
"""

# a flat, as the NAPR application page reads when copied (Unicode)
FLAT_PAGE = """საჯარო რეესტრის ეროვნული სააგენტო
ნაკვეთის საკადასტრო კოდი: 01.14.12.003.045
უძრავი ნივთის საკადასტრო კოდი: 01.14.12.003.045.01.512
მისამართი: ქალაქი თბილისი, ვაკის რაიონი, ჭავჭავაძის გამზირი, N 5, სართული 4, ბინა N 12
ნაკვეთის ფართობი: 1200.00 კვ.მ.
ბინის ფართი: 65.30 კვ.მ.
მესაკუთრე:
ანა ტესტი ,P/N: 01001088888
"""


def test_parse_extract_fields():
    from app.services import registry as R
    plot = R.parse_extract(LEGACY_PLOT)
    assert plot["cadastral_code"] == "66.56.06.005"
    assert plot["address"].startswith("რაიონი გორი") and plot["owner_full_name"] == "გიორგი ტესტოვი"
    assert plot["owner_personal_number"] == "01001099999"
    assert "apartment_area" not in plot  # the land plot's 4745 m² is not a flat's area
    flat = R.parse_extract(FLAT_PAGE)
    assert flat == {
        "cadastral_code": "01.14.12.003.045.01.512",
        "address": "ქალაქი თბილისი, ვაკის რაიონი, ჭავჭავაძის გამზირი, N 5, სართული 4, ბინა N 12",
        "street_house": "ჭავჭავაძის გამზირი, N 5", "floor": "4", "apartment_number": "12", "apartment_area": "65.30",
        "owner_full_name": "ანა ტესტი", "owner_personal_number": "01001088888",
    }
