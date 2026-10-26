"""💵 /money: photo → AI with the banknote prompt; /money then a photo; never claims 100% real."""
import tempfile
import unittest
from types import SimpleNamespace

import daily_tools


class FakeBot:
    def __init__(self):
        self.handlers, self.replies = [], []

    def message_handler(self, commands=None, func=None, content_types=None, **kw):
        def deco(f):
            self.handlers.append((commands, func, content_types or ["text"], f))
            return f
        return deco

    callback_query_handler = lambda self, **kw: (lambda f: f)
    reply_to = lambda self, m, text, **kw: self.replies.append(text)
    send_chat_action = lambda self, *a: None
    get_file = lambda self, fid: SimpleNamespace(file_path=fid)
    download_file = lambda self, p: b"img"

    def dispatch(self, m):
        for commands, func, types, f in self.handlers:
            if m.content_type not in types:
                continue
            text = m.text or getattr(m, "caption", None) or ""
            if commands and text.split() and text.split()[0].lstrip("/") in commands and m.content_type == "text":
                return f(m)
            if func and func(m):
                return f(m)


def msg(text=None, photo=False, caption=None):
    m = SimpleNamespace(text=text, caption=caption, chat=SimpleNamespace(id=1, type="private"),
                        from_user=SimpleNamespace(id=5), reply_to_message=None,
                        content_type="photo" if photo else "text")
    if photo:
        m.photo = [SimpleNamespace(file_id="note")]
    return m


class MoneyTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        self.bot = FakeBot()
        daily_tools.register(self.bot, SimpleNamespace(BOT_DB_PATH=db),
                             ai=lambda parts, prompt: self.calls.append((parts, prompt)) or "💵 100 ដុល្លារ")

    def test_prompt_never_claims_genuine(self):
        self.assertIn("never say a note is real", daily_tools.MONEY_PROMPT)
        self.assertIn("FOR MOTION PICTURE USE", daily_tools.MONEY_PROMPT)

    def test_command_then_photo(self):
        self.bot.dispatch(msg("/money"))
        self.assertIn("ពិនិត្យលុយ", self.bot.replies[-1])
        self.bot.dispatch(msg(photo=True))
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0][0])                       # the photo went to the AI
        self.assertIn("banknotes", self.calls[0][1])
        self.assertIn("100 ដុល្លារ", self.bot.replies[-1])

    def test_caption(self):
        self.bot.dispatch(msg(photo=True, caption="/money"))
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
