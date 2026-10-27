"""🎨 Colourful buttons + ✨ animated emoji (button_style.py)."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import telebot
from telebot import apihelper
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import button_style as bs


class ButtonStyleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.config = SimpleNamespace(BOT_DB_PATH=os.path.join(cls.tmp, "t.db"), OWNER_ID=1, ADMIN_IDS=[])
        cls.bot = telebot.TeleBot("1:T", threaded=False)
        bs.install(cls.bot, cls.config)

    def setUp(self):
        bs._state["colors"] = True
        bs.clear()
        bs.set_emoji(False)

    def test_auto_colours(self):
        d = lambda **kw: InlineKeyboardButton(**kw).to_dict().get("style")
        self.assertEqual(d(text="✅ ទទួល", callback_data="x"), "success")
        self.assertEqual(d(text="💳 បង់ប្រាក់ 1$", callback_data="pay"), "success")
        self.assertEqual(d(text="🚀 Hub", web_app=WebAppInfo("https://x")), "success")
        self.assertEqual(d(text="❌ បោះបង់", callback_data="x"), "danger")
        self.assertEqual(d(text="🗑️ លុប", callback_data="x"), "danger")
        self.assertEqual(d(text="📺 YouTube", url="https://y"), "primary")
        self.assertEqual(d(text="🏠 ម៉ឺនុយមេ", callback_data="nav:main"), "primary")
        self.assertIsNone(d(text="🎨 គូររូប", callback_data="x:img"))
        self.assertEqual(InlineKeyboardButton("❌ x", callback_data="x", style="primary").to_dict()["style"], "primary")
        bs._state["colors"] = False
        self.assertIsNone(d(text="✅ ទទួល", callback_data="x"))

    def test_learn_and_animate(self):
        text = "🔥 hi 🎤"
        ents = [SimpleNamespace(type="custom_emoji", offset=0, length=2, custom_emoji_id="111"),
                SimpleNamespace(type="custom_emoji", offset=6, length=2, custom_emoji_id="222")]
        self.assertEqual(bs.learn(text, ents), [("🔥", "111"), ("🎤", "222")])
        bs.set_emoji(True)
        out = bs.animate_html("<b>🔥 Hot</b> <code>🔥</code> <a href='x'>🎤</a> 🎤")
        self.assertEqual(out, '<b><tg-emoji emoji-id="111">🔥</tg-emoji> Hot</b> <code>🔥</code> <a href=\'x\'>🎤</a> '
                              '<tg-emoji emoji-id="222">🎤</tg-emoji>')
        d = InlineKeyboardButton("🎤 Rap", callback_data="x").to_dict()
        self.assertEqual((d["text"], d["icon_custom_emoji_id"]), ("Rap", "222"))

    def test_refused_falls_back_to_plain(self):
        bs.learn("🔥", [SimpleNamespace(type="custom_emoji", offset=0, length=2, custom_emoji_id="111")])
        bs.set_emoji(True)
        sent = []

        def fake(token, method_url, method="get", params=None, files=None, **kw):
            sent.append(dict(params))
            if "tg-emoji" in params.get("text", "") or "icon_custom_emoji_id" in params.get("reply_markup", ""):
                raise apihelper.ApiTelegramException("sendMessage", None, {"error_code": 400,
                                                                            "description": "Bad Request: custom emoji not allowed"})
            return {"message_id": 1, "date": 0, "chat": {"id": 5, "type": "private"}}
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🔥 Go", callback_data="x"))
        with mock.patch.object(apihelper, "_make_request", side_effect=fake):
            self.bot.send_message(5, "<b>🔥</b>", parse_mode="HTML", reply_markup=m)
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[1]["text"], "<b>🔥</b>")
        self.assertNotIn("icon_custom_emoji_id", sent[1]["reply_markup"])
        self.assertFalse(bs.emoji_on())


if __name__ == "__main__":
    unittest.main()
