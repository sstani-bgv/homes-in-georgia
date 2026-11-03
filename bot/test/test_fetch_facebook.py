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


if __name__ == "__main__":
    unittest.main()
