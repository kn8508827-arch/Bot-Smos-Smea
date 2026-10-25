"""TPNews live: channel page parsing, first run quiet, new posts → subscribers, Hub data."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import telebot

import tpnews_feed as tf

PAGE = """
<div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="TPNEWSREPORT/101" data-view="x">
 <a class="tgme_widget_message_photo_wrap 1 blured" style="width:800px;background-image:url('https://cdn.t.me/a.jpg')"></a>
 <div class="tgme_widget_message_text js-message_text" dir="auto">ព័ត៌មានទី១<br/>លម្អិត</div>
 <time datetime="2026-10-25T13:00:00+00:00" class="time">20:00</time></div>
<div class="tgme_widget_message js-widget_message" data-post="TPNEWSREPORT/102">
 <div class="tgme_widget_message_text js-message_text" dir="auto">ព័ត៌មានទី២ &amp; ថ្មី</div>
 <time datetime="2026-10-25T14:00:00+00:00" class="time">21:00</time></div>
"""


class TPNewsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.config = SimpleNamespace(BOT_DB_PATH=self.tmp.name, OWNER_ID=1, ADMIN_IDS=[])
        self.bot = telebot.TeleBot("1:T", threaded=False)
        for n in ("send_message", "send_photo", "reply_to", "answer_callback_query"):
            setattr(self.bot, n, MagicMock())
        self.posts = tf.parse_channel_page(PAGE, "TPNEWSREPORT")
        self.feed = lambda: self.posts
        self.api = tf.register(self.bot, self.config, fetch=lambda: self.feed())

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_parse(self):
        self.assertEqual([p["id"] for p in self.posts], [101, 102])
        self.assertEqual(self.posts[0]["photo"], "https://cdn.t.me/a.jpg")
        self.assertEqual(self.posts[0]["text"], "ព័ត៌មានទី១\nលម្អិត")
        self.assertEqual(self.posts[1]["text"], "ព័ត៌មានទី២ & ថ្មី")

    def test_first_run_quiet_then_new_posts_go_to_subscribers(self):
        with tf._db(self.config) as c:
            c.execute("INSERT INTO tp_subs(chat_id, since) VALUES (55, 0)")
        self.assertEqual(self.api["tick"](), 0)
        self.bot.send_photo.assert_not_called()
        new = {"id": 103, "text": "ព័ត៌មានទី៣", "photo": "", "ts": 1}
        self.feed = lambda: self.posts + [new]
        self.assertEqual(self.api["tick"](), 1)
        self.assertEqual(self.bot.send_message.call_args.args[0], 55)
        self.assertEqual(self.api["tick"](), 0)          # not sent twice

    def test_latest_for_hub(self):
        self.api["tick"]()
        self.assertEqual(tf.latest(self.config, 1)[0][:2], [102, "ព័ត៌មានទី២ & ថ្មី"])


if __name__ == "__main__":
    unittest.main()
