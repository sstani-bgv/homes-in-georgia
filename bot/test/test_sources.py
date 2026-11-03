"""livo.ge and korter.ge: page parsing (fetch_livo.py, fetch_korter.py) and ingest conversion. No network: the pages
below are cut down to the shapes the sites serve (Next.js flight data on Livo, window.INITIAL_STATE on Korter)."""
import json
import os
os.environ["ENV_FILE"] = os.devnull
import unittest
from unittest import mock

import fetch_korter
import fetch_livo
import ingest

LIVO_ITEM = {"id": 26284875, "metro_station_id": 12, "deal_type_id": 2, "real_estate_type_id": 1, "user_id": 671160,
             "price": {"1": {"price_total": 1620}, "2": {"price_total": 600}, "3": {"price_total": 515}},
             "area": 60, "floor": 2, "total_floors": 8, "bedroom": "1", "room": "2", "district_name": "Gldani-Nadzaladevi",
             "urban_name": "Nadzaladevi", "address": "ჭყონდიდელის ქ. 103", "comment": "Agents please do not call!<br />Near metro",
             "images": [{"large": "https://static-api-statements.tnet.ge/a.webp"}, {"large": "https://static-api-statements.tnet.ge/b.webp"}],
             "middle_slug": "for-rent-apartment", "dynamic_slug": "2-room-apartment-for-rent-in-nadzaladevi", "parameters": []}
SALE = {**LIVO_ITEM, "id": 1, "deal_type_id": 1}


def livo_page(*items):
    payload = 'x:["$","div",null,{"statements":' + json.dumps(list(items), separators=(",", ":")) + '}]'  # compact, as served
    half = len(payload) // 2  # the stream is split over several pushes, escaped as JS strings
    push = lambda s: '<script>self.__next_f.push([1,' + json.dumps(s) + '])</script>'
    return "<html>" + push(payload[:half]) + push(payload[half:]) + "</html>"


KORTER_ITEM = {"objectId": 1014837, "userId": 129885, "price": 1150, "currency": "USD", "area": 51, "roomCount": 2,
               "section": "rent", "floorNumbers": [1], "address": "Permtserta St, 149 (Block D)", "subLocalityNominative": "Vake",
               "link": "/en/apartments-for-rent-tbilisi/modern-city-lisi/1014837", "house": {"floorCount": 4},
               "mediaSrc": {"default": {"x1": "https://storage.googleapis.com/bd-ge-01/realty/400x315/1.jpg"}},
               "building": {"position": {"lat": 41.74, "lng": 44.74}}}


def korter_page(state):
    return ('<html><script>window.serverUrl = "x";\n window.INITIAL_STATE = ' + json.dumps(state) +
            ';\n window.lang = "en";</script></html>')


KORTER_DETAIL = korter_page({"layoutLandingStore": {
    "layout": {"description": "A brand-new apartment near Lisi lake", "bedroomCount": 1, "bathroomCount": 1,
               "hasBalcony": True, "hasTerrace": False, "videoLink": None,
               "images": [{"mediaSrc": {"default": {"x1": "https://storage.googleapis.com/bd-ge-01/realty/1280x960/2.jpg"}}},
                          {"mediaSrc": {"default": {"x1": "https://storage.googleapis.com/bd-ge-01/realty/1280x960/3.jpg"}}}]},
    "seller": {"sellerType": "owner", "name": "Test Owner", "phones": [{"displayNumber": "+995 500 00 00 00"}]}}})


class Livo(unittest.TestCase):
    def test_statements_from_flight_data(self):
        items = fetch_livo.statements(livo_page(LIVO_ITEM, SALE))
        self.assertEqual([i["id"] for i in items], [26284875, 1])

    def test_conversion_reuses_myhome_shape(self):
        it = ingest.from_livo(LIVO_ITEM)
        self.assertEqual((it["id"], it["source"], it["price_usd"], it["bedrooms"], it["floor"]), ("lv26284875", "livo.ge", 600, 1, 2))
        self.assertEqual(it["owner_id"], 671160)  # TNET user id, shared with myhome: the same owner on both sites
        self.assertEqual(it["urls"], ["https://livo.ge/en/udzravi-qoneba/for-rent-apartment/2-room-apartment-for-rent-in-nadzaladevi-26284875"])
        self.assertIn("Near metro", it["text"])
        self.assertEqual(len(it["photo_urls"]), 2)


class Korter(unittest.TestCase):
    def test_listing_and_detail(self):
        self.assertEqual(fetch_korter.listing(korter_page({"apartmentListingStore": {"apartments": [KORTER_ITEM]}})), [KORTER_ITEM])
        d = fetch_korter.detail_fields(KORTER_DETAIL)
        self.assertEqual((d["seller_type"], d["bedroomCount"], len(d["images"])), ("owner", 1, 2))
        self.assertNotIn("+995", json.dumps(d))  # phones are not stored

    def test_conversion(self):
        det = fetch_korter.detail_fields(KORTER_DETAIL)
        with mock.patch.dict(ingest.KR_DETAILS, {"1014837": det}):
            it = ingest.from_korter(KORTER_ITEM)
        self.assertEqual((it["id"], it["source"], it["price_usd"], it["bedrooms"], it["floor"]), ("kr1014837", "korter.ge", 1150, 1, 1))
        self.assertEqual(it["address"], "Vake, Permtserta St, 149 (Block D)")
        self.assertEqual(it["point"], ((41.74, 44.74), "улица"))
        self.assertEqual(it["parsed"], {"floors": 4, "bathrooms": 1, "yes": ["balcony"]})
        self.assertEqual(it["photo_urls"][0], "https://storage.googleapis.com/bd-ge-01/realty/1280x960/2.jpg")
        self.assertEqual(it["geo_queries"][:2], ["Permtserta St, 149 (Block D)", "Permtserta St"])

    def test_without_details_and_in_lari(self):
        it = ingest.from_korter({**KORTER_ITEM, "price": 2700, "currency": "GEL", "roomCount": 3})
        self.assertEqual((it["price_usd"], it["bedrooms"], it["text"]), (1000, 2, ""))
        self.assertEqual(it["photo_urls"], ["https://storage.googleapis.com/bd-ge-01/realty/400x315/1.jpg"])


if __name__ == "__main__":
    unittest.main()
