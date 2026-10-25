"""Morning news card: RSS photos, Khmer headlines, card drawing and the brief wiring."""
import unittest
from datetime import date
from unittest.mock import patch

import calendar_brief
import news_brief as nb

RSS = """<?xml version="1.0"?><rss xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><title>ពិធីបុណ្យអុំទូកឆ្នាំនេះ</title><link>https://a.kh/1</link>
  <media:content url="https://a.kh/1.jpg" medium="image"/></item>
<item><title>Gold price rises</title><link>https://a.kh/2</link>
  <description>&lt;p&gt;&lt;img src="https://a.kh/2.png"&gt; text&lt;/p&gt;</description></item>
<item><title>No photo</title><link>https://a.kh/3</link></item>
</channel></rss>"""


class NewsBriefTests(unittest.TestCase):
    def test_parse_feed_finds_photos(self):
        items = nb.parse_feed(RSS, "a.kh")
        self.assertEqual([i["image"] for i in items], ["https://a.kh/1.jpg", "https://a.kh/2.png", ""])

    def test_khmerize_only_non_khmer(self):
        stories = [{"title": "ពិធីបុណ្យ"}, {"title": "Gold price rises"}]
        asked = []
        nb.khmerize(stories, lambda p: asked.append(p) or "1. តម្លៃមាសឡើង")
        self.assertEqual(stories[1]["title"], "តម្លៃមាសឡើង")
        self.assertNotIn("ពិធីបុណ្យ", asked[0])

    def test_khmer_wrap_breaks_long_runs(self):
        from PIL import Image, ImageDraw
        import card_render as cr
        d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        lines = nb._wrap_px(d, "ក្រសួងសុខាភិបាលណែនាំប្រជាពលរដ្ឋឱ្យប្រុងប្រយ័ត្នគ្រុនឈាមរដូវវស្សា", cr.font(34, 600), 300, 2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(d.textlength(l, font=cr.font(34, 600)) <= 300 for l in lines))

    def test_brief_builds_card_and_caption(self):
        stories = [{"title": "ព័ត៌មាន ១", "link": "https://a.kh/1", "source": "a.kh", "image": ""}]
        with patch.object(nb, "fetch_stories", return_value=stories), \
                patch("market_info.fetch_rates", return_value={"rates": {"KHR": 4060}}), \
                patch("market_info.fetch_gold", return_value=None), \
                patch("weather_service.weather_service.fetch", side_effect=RuntimeError):
            png, cap, st = calendar_brief.news_brief(date(2026, 10, 25), "phnom_penh", ask=None)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertIn("https://a.kh/1", cap)
        self.assertEqual(calendar_brief.news_markup(st).keyboard[0][0].url, "https://a.kh/1")


if __name__ == "__main__":
    unittest.main()
