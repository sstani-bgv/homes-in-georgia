"""Incremental Instagram fetch: the second run asks Apify only for posts since the first, "no_items" rows are dropped,
and ingest turns a post into an ig: listing of a competitor agency. No network: apify_run and photo downloads are replaced."""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

import fetch_instagram as ig
import ingest


def post(pid, age_h, caption="Сдаётся квартира в аренду, Ваке, 2 спальни, 80 м², 1200$"):
    t = datetime.fromtimestamp(time.time() - age_h * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {"id": pid, "timestamp": t, "caption": caption, "ownerUsername": "agency1", "url": f"https://instagram.com/p/{pid}/",
            "images": ["https://cdn/x.jpg"]}


class IncrementalFetch(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        os.makedirs("raw")
        json.dump({"instagram_defaults": {"days_back": 7}, "instagram_accounts": [{"key": "agency1"}]}, open("sources.json", "w"))

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def run_fetch(self, returned):
        calls = []
        with mock.patch.object(ig, "apify_run", lambda accs, posts, since: calls.append(since) or returned), \
                mock.patch.object(ig, "download", lambda pid, urls, prefix: [f"raw/img/{prefix}{pid}_0.jpg"]), \
                mock.patch.object(sys, "argv", ["fetch_instagram.py"]):
            ig.main()
        return time.time() - calls[0], json.load(open("raw/instagram.json"))

    def test_second_run_asks_only_since_last_run(self):
        ago, out = self.run_fetch([post("a", 30), post("old", 24 * 8), {"error": "no_items"}])
        self.assertAlmostEqual(ago, 7 * 86400, delta=60)        # first run: the whole window
        self.assertEqual([o["id"] for o in out], ["a"])          # older than days_back and error rows dropped
        ago, out = self.run_fetch([post("b", 0.1), post("promo", 0.1, "Мы лучшее агентство, пишите нам в директ")])
        self.assertLess(ago, 0.3 * 3600)                         # since the last run + 15 min overlap
        self.assertEqual(sorted(o["id"] for o in out), ["a", "b"])  # merged, the non-rental promo skipped
        c = ingest.from_instagram(out[0])
        self.assertEqual((c["id"][:2], c["source"], c["owner_id"], c["price_usd"]), ("ig", "ig:agency1", "ig:agency1", 1200))


if __name__ == "__main__":
    unittest.main()
