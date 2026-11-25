"""Управление → Ключи → Расходы: Apify runs + AI tokens summed for today / week / month, no network."""
import json
import time
from datetime import datetime, timezone

from app.api import content
from app.models import Setting
from tests.conftest import auth, make_user


def test_spend(db, client, monkeypatch):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    monkeypatch.setattr(content, "_apify_accounts", lambda: [{"tail": "ab12", "used_usd": 4.68, "limit_usd": 5, "cycle_end": "2026-11-29", "error": None,
                                                              "days": [{"date": "1999-01-01", "usd": 3.0}, {"date": today, "usd": 0.07}]}])
    monkeypatch.setattr(content, "_ai_balance", lambda: {"balance_usd": 9.5, "used_usd": 0.5})
    monkeypatch.setattr(content, "_openrouter_prices", lambda: {"google/gemma": (1e-7, 2e-7)})
    now = time.time()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db.add(Setting(key="source_runs", value=json.dumps({"facebook": [[now - 5 * 86400, 0.1, 9, 6], [now, 0.05, 3, 6]], "ss": [[now, 0.02, 1, 6]]})))
    db.add(Setting(key="llm_usage", value=json.dumps({today: {"gpt-4.1-mini": [3, 1_000_000, 100_000], "weird": [1, 5, 5],
                                                          "google/gemma": [10, 1000, 100, 0.25, 1000, 0]}, "1999-01-01": {"gpt-4.1-mini": [9, 9, 9]}})))
    db.add(Setting(key="llm_purpose", value=json.dumps({today: {
        "index_competitor": {"google/gemma": [3, 1000, 500, 0.3, 0, 0]},
        "search_query": {"google/gemma": [2, 100, 10, 0.01, 0, 0]}, "search_pick": {"google/gemma": [2, 200, 20, 0.03, 0, 0]}}})))
    db.commit()
    admin = make_user(db, "superadmin")
    r = client.get("/api/spend", headers=auth(admin)).json()
    assert r["apify"]["today_usd"] == 0.07 and r["apify"]["week_usd"] == 0.07 and r["apify"]["runs_week"] == {"facebook": 2, "ss": 1}
    assert r["apify"]["accounts"][0]["used_usd"] == 4.68
    t = r["ai"]["today"]
    # gpt-4.1-mini at list price 0.56; gemma: 0.25 reported by OpenRouter + 1000 unpriced prompt tokens at its list price
    assert t["calls"] == 14 and t["prompt"] == 1_001_005 and t["usd"] == 0.8101 and t["unpriced"] == ["weird"]
    assert t["estimated"] and {m["model"]: m["usd"] for m in t["models"]}["google/gemma"] == 0.2501
    assert r["ai"]["month"]["calls"] == 14 and r["ai"]["week"]["calls"] == 14  # 1999 is not this month
    assert r["ai"]["balance"]["balance_usd"] == 9.5
    p = r["ai"]["purposes"]["today"]
    assert [x["purpose"] for x in p["rows"]] == ["index_competitor", "search_pick", "search_query"]
    assert p["rows"][0] == {"purpose": "index_competitor", "calls": 3, "tokens": 1500, "completion": 500, "usd": 0.3}
    assert p["searches"] == 2 and p["per_search_usd"] == 0.02  # (0.01 + 0.03) / 2 picks
    assert r["ai"]["purposes"]["month"]["rows"] == p["rows"]
    assert client.get("/api/spend", headers=auth(make_user(db, "operator"))).status_code == 403


def test_apify_account(monkeypatch):
    """The Apify API answers -> plan, cycle, days, limit, $ per source, «limit exhausted since» the day the sum hit it."""
    import httpx
    data = {"/users/me": {"plan": {"id": "FREE"}, "createdAt": "2026-10-30T10:00:00.000Z"},
            "/users/me/usage/monthly": {"usageCycle": {"startAt": "2026-10-30T00:00:00.000Z", "endAt": "2026-11-29T23:59:59.999Z"},
                                        "dailyServiceUsages": [{"date": f"2026-11-{d:02d}T00:00:00.000Z", "totalUsageCreditsUsd": 1.0} for d in range(10, 16)]},
            "/users/me/limits": {"limits": {"maxMonthlyUsageUsd": 5}, "current": {"monthlyUsageUsd": 5.01}},
            "/actor-runs": {"items": [{"actId": "A", "startedAt": "2026-11-12T00:00:00Z", "usageTotalUsd": 0.5},
                                      {"actId": "A", "startedAt": "2026-11-13T00:00:00Z", "usageTotalUsd": 0.25},
                                      {"actId": "B", "startedAt": "2026-11-13T00:00:00Z", "usageTotalUsd": 0.1},
                                      {"actId": "A", "startedAt": "2026-10-01T00:00:00Z", "usageTotalUsd": 9}]},
            "/acts/A": {"username": "dami_studio", "name": "facebook-groups-scraper"}, "/acts/B": {"username": "x", "name": "y"}}

    def handler(req):
        assert req.headers["Authorization"] == "Bearer apify_api_secret_TAIL"
        return httpx.Response(200, json={"data": data[req.url.path.removeprefix("/v2")]})
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    a = content._apify_account("apify_api_secret_TAIL")
    assert a["plan"] == "FREE" and a["cycle_start"] == "2026-10-30" and a["cycle_end"] == "2026-11-29" and len(a["days"]) == 6
    assert a["used_usd"] == 5.01 and a["exhausted"] and a["exhausted_since"] == "2026-11-14"
    assert a["by_source"] == [{"source": "facebook", "runs": 2, "usd": 0.75}, {"source": "x/y", "runs": 1, "usd": 0.1}]
    assert "secret" not in str(a)

    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(401)), **kw))
    assert content._apify_account("apify_api_secret_TAIL")["error"] == "invalid_key"
