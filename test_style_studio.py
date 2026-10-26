"""💇 Style Studio: catalog, prompts and the /style → pick → selfie → result flow (no network)."""
import time
import unittest
from types import SimpleNamespace

import style_studio


class FakeBot:
    def __init__(self):
        self.msg, self.cb, self.sent, self.photos_out = [], [], [], []

    def message_handler(self, commands=None, func=None, content_types=None, **kw):
        def deco(f):
            self.msg.append((commands, func, content_types or ["text"], f))
            return f
        return deco

    def callback_query_handler(self, func=None):
        def deco(f):
            self.cb.append((func, f))
            return f
        return deco

    def send_message(self, chat_id, text, **kw):
        self.sent.append(text)
        return SimpleNamespace(message_id=len(self.sent))

    reply_to = lambda self, m, text, **kw: self.send_message(m.chat.id, text, **kw)
    edit_message_text = lambda self, text, *a, **kw: self.sent.append(text)
    answer_callback_query = lambda self, *a, **kw: None
    send_chat_action = lambda self, *a: None
    delete_message = lambda self, *a: None
    get_file = lambda self, fid: SimpleNamespace(file_path="p/" + fid)
    download_file = lambda self, path: b"jpeg:" + path.encode()

    def send_photo(self, chat_id, data, caption=None, **kw):
        self.photos_out.append((data, caption))

    def dispatch(self, m):
        for commands, func, types, f in self.msg:
            if m.content_type not in types:
                continue
            if commands and (m.text or "").split()[0].lstrip("/") in commands:
                return f(m)
            if func and func(m):
                return f(m)

    def click(self, data, content_type="text"):
        call = SimpleNamespace(id="c", data=data, from_user=SimpleNamespace(id=7),
                               message=SimpleNamespace(chat=SimpleNamespace(id=7), message_id=1, content_type=content_type))
        for func, f in self.cb:
            if func(call):
                return f(call)


def msg(text=None, photo=False):
    m = SimpleNamespace(text=text, chat=SimpleNamespace(id=7, type="private"), from_user=SimpleNamespace(id=7),
                        content_type="photo" if photo else "text")
    if photo:
        m.photo = [SimpleNamespace(width=10, height=10, file_id="small"), SimpleNamespace(width=99, height=99, file_id="big")]
    return m


class StyleStudioTest(unittest.TestCase):
    def setUp(self):
        self.bot, self.runs, self.used = FakeBot(), [], []
        studio = {"enabled": lambda: True, "remaining": lambda uid: 3, "record": self.used.append, "busy": set(),
                  "error_text": lambda exc, uid: "error",
                  "run": lambda data, prompt: self.runs.append((data, prompt)) or b"OUT"}
        self.api = style_studio.register(self.bot, SimpleNamespace(), studio)

    def test_catalog_covers_every_group_and_kind(self):
        for g in style_studio.GROUPS:
            for k in style_studio.KINDS:
                n = sum(1 for v in style_studio.ITEMS.values() if v[0] == g and v[1] == k)
                self.assertGreaterEqual(n, 4, (g, k))
        for key in style_studio.ITEMS:
            self.assertLessEqual(len(f"sty:i:{key}"), 64)          # Telegram callback_data limit
            self.assertLessEqual(len(f"go_sty_{key}"), 64)         # /start payload limit

    def test_hair_prompt_keeps_face_and_changes_only_hair(self):
        p = style_studio.build_prompt("m_twoblock")
        self.assertIn("Change ONLY the hairstyle", p)
        self.assertIn("two-block", p)
        self.assertIn("same person", style_studio.build_prompt("w_cafe"))

    def test_pick_then_selfie_then_retry_without_resending(self):
        self.bot.dispatch(msg("/style"))
        self.assertIn("Style Studio", self.bot.sent[-1])
        self.bot.click("sty:g:w")
        self.bot.click("sty:k:w:hair")
        self.bot.click("sty:i:w_bob")
        self.assertIn("Selfie", self.bot.sent[-1])
        self.bot.dispatch(msg(photo=True))
        self.assertEqual(self.runs[-1][0], b"jpeg:p/big")            # largest photo
        self.assertIn("bob", self.runs[-1][1])
        self.assertEqual(len(self.bot.photos_out), 1)
        self.assertEqual(self.used, [7])
        self.bot.click("sty:i:w_pixie", content_type="photo")       # same selfie, another style
        self.assertEqual(len(self.runs), 2)
        self.assertIn("pixie", self.runs[-1][1])

    def test_hub_deep_link_goes_straight_to_selfie(self):
        self.bot.dispatch(msg("/style sty_k_pigtails"))
        self.assertEqual(self.api["pending"][7][0], "k_pigtails")
        self.bot.dispatch(msg("/style k_pigtails"))
        self.assertIn("Selfie", self.bot.sent[-1])

    def test_new_photo_button_forgets_the_selfie(self):
        self.api["photos"][7] = ("old", time.monotonic() + 60)
        self.bot.click("sty:new")
        self.assertNotIn(7, self.api["photos"])


if __name__ == "__main__":
    unittest.main()
