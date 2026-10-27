"""Tests for ☀️ Cooler Today (today_web.py)."""
import json
import os
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

import today_web
from test_icom_live import BOT_TOKEN, make_init

WEATHER = {"current": {"temp": 31, "feels": 35, "humidity": 70, "wind": 9, "code": 2},
           "days": [{"date": date(2026, 10, 27 + i), "code": 61 if i == 1 else 0, "tmax": 33, "tmin": 25, "rain": 70 if i == 1 else 10}
                    for i in range(3)]}
RATES = {"rates": {"KHR": 4100.0, "THB": 35.0, "EUR": 0.9}, "date": "Mon"}


class TodayWebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.c = SimpleNamespace(BOT_TOKEN=BOT_TOKEN, OWNER_ID=1, ADMIN_IDS=[], GEMINI_API_KEY="k",
                                 BOT_DB_PATH=os.path.join(self.tmp, "t.db"))
        today_web.register(mock.Mock(), self.c, {"ask": lambda p: "ផឹកទឹកឲ្យបានច្រើន"})
        for target, value in (("weather_service.weather_service.fetch", WEATHER), ("market_info.fetch_rates", RATES),
                              ("market_info.fetch_gold", {"oz": 4000.0, "at": "27/10"}),
                              ("market_info.fetch_news", [{"title": "ព័ត៌មាន", "link": "https://x", "source": "s", "when": ""}])):
            p = mock.patch(target, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("icom_live.is_allowed", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def post(self, path, data=None, uid=50):
        body = dict(data or {}, initData=make_init({"id": uid, "first_name": "Fan"}))
        code, raw, _c, _h = today_web.handle_post(path, self.c, json.dumps(body).encode())
        return code, json.loads(raw)

    def test_me_static(self):
        code, out = self.post("/today/me")
        self.assertEqual(len(out["provinces"]), 25)
        self.assertTrue(out["trip"][0]["places"][0]["map"].startswith("https://www.google.com/maps"))
        self.assertGreaterEqual(len(out["health"]["topics"]), 5)
        with mock.patch("icom_live.is_allowed", return_value=False):
            self.assertEqual(self.post("/today/dash", uid=77)[0], 403)

    def test_dash(self):
        code, out = self.post("/today/dash", {"province": "kampot"})
        self.assertEqual(code, 200)
        self.assertEqual(out["weather"]["name"], "កំពត")
        self.assertEqual(out["weather"]["days"][1]["label"], "ស្អែក")
        self.assertEqual(out["rates"]["khr"], 4100.0)
        self.assertAlmostEqual(out["gold"]["chi"], 4000 / 31.1034768 * 3.75, 2)
        self.assertEqual(out["news"][0]["title"], "ព័ត៌មាន")
        self.assertIn("lunar", out["cal"])

    def test_dash_survives_dead_sources(self):
        with mock.patch("market_info.fetch_gold", side_effect=RuntimeError("down")):
            code, out = self.post("/today/dash")
        self.assertEqual((code, out["gold"]), (200, None))

    def test_sila_and_brief(self):
        self.assertFalse(self.post("/today/sila")[1]["on"])
        code, out = self.post("/today/sila/set", {"on": True})
        self.assertTrue(out["on"])
        self.assertTrue(out["upcoming"])
        code, out = self.post("/today/brief/set", {"on": True, "province": "kep"})
        self.assertEqual(out["brief"], "kep")
        self.assertEqual(self.post("/today/brief/set", {"on": False})[1]["brief"], None)

    def test_anniv(self):
        code, out = self.post("/today/anniv/add", {"lunar": False, "month": 3, "day": 15, "title": "ខួបម៉ាក់"})
        self.assertEqual(out["items"][0]["title"], "ខួបម៉ាក់")
        code, out = self.post("/today/anniv/add", {"lunar": True, "month": 2, "day": 15, "waning": False, "title": "ខួបលោកតា"})
        self.assertEqual(len(out["items"]), 2)
        code, out = self.post("/today/anniv/add", {"lunar": False, "month": 2, "day": 31, "title": "x"})
        self.assertEqual(code, 400)
        rid = out = self.post("/today/anniv/list")[1]["items"][0]["id"]
        self.post("/today/anniv/del", {"id": rid}, uid=51)
        self.assertEqual(len(self.post("/today/anniv/list")[1]["items"]), 2)
        self.post("/today/anniv/del", {"id": rid})
        self.assertEqual(len(self.post("/today/anniv/list")[1]["items"]), 1)

    def test_health(self):
        code, out = self.post("/today/bp/add", {"text": "150/95 80"})
        self.assertEqual(out["result"]["label"], "ខ្ពស់")
        self.assertEqual(self.post("/today/bp/add", {"text": "abc"})[0], 400)
        self.assertTrue(self.post("/today/health/done")[1]["new"])
        self.assertFalse(self.post("/today/health/done")[1]["new"])
        code, out = self.post("/today/health/ask", {"q": "ឈឺទ្រូងខ្លាំង"})
        self.assertTrue(out["emergency"])
        code, out = self.post("/today/health/ask", {"q": "ក្ដៅថ្ងៃគួរធ្វើអ្វី"})
        self.assertIn("ផឹកទឹក", out["text"])


if __name__ == "__main__":
    unittest.main()
