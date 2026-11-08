"""Incremental Facebook fetch: the second run asks Apify only for posts since the first, and the kept dataset merges by
post id inside the days_back window. No network: apify_run is replaced."""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import fetch_facebook as fb


def post(pid, age_h):
    return {"post_id": pid, "creation_timestamp": time.time() - age_h * 3600, "text": "", "post_url": f"u{pid}"}


class IncrementalFetch(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        os.makedirs("raw")
        json.dump({"facebook_defaults": {"days_back": 7}, "facebook_groups": [{"key": "g1", "url": "https://fb/g/1"}]},
                  open("sources.json", "w"))

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def run_fetch(self, returned, *args):
        calls = []
        with mock.patch.object(fb, "apify_run", lambda groups, posts, hours: calls.append(hours) or returned), \
                mock.patch.object(sys, "argv", ["fetch_facebook.py", *args]):
            fb.main()
        return calls[0], {p["post_id"] for p in json.load(open(fb.RAW))}

    def test_second_run_asks_only_since_last_run_and_merges(self):
        hours, kept = self.run_fetch([post("a", 30), post("old", 24 * 8)])
        self.assertEqual(hours, 7 * 24)          # first run: the whole window
        self.assertEqual(kept, {"a"})            # older than days_back is dropped
        hours, kept = self.run_fetch([post("a", 30), post("b", 0.1)])
        self.assertLess(hours, 0.3)              # seconds since the last run + 15 min overlap
        self.assertEqual(kept, {"a", "b"})       # merged by id, no duplicate "a"
        hours, _ = self.run_fetch([], "--full")
        self.assertEqual(hours, 7 * 24)


class KeysAndShape(unittest.TestCase):
    def test_normalize_dami_row(self):
        row = {"recordType": "post", "id": "1016", "url": "https://www.facebook.com/groups/g/permalink/1016/", "time": "2026-09-20T23:30:52.000Z",
               "timestamp": 1789947052, "text": "Сдаю квартиру", "user": {"id": "pfbid0", "name": "Нино"}, "groupId": "315406794412410",
               "attachments": [{"type": "photo", "thumbnail": "https://scontent/x.jpg", "url": "https://www.facebook.com/photo/?fbid=1"}]}
        p = fb.normalize(row)
        self.assertEqual((p["post_id"], p["group_id"], p["user_name"], p["creation_timestamp"]), ("1016", "315406794412410", "Нино", 1789947052))
        self.assertEqual(p["media"], [{"type": "Photo", "uri": "https://scontent/x.jpg"}])
        self.assertEqual(fb.author_key(p), "fb-user:pfbid0")

    def test_next_key_when_one_is_out_of_usage(self):
        class R:
            def __init__(self, code, body): self.status_code, self._b = code, body
            def json(self): return self._b
            def raise_for_status(self): return self
        used = []

        class Client:
            def __init__(self, headers, timeout): self.tok = headers["Authorization"]
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **k):
                used.append(self.tok)
                return R(402, {"error": {"type": "not-enough-usage-to-run-paid-actor"}}) if self.tok.endswith("k1") else \
                    R(201, {"data": {"id": "r", "status": "SUCCEEDED", "defaultDatasetId": "d"}})
            def get(self, *a, **k): return R(200, [{"id": "x"}])
        with mock.patch.object(fb, "tokens", lambda: ["k1", "k2"]), mock.patch.object(fb.httpx, "Client", Client):
            self.assertEqual(fb.run_actor("a", {}), [{"id": "x"}])
        self.assertEqual(used, ["Bearer k1", "Bearer k2"])
        with mock.patch.object(fb, "tokens", lambda: ["k1"]), mock.patch.object(fb.httpx, "Client", Client):
            self.assertRaises(fb.NoUsage, fb.run_actor, "a", {})


if __name__ == "__main__":
    unittest.main()
