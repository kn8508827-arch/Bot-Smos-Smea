"""🎙️ Record Studio: limits (3 a day, 30 s), effects through real ffmpeg, AI re-voice, WAV download."""
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

import record_studio


def sample_ogg(seconds=3):
    exe = record_studio.ffmpeg_exe()
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "s.ogg")
        subprocess.run([exe, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=f=220:d={seconds}",
                        "-c:a", "libopus", out], check=True)
        return open(out, "rb").read()


class FakeBot:
    def __init__(self):
        self.msg, self.cb, self.sent, self.voices, self.docs = [], [], [], [], []

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
        return SimpleNamespace(message_id=1)

    reply_to = lambda self, m, text, **kw: self.send_message(1, text)
    answer_callback_query = lambda self, *a, **kw: None
    send_chat_action = lambda self, *a: None
    delete_message = lambda self, *a: None
    edit_message_reply_markup = lambda self, *a, **kw: None
    get_file = lambda self, fid: SimpleNamespace(file_path=fid)
    download_file = lambda self, p: sample_ogg()
    send_voice = lambda self, chat_id, data, caption=None, **kw: self.voices.append((data, caption))
    send_document = lambda self, chat_id, doc, caption=None, **kw: self.docs.append(doc)

    def dispatch(self, m):
        for commands, func, types, f in self.msg:
            if m.content_type not in types:
                continue
            if commands and m.text and m.text.split()[0].lstrip("/") in commands:
                return f(m)
            if func and func(m):
                return f(m)

    def click(self, data, uid=5):
        call = SimpleNamespace(id="c", data=data, from_user=SimpleNamespace(id=uid),
                               message=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2))
        for func, f in self.cb:
            if func(call):
                return f(call)


def msg(uid=5, text=None, seconds=None):
    m = SimpleNamespace(text=text, chat=SimpleNamespace(id=1, type="private"), from_user=SimpleNamespace(id=uid),
                        reply_to_message=None, content_type="voice" if seconds else "text", audio=None, video_note=None)
    m.voice = SimpleNamespace(file_id="v", duration=seconds) if seconds else None
    return m


class RecordStudioTest(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        self.bot, self.spoken = FakeBot(), []
        cfg = SimpleNamespace(BOT_DB_PATH=self.db, OWNER_ID=1, ADMIN_IDS=[])
        record_studio.register(
            self.bot, cfg, hear=lambda data, mime: '{"text": "សួស្ដី", "mood": "happy"}',
            speak=lambda text, voice, style: self.spoken.append((text, voice, style)) or record_studio.run_ffmpeg(
                sample_ogg(1), "", out="wav"))

    def test_record_clean_effect_ai_and_wav(self):
        self.bot.dispatch(msg(text="/record"))
        self.assertIn("3/3", self.bot.sent[-1])
        self.bot.dispatch(msg(seconds=3))
        self.assertEqual(len(self.bot.voices), 1)                    # clean version straight away
        self.assertIn("សម្អាត", self.bot.voices[0][1])
        self.bot.click("rec:fx:robot")
        self.assertIn("Robot", self.bot.voices[-1][1])
        self.bot.click("rec:ai:w_sweet")
        self.assertEqual(self.spoken[-1][:2], ("សួស្ដី", "Leda"))
        self.assertIn("happy", self.spoken[-1][2])
        self.bot.click("rec:wav")
        self.assertTrue(self.bot.docs[-1][1][:4] == b"RIFF")

    def test_member_limits(self):
        self.bot.dispatch(msg(text="/record"))
        self.bot.dispatch(msg(seconds=45))
        self.assertIn("30", self.bot.sent[-1])                       # too long, nothing counted
        for _ in range(3):
            self.bot.click("rec:new")
            self.bot.dispatch(msg(seconds=3))
        self.bot.click("rec:new")
        self.assertIn("អស់ 3", self.bot.sent[-1])

    def test_admin_has_no_limit(self):
        for _ in range(5):
            self.bot.click("rec:new", uid=1)
            self.bot.dispatch(msg(uid=1, seconds=90))
        self.assertEqual(len(self.bot.voices), 5)


if __name__ == "__main__":
    unittest.main()
