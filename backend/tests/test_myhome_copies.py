"""Копии объекта на myhome (services/myhome_copies.py, David 11-22): the search keeps the object's district / street and
floor ±1; the ads become «copy» listings (a live parser ad untouched) and «Похожие» shows a copy with the object's photos.
No network: search() and the photo download are faked."""
import json

from app.models import Listing
from app.services import myhome_copies as M
from app.services import similar
from tests.conftest import make_listing, make_user
from tests.test_similar import H, _object

_KR_SEARCH = M.kr_search  # the real one: conftest stubs it in every test


def ad(i, **kw):
    return {"id": i, "address": "რამიშვილი ნ. ქ. ", "area": 56, "floor": 8, "bedroom": "1", "room": "2", "urban_name": "Vake",
            "urban_id": 38, "street_id": 1212, "price": {"2": {"price_total": 800}}, "comment": "ქირავდება<br />ბინა",
            "images": [{"large": f"https://img/{i}/{n}.webp"} for n in range(3)], "dynamic_slug": "x", **kw}


def test_candidates_keep_district_street_and_floor(monkeypatch):
    from types import SimpleNamespace as N
    calls = []

    def fake(query, pause=1.0):
        calls.append(query)
        if query.startswith("streets="):
            return [ad(3), ad(4, floor=2)]
        return [ad(1), ad(2, urban_name="Saburtalo", urban_id=47, street_id=9, address="Kazbegi 3")]
    monkeypatch.setattr(M, "search", fake)
    p = N(id=1, rooms=None, bedrooms=1, area=57.0, floor=8, district="Ваке", address="Ramishvili 3")
    got = sorted(i["id"] for i in M.candidates(p, pause=0))
    assert got == [1, 3]  # #2 another district and street, #4 the 2nd floor
    assert calls[0] == "room_types=2&area_from=50&area_to=64" and "urbans=38&" in calls[1] and "streets=1212&" in calls[2]


def test_copies_saved_and_shown_in_similar(db, client, monkeypatch):
    vika = make_user(db, "agent", "Вика")
    pid = _object(db, client, vika)
    make_listing(db, "mh5", source="myhome.ge", status="published")  # the parser's own live ad: left alone
    monkeypatch.setattr(M, "_hashes", lambda urls: list(H[:3]))  # the agent re-posted the owner's photos
    assert M.save(db, [ad(7, area=70, floor=4, bedroom="2"), ad(5)]) == 1  # the object: 70 m², 4th floor, 2 bedrooms
    l = db.get(Listing, "mh7")
    assert l.status == "copy" and l.district == "Ваке" and l.price_usd == 800 and l.text == "ქირავდება\nბინა"
    assert db.get(Listing, "mh5").status == "published"
    res = similar.find(db, f"p:{pid}", vika)
    x = next((c for c in res if c["key"] == "l:mh7"), None)
    assert x is not None and x["level"] in ("same", "maybe") and any("общих фото" in r for r in x["reasons"])


def ss_ad(i, **kw):
    a = {"subdistrictId": 5, "subdistrictTitle": "Vake", "streetId": 77, "streetTitle": "Nino Ramishvili St.", "streetNumber": None}
    return {"applicationId": i, "detailUrl": f"x-{i}", "totalArea": 57, "floorNumber": "8", "numberOfBedrooms": 1,
            "price": {"priceUsd": 800}, "title": "ბინა", "description": "ქირავდება", "address": {**a, **kw.pop("address", {})},
            "appImages": [{"fileName": f"https://ss/{i}/{n}_Thumb.jpg"} for n in range(3)], **kw}


def test_ss_candidates_find_the_district_and_street_ids_then_search_them(monkeypatch):
    from types import SimpleNamespace as N
    calls = []

    def fake(queries, pages=3):
        calls.append(queries)
        if len(calls) == 1:  # the city by the area
            return [ss_ad(1), ss_ad(2, address={"subdistrictId": 9, "subdistrictTitle": "Saburtalo", "streetId": 8,
                                                "streetTitle": "Kazbegi"})]
        return [ss_ad(3, floorNumber="2"), ss_ad(4, numberOfBedrooms=2), ss_ad(5)]
    monkeypatch.setattr(M, "ss_search", fake)
    p = N(id=1, bedrooms=1, area=57.0, floor=8, district="Ваке", address="Ramishvili 3")
    rows = M.ss_candidates(p)
    assert sorted(r["id"] for r in rows) == ["ss1", "ss5"]  # 2: Saburtalo; 3: 2nd floor; 4: 2 bedrooms
    assert calls[1] == [{"areaFrom": 50, "areaTo": 64, "subdistrictIds": [5]}, {"areaFrom": 50, "areaTo": 64, "streetIds": [77]}]
    r = next(r for r in rows if r["id"] == "ss1")
    assert r["photos"][0] == "https://ss/1/0.jpg" and r["floor"] == 8 and r["district"] == "Ваке" and r["source"] == "ss.ge"


def test_compare_by_link(db, client, monkeypatch):
    from tests.conftest import auth
    vika = make_user(db, "agent", "Вика")
    pid = _object(db, client, vika)
    row = {"id": "ss991", "source": "ss.ge", "url": "https://home.ss.ge/ka/udzravi-qoneba/x-991", "photos": ["https://ss/1.jpg"] * 3,
           "address": "Tsatskhvebi 4", "area": 75.0, "floor": 5, "bedrooms": 2, "district": None, "price_usd": 900, "text": "x"}
    monkeypatch.setattr(M, "by_link", lambda url: row)
    monkeypatch.setattr(M, "_hashes", lambda urls: list(H[:3]))  # the owner's photos
    r = client.post(f"/api/properties/{pid}/compare-link", json={"url": "https://home.ss.ge/ka/udzravi-qoneba/x-991"}, headers=auth(vika))
    assert r.status_code == 200, r.text
    assert r.json()["listing_id"] == "ss991" and r.json()["pct"] >= 65 and "общих фото: 3" in r.json()["matched"]
    assert db.get(Listing, "ss991").status == "copy" and r.json()["linked"]
    monkeypatch.undo()
    r = client.post(f"/api/properties/{pid}/compare-link", json={"url": "https://example.com/1"}, headers=auth(vika))
    assert r.status_code == 400 and "myhome" in r.json()["detail"]


def test_linked_owner_ad_finds_the_copies_of_its_photos(db, client, monkeypatch):
    """David 11-23: our shoot shares no photo with the agents' copies; the owner's ad pasted by link does — it stays on
    the card and its photos lead «Похожие» to every copy of them, a duplicate search is queued."""
    from app.models import Job, PropertyListing
    from tests.conftest import auth
    vika = make_user(db, "agent", "Вика")
    pid = _object(db, client, vika)
    G = [0x7777000077770000, 0x3C3C3C3C3C3C3C3C, 0x6A6A6A6A01010101]  # the owner's photos: none of ours (H)
    monkeypatch.setattr(M, "_hashes", lambda urls: list(G))
    M.save(db, [ad(31, area=70, floor=4, bedroom="2")])  # an agent's copy with the owner's photos

    def copy():
        return next((c for c in similar.find(db, f"p:{pid}", vika) if c["key"] == "l:mh31"), {"reasons": []})
    assert not any("общих фото" in r for r in copy()["reasons"])
    row = {"id": "mh30", "source": "myhome.ge", "url": "https://www.myhome.ge/pr/30/x", "photos": ["https://img/30.jpg"] * 3,
           "address": "x", "area": 70.0, "floor": 4, "bedrooms": 2, "district": None, "price_usd": 900, "text": "x"}
    monkeypatch.setattr(M, "by_link", lambda url: row)
    r = client.post(f"/api/properties/{pid}/compare-link", json={"url": "https://www.myhome.ge/pr/30/"}, headers=auth(vika))
    assert r.status_code == 200 and r.json()["linked"], r.text
    assert db.query(PropertyListing).filter_by(property_id=pid, listing_id="mh30").count() == 1
    assert any(x["listing_id"] == "mh30" for x in client.get(f"/api/properties/{pid}", headers=auth(vika)).json()["listings"])
    assert db.query(Job).filter_by(kind="dup_scan").count() == 1
    assert any("общих фото" in r for r in copy()["reasons"])


def test_link_forms(monkeypatch):
    """myhome's new slug links (David 11-23: «Нужна ссылка…» on /ka/udzravi-qoneba/…-26740808/) and the old /pr/ ones."""
    import pytest
    monkeypatch.setattr(M, "_mh_detail", lambda i: i)
    for u in ("https://www.myhome.ge/ka/udzravi-qoneba/qiravdeba-2-otaxiani-axali-ashenebuli-bina-vakeshi-26740808/",
              "https://www.myhome.ge/udzravi-qoneba/qiravdeba-2-otaxiani-bina-vakeshi-26740808",
              "https://www.myhome.ge/en/pr/26740808/flat-for-rent", "myhome.ge/pr/26740808?x=1",
              "https://livo.ge/en/udzravi-qoneba/for-rent-apartment/2-room-flat-in-vake-26740808"):  # livo: myhome's ad
        assert M.by_link(u) == "26740808", u
    monkeypatch.setattr(M, "_kr_detail", lambda link, district=None: link)
    assert M.by_link("https://korter.ge/en/apartments-for-rent-tbilisi/park-home-saburtalo/1074541?x=1") == \
        "/en/apartments-for-rent-tbilisi/park-home-saburtalo/1074541"
    with pytest.raises(ValueError):
        M.by_link("https://www.myhome.ge/ka/s/")


def test_copies_are_searched_by_the_linked_ad_too(db, client, monkeypatch):
    """David 11-23 (call, 14:36): «по ссылке составил карточку объекта и начал искать такие же» — the owner's ad on
    another street / area than our card is searched by its own address and area as well (myhome and ss.ge)."""
    from app.models import Property, PropertyListing
    vika = make_user(db, "agent", "Вика")
    pid = _object(db, client, vika)
    p = db.get(Property, pid)
    p.address, p.area, p.floor = "Ateni 16", 55, 5
    make_listing(db, "mh8801", source="myhome", address="Ramishvili 3", area=57.0, floor=8, status="copy")
    make_listing(db, "mh8802", source="myhome", address="Ateni 16", area=55.0, floor=5, status="copy")  # same as the card
    db.add_all([PropertyListing(property_id=pid, listing_id="mh8801", source="myhome"),
                PropertyListing(property_id=pid, listing_id="mh8802", source="myhome")])
    db.commit()
    seen, ss_seen = [], []
    monkeypatch.setattr(M, "candidates", lambda c, pause=1.0: seen.append((c.address, float(c.area), c.floor)) or [])
    monkeypatch.setattr(M, "ss_candidates", lambda c: ss_seen.append(c.address) or [])
    M.run(db, pid)
    # the object first, the owner's ad by its own card, the ad matching the card not twice
    assert seen[:2] == [("Ateni 16", 55.0, 5), ("Ramishvili 3", 57.0, 8)] and ss_seen[:2] == ["Ateni 16", "Ramishvili 3"]
    assert [x[0] for x in seen].count("Ateni 16") == 1 and len(seen) <= 1 + M.LINK_CARDS


def test_korter_candidates_keep_district_street_bedrooms_closest_first(monkeypatch):
    """David 11-23: «MyHome, SS, Livo, Korter» — Korter's own base searched by district path, area and floor."""
    from types import SimpleNamespace as N
    calls, opened = [], []

    def item(i, **kw):
        return {"objectId": i, "link": f"/en/x/{i}", "section": "rent", "area": 57, "roomCount": 2,
                "subLocalityNominative": "Vake", "address": "Nino Ramishvili St, 3", **kw}
    monkeypatch.setattr(M, "kr_search", lambda slug, q, pause=0.5: calls.append((slug, q)) or [
        item(1, area=63), item(2), item(3, subLocalityNominative="Saburtalo", address="Kazbegi Ave, 3"),
        item(4, roomCount=3), item(5, subLocalityNominative="Saburtalo")])  # 5: Ramishvili filed under Saburtalo
    monkeypatch.setattr(M, "_kr_detail", lambda link, district=None: opened.append(link) or {"id": link})
    p = N(area=57.0, floor=8, bedrooms=1, district="Ваке", address="Ramishvili 3")
    assert [r["id"] for r in M.kr_candidates(p, pause=0)] == ["/en/x/2", "/en/x/5", "/en/x/1"]  # 3: other place; 4: 2 bedrooms
    assert calls == [("vake", "min_area=50&max_area=64&min_floor_number=7&max_floor_number=9")]


def test_korter_search_falls_back_to_the_city(monkeypatch):
    pages = {"/en/apartments-for-rent-tbilisi?min_area=1&page=1": '<script>window.INITIAL_STATE = {"apartmentListingStore": '
             '{"apartments": [{"objectId": 9, "section": "rent"}, {"objectId": 8, "section": "sale"}]}}</script>'}
    monkeypatch.setattr(M, "_kr_get", lambda path: pages.get(path))
    assert [i["objectId"] for i in _KR_SEARCH("lisi", "min_area=1", pause=0)] == [9]


def test_copies_by_the_link_photos_with_a_second_round(monkeypatch):
    """Stanislav 11-23: the link's photos against the thumbnails of a wide search; a copy leading with a shot the link
    lacks is caught through another copy's photos."""
    A, B, C = 0x0F0F0F0F0F0F0F0F, 0x123456789ABCDEF0, 0x5555AAAA5555AAAA
    h = {"a1": A ^ 1, "a2": A, "b": B, "b2": B ^ 2, "c": C}
    monkeypatch.setattr(M, "_hashes", lambda urls: [h[u] for u in urls[:M.PHOTOS]])
    rows = [{"id": "mh1", "thumbs": ["a1"], "photos": ["a2", "b"]},     # shares the link's photo
            {"id": "mh2", "thumbs": ["b2"], "photos": ["b2"]},          # only mh1's other photo: round two
            {"id": "mh3", "thumbs": ["c"], "photos": ["c"]}]            # another flat
    assert [r["id"] for r in M.by_photos(rows, [A])] == ["mh1", "mh2"]
    assert M.by_photos(rows, []) == []


def test_wide_search_learns_the_street_from_the_district(monkeypatch):
    """#15509 11-23: the city search of 120 had no ad on Ramishvili, so only Vake's newest were searched (0 copies).
    The street's id comes from the district's ads too."""
    from types import SimpleNamespace
    seen = []

    def fake(q, pause=1.0):
        seen.append(q)
        if q.startswith("urbans=38"):
            return [{"id": 5, "urban_id": 38, "urban_name": "Vake", "street_id": 1212, "address": "რამიშვილი ნ. ქ. 3"}]
        if q.startswith("streets=1212"):
            return [{"id": 6, "urban_id": 38, "urban_name": "Vake", "street_id": 1212, "address": "Ramishvili St 3"}]
        return [{"id": 4, "urban_id": 38, "urban_name": "Vake", "street_id": 77, "address": "Chavchavadze Ave 1"}]
    monkeypatch.setattr(M, "search", fake)
    c = SimpleNamespace(address="რამიშვილი ნ. ქ. ", district="Ваке", area=56, floor=8, rooms=None, bedrooms=1)
    assert {r["id"] for r in M.wide_mh(c, 0)} == {"mh4", "mh5", "mh6"}
    assert seen[1:] == ["urbans=38&room_types=1,2,3", "streets=1212&room_types=1,2,3"]
