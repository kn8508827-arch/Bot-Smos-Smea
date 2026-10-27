"""🔊 ថ្ងៃសីល reminders come with a Khmer voice message (made once, re-sent by file_id)."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import telebot

import buddhist_days as bd
import khmer_calendar as kc


def next_sila_eve():
    d = datetime(2026, 10, 27, tzinfo=bd.TZ_KH).date()
    while not kc.is_sila_day(d + timedelta(days=1)):
        d += timedelta(days=1)
    return datetime(d.year, d.month, d.day, 18, 5, tzinfo=bd.TZ_KH)


class SilaVoiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config = SimpleNamespace(BOT_DB_PATH=os.path.join(self.tmp, "t.db"), OWNER_ID=1, ADMIN_IDS=[])
        self.bot = telebot.TeleBot("1:T", threaded=False)
        self.bot.send_message = mock.Mock()
        self.voices = []

        def send_voice(chat_id, voice, caption=None):
            self.voices.append((chat_id, voice))
            return SimpleNamespace(voice=SimpleNamespace(file_id="FILE123"))
        self.bot.send_voice = send_voice
        self.spoken = []
        self.api = bd.register(self.bot, self.config, speak=lambda t: self.spoken.append(t) or b"OGGDATA")
        conn = bd._db(self.config)
        with conn:
            for cid in (10, 20, 30):
                conn.execute("INSERT INTO sila_subs(chat_id, created_at) VALUES (?, 0)", (cid,))
        conn.close()

    def test_eve_voice_made_once(self):
        with mock.patch.dict(os.environ, {"SILA_VOICE": "Sulafat"}):
            sent = self.api.tick(next_sila_eve())
        self.assertEqual(sent, 3)
        self.assertEqual(len(self.spoken), 1)
        self.assertIn("ស្អែកនេះ", self.spoken[0])
        self.assertEqual([v[1] for v in self.voices], [b"OGGDATA", "FILE123", "FILE123"])
        # second tick the same evening sends nothing again
        self.assertEqual(self.api.tick(next_sila_eve() + timedelta(minutes=5)), 0)

    def test_voice_off_and_tts_failure(self):
        with mock.patch.dict(os.environ, {"SILA_VOICE": "off"}):
            self.api.tick(next_sila_eve())
        self.assertEqual(self.voices, [])
        self.assertEqual(self.bot.send_message.call_count, 3)

    def test_tts_failure_keeps_text(self):
        api = bd.register(self.bot, self.config, speak=mock.Mock(side_effect=RuntimeError("down")))
        api.tick(next_sila_eve())
        self.assertEqual(self.voices, [])
        self.assertEqual(self.bot.send_message.call_count, 3)


if __name__ == "__main__":
    unittest.main()
