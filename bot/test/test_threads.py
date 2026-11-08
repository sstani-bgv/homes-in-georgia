"""Threads source (fetch_threads.py): a found post goes through the Facebook rental filter and the ingest converter."""
import unittest

import fetch_threads
import ingest

TEXT = "Сдаю квартиру в Ваке, 2 спальни, 75 м², 5/9 этаж, $900 в месяц. Животные по договорённости."


class ThreadsTest(unittest.TestCase):
    def test_filter_timestamp_and_ingest(self):
        self.assertIsNone(fetch_threads.skip_reason({"text": TEXT, "photos_src": ["x"]}))
        self.assertEqual(fetch_threads.skip_reason({"text": "Ищу квартиру в Ваке до $900, 2 спальни, с животными", "photos_src": ["x"]}),
                         "ищут квартиру")
        self.assertAlmostEqual(fetch_threads._ts({"timestamp": "2026-11-08T10:00:00+0000"}), 1794132000, delta=1)
        c = ingest.from_threads({"id": "123", "group": "owner", "groups": ["owner"], "post_urls": ["https://www.threads.net/@owner/post/abc"],
                                 "author": "owner", "author_name": "owner", "author_type": None, "date": None, "text": TEXT, "photos": []})
        self.assertEqual((c["id"], c["source"], c["owner_id"], c["price_usd"], c["area"], c["floor"], c["bedrooms"]),
                         ("th123", "th:owner", "th:owner", 900, 75.0, 5, 2))


if __name__ == "__main__":
    unittest.main()
