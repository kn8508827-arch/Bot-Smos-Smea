"""Partner Facebook Pages: token exchange, page tokens, new posts → spotlight + alert."""
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import telebot

import fb_pages as fp


class FakeResp:
    def __init__(self, data, code=200):
        self._d, self.status_code = data, code

    def json(self):
        return self._d


class FbPagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.config = SimpleNamespace(BOT_DB_PATH=self.tmp.name, OWNER_ID=1, ADMIN_IDS=[])
        self.posts = [{"id": "9_1", "message": "ព័ត៌មានចាស់", "created_time": "2026-10-25T01:00:00+0000",
                       "permalink_url": "https://fb.com/9_1"}]
        def get(url, params=None):
            if url.endswith("oauth/access_token"):
                return FakeResp({"access_token": "LONG"})
            if url.endswith("me/accounts"):
                return FakeResp({"data": [{"id": "9", "name": "TPNews", "access_token": "PAGETOKEN"}]})
            if url.endswith("9/posts"):
                return FakeResp({"data": list(self.posts)})
            return FakeResp({"error": {"message": "no"}}, 400)
        self.client = SimpleNamespace(get=get)
        self.alerts = []
        self.bot = telebot.TeleBot("1:T", threaded=False)
        self.api = fp.register(self.bot, self.config, on_new=lambda k, p: self.alerts.append((k, p)), client=self.client)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_flow(self):
        with patch.dict(os.environ, {"FB_APP_ID": "A", "FB_APP_SECRET": "S"}):
            self.assertEqual(fp.connect(self.config, "SHORT", self.client), [("9", "TPNews")])
        with fp._db(self.config) as c:
            c.execute("UPDATE fb_pages SET partner='tpnews'")
        self.api["tick"]()                        # first read: remembered, no alert
        self.assertEqual(self.alerts, [])
        now = time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime())
        self.posts.insert(0, {"id": "9_2", "message": "ព័ត៌មានថ្មី\nលម្អិត", "created_time": now,
                              "permalink_url": "https://fb.com/9_2"})
        self.api["tick"]()
        self.assertEqual(self.alerts[0][0], "tpnews")
        sp = fp.spotlight(self.config)
        self.assertEqual(sp[0][:4], ["tpnews", "TPNews", "tpnews-logo.png", "ព័ត៌មានថ្មី"])
        self.assertEqual(sp[0][5], "https://fb.com/9_2")

    def test_needs_app_credentials(self):
        with patch.dict(os.environ, {"FB_APP_ID": "", "FB_APP_SECRET": ""}):
            with self.assertRaises(RuntimeError):
                fp.connect(self.config, "SHORT", self.client)


if __name__ == "__main__":
    unittest.main()
