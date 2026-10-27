"""Tests for 🧰 Cooler Daily Tools (tools_web.py) — the Hub's everyday-helpers web app backend."""
import base64
import json
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import daily_tools
import knowledge_base
import tools_web
from test_icom_live import BOT_TOKEN, make_init

PHOTO = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8" + b"1" * 2000).decode()
WAV = "data:audio/wav;base64," + base64.b64encode(b"RIFF" + b"0" * 2000).decode()


class ToolsWebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.c = SimpleNamespace(BOT_TOKEN=BOT_TOKEN, OWNER_ID=1, ADMIN_IDS=[], GEMINI_API_KEY="k",
                                 BOT_DB_PATH=os.path.join(self.tmp, "t.db"))
        self.calls = []
        self.bot = mock.Mock()

        def ai(parts, prompt):
            self.calls.append((parts, prompt))
            if "reminder request" in prompt:
                return '{"when": "2099-01-02 14:00", "repeat": "none", "what": "ណាត់ភ្ញៀវ"}'
            return "🔴 គ្រោះថ្នាក់ខ្ពស់ 90%"
        tools_web.register(self.bot, self.c, {"ai": ai, "rate": lambda: 4000.0,
                                              "kb_ask": lambda s, p: "ហាងបើកម៉ោង ៧ ព្រឹក 📄 ម៉ោង",
                                              "extract": lambda b, m: "តម្លៃកាហ្វេ 5000 រៀល"})
        self.allowed = mock.patch("icom_live.is_allowed", return_value=True)
        self.allowed.start()
        self.addCleanup(self.allowed.stop)

    def post(self, path, data=None, uid=50):
        body = dict(data or {}, initData=make_init({"id": uid, "first_name": "Fan"}))
        code, raw, _ct, _h = tools_web.handle_post(path, self.c, json.dumps(body).encode(), bot=self.bot)
        return code, json.loads(raw)

    def test_me_and_members_only(self):
        code, out = self.post("/tools/me")
        self.assertTrue(out["member"])
        self.assertIn("📦 ផ្សេងៗ", out["categories"])
        with mock.patch("icom_live.is_allowed", return_value=False):
            code, out = self.post("/tools/cash/list", uid=77)
        self.assertEqual(code, 403)

    def test_ai_tools(self):
        code, out = self.post("/tools/ai", {"tool": "scam", "text": "aba-bonus.xyz OTP"})
        self.assertEqual((code, out["title"]), (200, "🚨 លទ្ធផលឆែក Scam"))
        self.assertIn("aba-bonus.xyz", self.calls[-1][1])
        code, out = self.post("/tools/ai", {"tool": "money"})
        self.assertEqual(code, 400)
        code, out = self.post("/tools/ai", {"tool": "money", "photo": PHOTO})
        self.assertEqual(self.calls[-1][0][0]["inline_data"]["mime_type"], "image/jpeg")
        code, out = self.post("/tools/ai", {"tool": "totext", "audio": WAV})
        self.assertEqual(self.calls[-1][0][0]["inline_data"]["mime_type"], "audio/wav")
        code, out = self.post("/tools/ai", {"tool": "translate", "text": "Hello", "lang": "zh"})
        self.assertIn("Simplified Chinese", self.calls[-1][1])
        code, out = self.post("/tools/ai", {"tool": "homework", "text": "2x+5=15"})
        self.assertIn("2x+5=15", self.calls[-1][1])

    def test_ai_limit_and_refund(self):
        with mock.patch.dict(os.environ, {"TOOLS_DAILY_LIMIT": "1"}):
            self.post("/tools/ai", {"tool": "scam", "text": "x"})
            code, out = self.post("/tools/ai", {"tool": "scam", "text": "y"})
            self.assertEqual(code, 400)
            self.assertIn("អស់", out["msg"])
        tools_web.HOOKS["ai"] = mock.Mock(side_effect=RuntimeError("down"))
        code, out = self.post("/tools/ai", {"tool": "scam", "text": "x"}, uid=60)
        self.assertEqual(code, 502)
        import studio_web
        self.assertEqual(studio_web.text_used(self.c, 60, "tools"), 0)

    def test_reminders_shared_with_bot_table(self):
        code, out = self.post("/tools/remind/add", {"text": "ស្អែកម៉ោង ២ ណាត់ភ្ញៀវ"})
        self.assertEqual(out["item"]["what"], "ណាត់ភ្ញៀវ")
        conn = daily_tools._db(self.c)
        row = conn.execute("SELECT chat_id, user_id FROM reminders").fetchone()
        conn.close()
        self.assertEqual((row["chat_id"], row["user_id"]), (50, 50))
        rid = out["item"]["id"]
        code, out = self.post("/tools/remind/del", {"id": rid}, uid=51)      # someone else can't delete it
        self.assertEqual(len(self.post("/tools/remind/list")[1]["items"]), 1)
        code, out = self.post("/tools/remind/del", {"id": rid})
        self.assertEqual(out["items"], [])

    def test_cash_and_report(self):
        self.post("/tools/cash/add", {"kind": "out", "amount": "20000", "currency": "KHR", "category": "🍜 អាហារ", "note": "បាយ"})
        self.post("/tools/cash/add", {"text": "លក់បាន $20 អាវ"})
        code, out = self.post("/tools/cash/add", {"kind": "out", "amount": "abc"})
        self.assertEqual(code, 400)
        code, rep = self.post("/tools/report")
        self.assertEqual((rep["income"], rep["spent"], rep["balance"]), (20.0, 5.0, 15.0))
        self.assertEqual(rep["cats"][0][:2], ["🍜 អាហារ", 5.0])
        code, out = self.post("/tools/cash/list")
        self.assertEqual(len(out["items"]), 2)
        self.post("/tools/cash/del", {"id": out["items"][0]["id"]})
        self.assertEqual(len(self.post("/tools/cash/list")[1]["items"]), 1)
        code, rep = self.post("/tools/report", {"month": "2001-01"})
        self.assertEqual(rep["count"], 0)

    def test_kb(self):
        code, out = self.post("/tools/kb/ask", {"q": "ម៉ោងប៉ុន្មាន?"})
        self.assertEqual(code, 400)
        code, out = self.post("/tools/kb/add", {"title": "ម៉ោង", "body": "ហាងបើក ៧ ព្រឹក"})
        self.assertEqual(out["item"]["title"], "ម៉ោង")
        code, out = self.post("/tools/kb/add", {"file": PHOTO, "name": "menu.jpg"})
        self.assertEqual(len(out["items"]), 2)
        self.assertEqual(knowledge_base.docs(self.c, 50)[1]["body"], "តម្លៃកាហ្វេ 5000 រៀល")
        code, out = self.post("/tools/kb/ask", {"q": "ម៉ោងប៉ុន្មាន?"})
        self.assertTrue(out["found"])
        code, out = self.post("/tools/kb/del", {"id": out.get("id", 0) or knowledge_base.docs(self.c, 50)[0]["id"]})
        self.assertEqual(len(out["items"]), 1)

    def test_send(self):
        code, out = self.post("/tools/send", {"title": "🌐 បកប្រែ", "text": "<b>hi</b>"})
        self.assertEqual(code, 200)
        args = self.bot.send_message.call_args
        self.assertEqual(args[0][0], 50)
        self.assertIn("&lt;b&gt;hi", args[0][1])


if __name__ == "__main__":
    unittest.main()
