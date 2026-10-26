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

    def wait_for(self, pred):
        for _ in range(60):
            if any(pred(m or {}) for m in self.sent):
                return True
            time.sleep(0.05)
        return False

    def test_ice_breaker_asks_ai(self):
        asked = []
        self.api  # register again with an ask that records the question
        bot = telebot.TeleBot("1:T", threaded=False)
        bot.get_me = MagicMock(return_value=SimpleNamespace(username="TeamSmosSneabot"))
        client = SimpleNamespace(post=lambda url, params=None, json=None: (self.sent.append(json), SimpleNamespace(status_code=200, text=""))[1])
        api = mb.register(bot, self.config, ask=lambda uid, t, n: (asked.append(t), "Rap!")[1], client=client)
        api["handle_event"]("P1", {"sender": {"id": "777"}, "postback": {"payload": "ICE_JOKE"}}) if "handle_event" in api else \
            api["handle_body"](json.dumps({"object": "page", "entry": [{"id": "P1", "messaging": [
                {"sender": {"id": "777"}, "postback": {"payload": "ICE_JOKE"}}]}]}).encode())
        self.assertTrue(self.wait_for(lambda m: m.get("message", {}).get("text") == "Rap!"))
        self.assertEqual(asked, [mb.ICE["ICE_JOKE"][1]])
        # first contact also gets the Hub card
        self.assertTrue(any(m and m.get("message", {}).get("attachment", {}).get("payload", {}).get("template_type") == "generic"
                            for m in self.sent))

    def test_hub_word_sends_card_not_ai(self):
        self.post({"object": "page", "entry": [{"id": "P1", "messaging": [{"sender": {"id": "888"}, "message": {"text": "hub"}}]}]})
        self.assertTrue(self.wait_for(lambda m: m.get("message", {}).get("attachment", {}).get("payload", {}).get("template_type") == "generic"))
        time.sleep(0.2)
        self.assertNotIn("ចម្លើយ AI", [(m or {}).get("message", {}).get("text") for m in self.sent])

    def test_join_guide(self):
        self.post({"object": "page", "entry": [{"id": "P1", "messaging": [{"sender": {"id": "999"}, "message": {"text": "សមាជិក"}}]}]})
        self.assertTrue(self.wait_for(lambda m: "ABA" in m.get("message", {}).get("attachment", {}).get("payload", {}).get("text", "")))
        g = mb.join_guide("https://t.me/x")["attachment"]["payload"]
        self.assertLessEqual(len(g["text"]), 640)

    def test_name_and_memory(self):
        seen, names = [], []
        bot = telebot.TeleBot("1:T", threaded=False)
        bot.get_me = MagicMock(return_value=SimpleNamespace(username="TeamSmosSneabot"))
        client = SimpleNamespace(
            post=lambda url, params=None, json=None: (self.sent.append(json), SimpleNamespace(status_code=200, text=""))[1],
            get=lambda url, params=None: SimpleNamespace(status_code=200, json=lambda: {"first_name": "Dara"}))
        api = mb.register(bot, self.config, ask=lambda uid, t, n: (names.append(n), "ok")[1], client=client,
                          observe=lambda uid, t: seen.append((uid, t)))
        api["handle_body"](json.dumps({"object": "page", "entry": [{"id": "P1", "messaging": [
            {"sender": {"id": "4242"}, "message": {"text": "ខ្ញុំចូលចិត្ត Rap"}}]}]}).encode())
        self.assertTrue(self.wait_for(lambda m: m.get("message", {}).get("text") == "ok"))
        for _ in range(40):
            if seen:
                break
            time.sleep(0.05)
        self.assertEqual(names, ["Dara"])
        self.assertEqual(seen, [(mb.ai_user_id("4242"), "ខ្ញុំចូលចិត្ត Rap")])

    def test_hub_me_route(self):
        req = urllib.request.Request(self.url.replace("/messenger/webhook", "/hub/me"), data=b'{"initData":"x"}',
                                     headers={"Content-Type": "application/json"})
        self.assertEqual(json.loads(urllib.request.urlopen(req).read()), {"member": False, "owner": False})

    def test_profile_body(self):
        b = mb.profile_body("https://kn8508827-arch.github.io/Blue-Gold-Rush/hub/", "https://t.me/TeamSmosSneabot")
        self.assertEqual(b["whitelisted_domains"], ["https://kn8508827-arch.github.io"])
        self.assertEqual(len(b["ice_breakers"][0]["call_to_actions"]), len(mb.ICE))
        for a in b["persistent_menu"][0]["call_to_actions"]:
            self.assertLessEqual(len(a["title"]), 30)
        for q in b["ice_breakers"][0]["call_to_actions"]:
            self.assertLessEqual(len(q["question"]), 80)
        self.assertLessEqual(len(b["greeting"][0]["text"]), 160)

    def test_bad_signature_rejected(self):
        with self.assertRaises(Exception):
            self.post({"object": "page", "entry": []}, secret="wrong")

    def test_ai_user_id_fits_sqlite(self):
        uid = mb.ai_user_id("27183928374659201")
        self.assertTrue(-2 ** 63 < uid < 0)

    def test_split(self):
        self.assertEqual(len(mb.split_text("ក" * 4000)), 3)


if __name__ == "__main__":
    unittest.main()
