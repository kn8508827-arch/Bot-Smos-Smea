"""/news keeps working when Google News is blocked: falls back to the Khmer sites, then English."""
import unittest
from unittest.mock import patch

import market_info as mi

RSS = """<rss><channel><item><title>{t}</title><link>https://x.kh/{n}</link></item></channel></rss>"""


class NewsFallbackTests(unittest.TestCase):
    def setUp(self):
        mi._cache.clear()

    def test_falls_back_to_sites_then_english(self):
        calls = []

        def fake_get(url, client=None):
            calls.append(url)
            if "news.google.com" in url and "hl=km" in url:
                raise RuntimeError("blocked")
            class R: pass
            r = R()
            r.text = RSS.format(t="ការប្រកួតបាល់ទាត់ កីឡា" if "google" not in url else "Cambodia wins", n=len(calls))
            return r
        with patch.object(mi, "_get", side_effect=fake_get):
            items = mi.fetch_news("sport")
        self.assertTrue(items)
        self.assertIn("កីឡា", items[0]["title"])          # from a Khmer site feed

    def test_english_when_everything_else_fails(self):
        def fake_get(url, client=None):
            if "hl=en" not in url:
                raise RuntimeError("down")
            class R: pass
            r = R()
            r.text = RSS.format(t="Cambodia economy grows", n=1)
            return r
        with patch.object(mi, "_get", side_effect=fake_get):
            self.assertEqual(mi.fetch_news("economy")[0]["title"], "Cambodia economy grows")


if __name__ == "__main__":
    unittest.main()
