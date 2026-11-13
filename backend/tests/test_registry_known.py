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


# the Batumi extract's layout (pypdf text of a 2023 NAPR extract), names and numbers made up: the address wraps, the plot's
# «დაზუსტებული ფართობი» comes first, the flat's area sits in the ownership table on lines of its own
NEW_EXTRACT = """*882023000001* მიწის (უძრავი ქონების) საკადასტრო კოდი N 05.36.24.120.02.01.510
ამონაწერი საჯარო რეესტრიდან
მისამართი: ქალაქი ბათუმი , წმინდა ანდრია
პირველწოდებულის III ჩიხი N12
ნაკვეთის საკუთრების ტიპი: თანასაკუთრება
დაზუსტებული ფართობი:2500.00  კვ.მ.
მესაკუთრეები:
ნინო ტესტი ,P/N: 01001077777
მესაკუთრე: საკუთრების ტიპი: საკუთრება: ფართი: წილი:
ნინო ტესტი საკუთრება ფართი(მშენებარე) სართული11,ბინა
N76,ბლოკი ''ა"
30.40
კვ.მ.
იპოთეკა
"""


def test_parse_new_extract():
    from app.services import registry as R
    assert R.parse_extract(NEW_EXTRACT) == {
        "cadastral_code": "05.36.24.120.02.01.510",
        "address": "ქალაქი ბათუმი , წმინდა ანდრია პირველწოდებულის III ჩიხი N12",
        "street_house": "წმინდა ანდრია პირველწოდებულის III ჩიხი N12", "floor": "11", "apartment_number": "76",
        "apartment_area": "30.40", "owner_full_name": "ნინო ტესტი", "owner_personal_number": "01001077777",
    }


def test_extract_url_check(monkeypatch):
    import pytest
    from app.services import registry as R
    from app.services.errors import DomainError
    monkeypatch.setattr(R.socket, "getaddrinfo", lambda h, *a, **k: [(0, 0, 0, "", ("10.0.0.5" if h == "int.napr.gov.ge" else "91.239.207.10", 443))])
    assert str(R._check_url("http://bs.napr.gov.ge/GetBlob?pid=101&bid=x")) == "https://bs.napr.gov.ge/GetBlob?pid=101&bid=x"
    for bad in ("https://evil.com/?napr.gov.ge", "https://napr.gov.ge.evil.com/", "https://int.napr.gov.ge/",
                "https://bs.napr.gov.ge:8443/", "file:///etc/passwd", "https://127.0.0.1/", ""):
        with pytest.raises(DomainError):
            R._check_url(bad)


def test_extract_from_url_saved_with_contract(db, client, monkeypatch):
    from app import storage
    from app.services import registry as R
    from tests.test_contracts import _lead
    ag, ag2 = make_user(db, "agent"), make_user(db, "agent")
    lead_a, lead_b = _lead(db, ag), _lead(db, ag2)
    pdf = b"%PDF-1.4 fake extract"
    monkeypatch.setattr(R, "download_extract", lambda url: pdf)
    monkeypatch.setattr(R, "text_of_pdf", lambda b: NEW_EXTRACT if b == pdf else None)
    url = "https://bs.napr.gov.ge/GetBlob?pid=101&bid=abc"
    r = client.post("/api/registry/extract-from-url", json={"url": url}, headers=auth(ag))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["fields"]["owner_personal_number"] == "01001077777" and out["url"] == url
    assert R.EXTRACT_KEY_RE.match(out["file"]) and storage.read(out["file"]) == pdf
    c = client.post("/api/contracts/draft", json={"lead_id": lead_a.id, "kind": "rent", "data": {"extract_url": url, "extract_file": out["file"]}},
                    headers=auth(ag)).json()
    assert c["data"]["extract_url"] == url
    got = client.get(f"/api/contracts/{c['id']}/extract", headers=auth(ag))
    assert got.status_code == 200 and got.content == pdf
    # data is the wizard's: a key outside the extracts (another private file) is never served
    other = client.post("/api/contracts/draft", json={"lead_id": lead_b.id, "kind": "rent", "data": {"extract_file": "private/properties/1/x.pdf"}},
                        headers=auth(ag2)).json()
    assert client.get(f"/api/contracts/{other['id']}/extract", headers=auth(ag2)).status_code == 404
    assert client.post("/api/registry/extract-from-url", json={"url": url}).status_code == 401
