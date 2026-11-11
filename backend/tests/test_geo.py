"""Address field: OSM suggestions (HTTP mocked), pasted Google Maps points, the point saved on the object."""
import httpx

from app.models import Property
from app.services import geo, properties
from tests.conftest import auth, make_user

HIT = {"display_name": "37, Ilia Chavchavadze Avenue, Vake, Vake District, Tbilisi, 0179, Georgia", "lat": "41.7096",
       "lon": "44.7612", "address": {"suburb": "Vake", "city_district": "Vake District"}}


def test_search_osm_cached_and_points(client, db, monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, **kw):
        calls.append((url, params, headers))
        if "goo.gl" in url:  # a short share link redirects to the full place URL
            return httpx.Response(200, request=httpx.Request("GET", "https://www.google.com/maps/place/X/@41.70,44.70,17z/data=!3d41.7151!4d44.8271"))
        return httpx.Response(200, json=HIT if "reverse" in url else [HIT], request=httpx.Request("GET", url))

    monkeypatch.setattr(geo.httpx, "get", fake_get)
    monkeypatch.setattr(geo.time, "sleep", lambda s: None)
    geo._nominatim.cache_clear()
    geo._reverse.cache_clear()
    ag = make_user(db, "agent")
    assert client.get("/api/geo/search?q=Chavchavadze 37").status_code == 401
    r = client.get("/api/geo/search?q=Chavchavadze  37", headers=auth(ag)).json()
    assert r["items"] == [{"label": "37, Ilia Chavchavadze Avenue, Vake, Vake District, Tbilisi, 0179", "lat": 41.7096,
                           "lng": 44.7612, "district": "Ваке"}] and r["point"] is False
    url, params, headers = calls[0]
    assert "nominatim" in url and params["countrycodes"] == "ge" and params["limit"] == 5 and "User-Agent" in headers
    client.get("/api/geo/search?q=Chavchavadze 37", headers=auth(ag))
    assert len(calls) == 1  # cached
    assert client.get("/api/geo/search?q=ab", headers=auth(ag)).json()["items"] == []

    # pasted points: the pin (!3d!4d) wins over the view (@), «lat, lng», a short link; OSM only names them (/reverse)
    for q, at in [("https://www.google.com/maps/place/Vake/@41.70,44.70,15z/data=!3d41.7096!4d44.7612", (41.7096, 44.7612)),
                  ("41.7151, 44.8271", (41.7151, 44.8271)),
                  ("https://maps.google.com/?q=41.7001,44.7502", (41.7001, 44.7502)),
                  ("https://maps.app.goo.gl/abc123", (41.7151, 44.8271))]:
        r = client.get("/api/geo/search", params={"q": q}, headers=auth(ag)).json()
        assert r["point"] and (r["items"][0]["lat"], r["items"][0]["lng"]) == at, q
        assert r["items"][0]["label"].startswith("37, Ilia") and r["items"][0]["district"] == "Ваке"  # named by /reverse
    assert geo.point_of("44.8271, 41.7151") is None  # swapped: not in Georgia
    assert sum("/search" in c[0] for c in calls) == 1


def test_osm_error_not_cached(client, db, monkeypatch):
    n = []

    def boom(*a, **kw):
        n.append(1)
        raise httpx.ConnectError("down")

    monkeypatch.setattr(geo.httpx, "get", boom)
    monkeypatch.setattr(geo.time, "sleep", lambda s: None)
    geo._nominatim.cache_clear()
    ag = make_user(db, "agent")
    for _ in range(2):
        assert client.get("/api/geo/search?q=Pekini 12", headers=auth(ag)).json() == {"items": [], "point": False, "error": True}
    assert len(n) == 2


def test_point_saved_on_object_and_used_by_post(client, db):
    ag = make_user(db, "agent")
    r = client.post("/api/properties", json={"address": "Chavchavadze 37", "lat": 41.7096, "lng": 44.7612,
                                              "owner_name": "Нино", "owner_phone": "+995 555 11 22 33"}, headers=auth(ag)).json()
    assert (r["lat"], r["lng"]) == (41.7096, 44.7612)
    r = client.patch(f"/api/properties/{r['id']}", json={"lat": 41.71, "lng": 44.76}, headers=auth(ag)).json()
    assert (r["lat"], r["lng"]) == (41.71, 44.76)
    p = db.get(Property, r["id"])
    assert properties.maps_url(db, p) == "https://maps.google.com/?q=41.71,44.76"
    r = client.patch(f"/api/properties/{p.id}", json={"lat": None, "lng": None}, headers=auth(ag)).json()
    assert r["lat"] is None


def test_point_facts_district_and_metro(client, db, monkeypatch):
    rev = {"Vake District": {"city_district": "Vake District", "suburb": "Vake"},
           "Bagebi": {"city_district": "Vake District", "suburb": "Bagebi"},
           "Saburtalo": {"city_district": "Saburtalo District", "suburb": "Nutsubidze Plateau"},
           "Old": {"city_district": "Mtatsminda District", "suburb": "Sololaki"}}
    pick = []

    def fake_get(url, params=None, **kw):
        return httpx.Response(200, json={"display_name": "x", "lat": params["lat"], "lon": params["lon"], "address": rev[pick[-1]]},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(geo.httpx, "get", fake_get)
    monkeypatch.setattr(geo.time, "sleep", lambda s: None)
    geo._reverse.cache_clear()
    ag = make_user(db, "agent")
    for name, (lat, lng), district, metro in [
            ("Old", (41.7036, 44.7900), "Старый город", "Rustaveli"),  # next to Rustaveli station
            ("Bagebi", (41.7120, 44.7320), "Ваке-Багеби", "VazhaPshavela"),  # ~1.3 km from Vazha-Pshavela
            ("Vake District", (41.7090, 44.7550), "Ваке", "Delisi"),  # Vake Park: ~2 km, the nearest still fills (11-11)
            ("Saburtalo", (41.7240, 44.7310), "Сабуртало", "VazhaPshavela")]:
        pick.append(name)
        r = client.get("/api/geo/point", params={"lat": lat, "lng": lng}, headers=auth(ag)).json()
        assert (r["district"], r["metro"]) == (district, metro), name
        assert (r["metro_m"] is None) == (metro is None) and (r["metro_m"] or 0) <= 8000
    pick.append("Old")  # clearly outside Tbilisi (>8 km from any station): no metro rather than a wrong one
    r = client.get("/api/geo/point", params={"lat": 41.85, "lng": 44.95}, headers=auth(ag)).json()
    assert (r["metro"], r["metro_m"]) == (None, None)
    assert client.get("/api/geo/point?lat=10&lng=10", headers=auth(ag)).status_code == 422
