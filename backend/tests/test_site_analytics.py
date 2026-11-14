"""CRM «Аналитика»: the hge.ge counter report, proxied with the server-side key, for superadmins only."""
import httpx
import pytest

from app.config import settings
from app.services import site_analytics
from tests.conftest import auth, make_user

REPORT = {"period": {"days": 7}, "totals": {"visitors": 12, "contacts": 3}, "prev": {"visitors": 10, "contacts": 1}, "daily": []}


class Resp:
    def __init__(self, data):
        self.data = data

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


@pytest.fixture
def script(monkeypatch):
    """Stand-in for the Apps Script web app; records what the CRM asked."""
    calls = []
    answer = {"data": {"ok": True, "report": REPORT}}

    def get(url, params=None, **kw):
        calls.append({"url": url, **(params or {})})
        if isinstance(answer["data"], httpx.HTTPError):
            raise answer["data"]
        return Resp(answer["data"])

    monkeypatch.setattr(site_analytics.httpx, "get", get)
    monkeypatch.setattr(settings, "SITE_ANALYTICS_KEY", "k" * 24)
    site_analytics.clear_cache()
    yield calls, answer
    site_analytics.clear_cache()


def test_only_superadmins_see_site_analytics(db, client, script):
    calls, _ = script
    for role in ("superadmin",):
        r = client.get("/api/analytics/site", params={"days": 30}, headers=auth(make_user(db, role)))
        assert r.status_code == 200, r.text
        assert r.json()["totals"]["visitors"] == 12
    for role in ("admin", "operator", "agent", "recruit"):  # every lower level, incl. admin
        assert client.get("/api/analytics/site", headers=auth(make_user(db, role))).status_code == 403
    assert client.get("/api/analytics/site").status_code == 401
    # the key goes from the server to the script and never back to the browser
    assert calls[0] == {"url": settings.SITE_ANALYTICS_URL, "action": "report", "key": "k" * 24, "days": 30}
    assert "k" * 24 not in r.text


def test_cache_period_and_fresh(db, client, script):
    calls, _ = script
    h = auth(make_user(db, "superadmin"))
    client.get("/api/analytics/site", params={"days": 7}, headers=h)
    client.get("/api/analytics/site", params={"days": 7}, headers=h)
    assert len(calls) == 1  # second click served from memory
    client.get("/api/analytics/site", params={"days": 7, "fresh": True}, headers=h)
    assert len(calls) == 2
    client.get("/api/analytics/site", params={"days": 5000}, headers=h)  # unknown period → 7 days (cached)
    assert len(calls) == 2


def test_errors_are_readable(db, client, script, monkeypatch):
    _, answer = script
    h = auth(make_user(db, "superadmin"))

    answer["data"] = {"ok": False, "error": "bad_key"}
    r = client.get("/api/analytics/site", headers=h)
    assert (r.status_code, r.json()["code"]) == (502, "site_analytics_bad_key")

    answer["data"] = {"ok": False, "error": "bad_session"}  # old script deployment, before REPORT_KEY
    assert client.get("/api/analytics/site", headers=h).json()["code"] == "site_analytics_outdated"

    answer["data"] = httpx.ConnectTimeout("slow")
    assert client.get("/api/analytics/site", headers=h).json()["code"] == "site_analytics_unavailable"

    answer["data"] = ValueError("html instead of json")
    assert client.get("/api/analytics/site", headers=h).json()["code"] == "site_analytics_unavailable"

    monkeypatch.setattr(settings, "SITE_ANALYTICS_KEY", "")
    r = client.get("/api/analytics/site", headers=h)
    assert (r.status_code, r.json()["code"]) == (503, "site_analytics_off")
