"""Live interpreter: session, Khmer ⇄ other language, text + voice reply."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import telebot

import live_translate as lt


def msg(uid, text=None, ctype="text"):
    return SimpleNamespace(from_user=SimpleNamespace(id=uid), chat=SimpleNamespace(id=uid, type="private"),
                           text=text, content_type=ctype, message_id=1, entities=None, voice=None, audio=None)


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.bot = telebot.TeleBot("1:T", threaded=False)
        for n in ("send_message", "reply_to", "send_voice", "send_audio", "send_chat_action", "answer_callback_query"):
            setattr(self.bot, n, MagicMock())
        self.ai = MagicMock(return_value='{"heard": "សួស្ដី", "from": "km", "translation": "你好"}')
        self.api = lt.register(self.bot, SimpleNamespace(OWNER_ID=1, ADMIN_IDS=[]), ai=self.ai, tts=lambda t: b"WAV")

    def test_parse_reply(self):
        self.assertEqual(lt.parse_reply('x {"heard":"a","from":"km","translation":"b"} y')["translation"], "b")
        self.assertIsNone(lt.parse_reply("sorry"))

    def test_text_turn_translates_only_in_session(self):
        self.bot.process_new_messages([msg(5, "សួស្ដី")])
        self.ai.assert_not_called()
        self.api["sessions"][5] = ("zh", 10 ** 12)
        with patch("voice_studio.wav_to_ogg", return_value=b"OGG"), patch("voice_studio.wav_seconds", return_value=1):
            self.bot.process_new_messages([msg(5, "សួស្ដី")])
        self.assertIn("你好", self.bot.reply_to.call_args.args[1])
        self.bot.send_voice.assert_called_once()

    def test_commands_pass_through(self):
        self.api["sessions"][5] = ("en", 10 ** 12)
        self.bot.process_new_messages([msg(5, "/menu")])
        self.ai.assert_not_called()


if __name__ == "__main__":
    unittest.main()
