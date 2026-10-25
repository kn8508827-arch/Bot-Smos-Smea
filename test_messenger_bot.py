"""Messenger: webhook verify, signature check, AI reply sent to the right Page."""
import hashlib
import hmac
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import telebot

import messenger_bot as mb


class MessengerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.config = SimpleNamespace(BOT_DB_PATH=self.tmp.name, OWNER_ID=1, ADMIN_IDS=[])
        self.sent = []
        client = SimpleNamespace(post=lambda url, params=None, json=None: (self.sent.append(json), SimpleNamespace(status_code=200, text=""))[1])
        bot = telebot.TeleBot("1:T", threaded=False)
        bot.get_me = MagicMock(return_value=SimpleNamespace(username="TeamSmosSneabot"))
        self.api = mb.register(bot, self.config, ask=lambda uid, t, n: "ចម្លើយ AI", client=client)
        with mb._db(self.config) as c:
            c.execute("INSERT INTO msg_pages VALUES ('P1', 'Vid Cooler', 'TOK', 0)")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self.api["Handler"])
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/messenger/webhook"
        self.env = patch.dict(os.environ, {"FB_APP_SECRET": "sek", "MESSENGER_VERIFY_TOKEN": "cooler"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.srv.shutdown()
        os.unlink(self.tmp.name)

    def test_verify(self):
        r = urllib.request.urlopen(self.url + "?hub.mode=subscribe&hub.verify_token=cooler&hub.challenge=42")
        self.assertEqual(r.read(), b"42")
        with self.assertRaises(Exception):
            urllib.request.urlopen(self.url + "?hub.mode=subscribe&hub.verify_token=bad&hub.challenge=42")

    def post(self, body, secret="sek"):
        raw = json.dumps(body).encode()
        sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        req = urllib.request.Request(self.url, data=raw, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
        return urllib.request.urlopen(req)

    def test_message_gets_ai_reply(self):
        self.post({"object": "page", "entry": [{"id": "P1", "messaging": [{"sender": {"id": "555"}, "message": {"text": "សួស្ដី"}}]}]})
        for _ in range(50):
            if any((m or {}).get("message", {}).get("text") == "ចម្លើយ AI" for m in self.sent):
                break
            time.sleep(0.05)
        texts = [(m or {}).get("message", {}).get("text") for m in self.sent]
        self.assertIn("ចម្លើយ AI", texts)
        self.assertTrue(any("attachment" in (m or {}).get("message", {}) for m in self.sent))   # welcome + Telegram button

    def test_bad_signature_rejected(self):
        with self.assertRaises(Exception):
            self.post({"object": "page", "entry": []}, secret="wrong")

    def test_split(self):
        self.assertEqual(len(mb.split_text("ក" * 4000)), 3)


if __name__ == "__main__":
    unittest.main()
