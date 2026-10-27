"""Tests for 💬 chat with Cooler Bot inside the Hub (hub.chat_response)."""
import json
import unittest
from types import SimpleNamespace

import hub
from test_icom_live import BOT_TOKEN, make_init


class HubChat(unittest.TestCase):
    def setUp(self):
        self.c = SimpleNamespace(BOT_TOKEN=BOT_TOKEN, OWNER_ID=1, ADMIN_IDS=[])
        self.calls = []

    def ask(self, uid, text, name):
        self.calls.append((uid, text, name))
        return "សួស្តី " + (name or "")

    def post(self, data):
        return hub.chat_response(self.c, json.dumps(data).encode(), ask=self.ask)

    def test_reply_uses_signed_user(self):
        code, out = self.post({"initData": make_init({"id": 55, "first_name": "Vid"}), "text": "  hello  "})
        self.assertEqual(code, 200)
        self.assertEqual(self.calls[0][0], 55)
        self.assertEqual(self.calls[0][1], "hello")
        self.assertIn("សួស្តី", out["reply"])

    def test_rejects_unsigned(self):
        code, out = self.post({"initData": "user=%7B%22id%22%3A55%7D&hash=bad", "text": "hi"})
        self.assertEqual(code, 401)
        self.assertEqual(self.calls, [])

    def test_empty_text(self):
        code, _ = self.post({"initData": make_init({"id": 55, "first_name": "Vid"}), "text": "   "})
        self.assertEqual(code, 400)
        self.assertEqual(self.calls, [])

    def test_long_text_trimmed(self):
        self.post({"initData": make_init({"id": 55, "first_name": "Vid"}), "text": "a" * 5000})
        self.assertEqual(len(self.calls[0][1]), hub.HUB_CHAT_MAX)

    def test_ai_error_is_friendly(self):
        def boom(*a):
            raise RuntimeError("x")
        code, out = hub.chat_response(self.c, json.dumps({"initData": make_init({"id": 55, "first_name": "Vid"}), "text": "hi"}).encode(), ask=boom)
        self.assertEqual(code, 200)
        self.assertTrue(out["reply"])

    def test_voice_message(self):
        import base64
        got = []
        def hear(uid, blob, mime, name):
            got.append((uid, blob, mime))
            return "🎧 «សួស្តី Cooler Bot»\n\nសួស្តី Papa! 😎"
        audio = "data:audio/wav;base64," + base64.b64encode(b"RIFF1234WAVE").decode()
        code, out = hub.chat_response(self.c, json.dumps({"initData": make_init({"id": 55, "first_name": "Vid"}),
                                                          "audio": audio}).encode(), ask=self.ask, ask_voice=hear)
        self.assertEqual(code, 200)
        self.assertEqual(got[0][0], 55)
        self.assertEqual(got[0][2], "audio/wav")
        self.assertEqual(out["heard"], "សួស្តី Cooler Bot")
        self.assertEqual(out["reply"], "សួស្តី Papa! 😎")
        self.assertEqual(self.calls, [])

    def test_voice_bad_type(self):
        code, _ = hub.chat_response(self.c, json.dumps({"initData": make_init({"id": 55}),
                                                        "audio": "data:image/png;base64,AAAA"}).encode(), ask=self.ask)
        self.assertEqual(code, 400)

    def test_bad_json(self):
        code, _ = hub.chat_response(self.c, b"{nope")
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
