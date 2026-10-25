"""/news: a country flag per story + Khmer headline, one AI call, cached."""
import unittest

import market_info as mi


class FlagTests(unittest.TestCase):
    def test_flag(self):
        self.assertEqual(mi.flag("kh"), "🇰🇭")
        self.assertEqual(mi.flag("XX"), "🌍")
        self.assertEqual(mi.flag(""), "🌍")

    def test_enrich_once(self):
        items = [{"title": "Obama speech in Ohio", "link": "https://a/1", "source": "NBC", "when": ""},
                 {"title": "ព័ត៌មានក្នុងស្រុក", "link": "https://a/2", "source": "kh", "when": ""}]
        calls = []
        ask = lambda p: calls.append(p) or "1 | US | លោក អូបាម៉ា ថ្លែងសុន្ទរកថានៅ Ohio\n2 | KH | ព័ត៌មានក្នុងស្រុក"
        mi.enrich_news(items, ask)
        mi.enrich_news(items, ask)
        self.assertEqual(len(calls), 1)
        self.assertEqual(items[0]["flag"], "🇺🇸")
        text = mi.news_text("top", items)
        self.assertIn("🇺🇸", text)
        self.assertIn("អូបាម៉ា", text)

    def test_no_ai_still_works(self):
        items = [{"title": "Hello", "link": "https://a/1", "source": "", "when": ""}]
        self.assertIn("🌍", mi.news_text("top", mi.enrich_news(items, lambda p: "")))


if __name__ == "__main__":
    unittest.main()
