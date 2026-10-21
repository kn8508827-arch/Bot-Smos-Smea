"""Offline coverage for community commands and SQLite behavior."""
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

import community_features
import config
import database


class FakeTelegramBot:
    def __init__(self):
        self.handlers = []
        self.replies = []
        self.sent_messages = []
        self.callback_answers = []

    def message_handler(self, *args, **kwargs):
        def register(handler):
            self.handlers.append(("message", kwargs, handler))
            return handler
        return register

    def callback_query_handler(self, *args, **kwargs):
        def register(handler):
            self.handlers.append(("callback", kwargs, handler))
            return handler
        return register

    def reply_to(self, message, text, **kwargs):
        self.replies.append((message, text, kwargs))

    def send_message(self, *args, **kwargs):
        self.sent_messages.append((args, kwargs))

    def answer_callback_query(self, *args, **kwargs):
        self.callback_answers.append((args, kwargs))

    def get_me(self):
        return SimpleNamespace(username="community_test_bot")


class TestCommunityFeatures(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="community-tests-", dir=os.getcwd())
        self.db_path = os.path.join(self.temp_dir.name, "community.db")
        self.old_config_db_path = config.BOT_DB_PATH
        self.old_admin_ids = config.ADMIN_IDS
        self.old_database_path = database.DB_PATH
        config.BOT_DB_PATH = self.db_path
        config.ADMIN_IDS = [99]
        database.DB_PATH = self.db_path
        database.init_db(self.db_path)
        self.bot = FakeTelegramBot()
        community_features.register(self.bot, database, config)

    def tearDown(self):
        config.BOT_DB_PATH = self.old_config_db_path
        config.ADMIN_IDS = self.old_admin_ids
        database.DB_PATH = self.old_database_path
        self.temp_dir.cleanup()

    def message_handler(self, command):
        for kind, options, handler in self.bot.handlers:
            if kind == "message" and command in options.get("commands", []):
                return handler
        self.fail(f"No /{command} handler was registered")

    def callback_handler(self):
        for kind, _options, handler in self.bot.handlers:
            if kind == "callback":
                return handler
        self.fail("No callback handler was registered")

    def make_message(self, user_id=1, first_name="Alice", text="/checkin"):
        user = SimpleNamespace(
            id=user_id, first_name=first_name, username=f"user{user_id}", last_name=None
        )
        chat = SimpleNamespace(id=user_id, type="private")
        return SimpleNamespace(from_user=user, chat=chat, message_id=10, text=text)

    def read_points(self, user_id):
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT points, vip FROM community_points WHERE user_id=?", (user_id,)
            ).fetchone()
        finally:
            conn.close()
        return row

    def test_checkin_awards_once_and_records_user_interaction(self):
        message = self.make_message()
        self.message_handler("checkin")(message)
        self.message_handler("checkin")(message)

        self.assertEqual(self.read_points(1)[0], community_features.POINTS_CHECKIN)
        with database.get_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT chat_type FROM users WHERE user_id=?", (1,)
            ).fetchone()
        self.assertEqual(row["chat_type"], "private")

    def test_quiz_awards_correct_answer_only_once_and_rejects_invalid_indices(self):
        message = self.make_message(text="/quiz")
        self.message_handler("quiz")(message)
        markup = self.bot.replies[-1][2]["reply_markup"]
        callback_data = markup.keyboard[0][0].callback_data
        _prefix, _kind, qid_text, _choice = callback_data.split(":")
        qid = int(qid_text)
        correct = community_features.QUIZ[qid][2]
        callback = SimpleNamespace(
            id="quiz-1",
            data=f"community:quiz:{qid}:{correct}",
            from_user=message.from_user,
            message=SimpleNamespace(chat=message.chat, message_id=message.message_id),
        )
        handler = self.callback_handler()
        handler(callback)
        handler(callback)
        self.assertEqual(self.read_points(1)[0], community_features.POINTS_QUIZ)

        invalid = SimpleNamespace(
            id="quiz-invalid",
            data="community:quiz:-1:0",
            from_user=message.from_user,
            message=callback.message,
        )
        handler(invalid)
        self.assertEqual(self.read_points(1)[0], community_features.POINTS_QUIZ)

    def test_referral_awards_both_users_once_and_registers_new_user(self):
        message = self.make_message(text="/start ref_202")
        handler = self.message_handler("start")
        handler(message)
        handler(message)

        self.assertEqual(self.read_points(1)[0], community_features.POINTS_REFERRAL)
        self.assertEqual(self.read_points(202)[0], community_features.POINTS_REFERRAL)
        with database.get_db(self.db_path) as conn:
            user = conn.execute("SELECT user_id FROM users WHERE user_id=?", (1,)).fetchone()
        self.assertEqual(user["user_id"], 1)

    def test_profile_and_leaderboard_escape_telegram_html(self):
        message = self.make_message(first_name="<b>Alice</b>", text="/profile")
        self.message_handler("profile")(message)
        self.assertIn("&lt;b&gt;Alice&lt;/b&gt;", self.bot.replies[-1][1])

        self.message_handler("leaderboard")(message)
        board = self.bot.replies[-1][1]
        self.assertIn("&lt;b&gt;Alice&lt;/b&gt;", board)
        self.assertNotIn("<b>Alice</b>", board)

    def test_community_menu_buttons_run_the_selected_action(self):
        message = self.make_message(text="/community")
        self.message_handler("community")(message)
        markup = self.bot.replies[-1][2]["reply_markup"]
        button = next(
            button
            for row in markup.keyboard
            for button in row
            if button.callback_data == "community:menu:checkin:0"
        )
        callback = SimpleNamespace(
            id="menu-1",
            data=button.callback_data,
            from_user=message.from_user,
            message=SimpleNamespace(chat=message.chat, message_id=message.message_id),
        )
        self.callback_handler()(callback)

        self.assertEqual(self.read_points(1)[0], community_features.POINTS_CHECKIN)
        self.assertIn("Check-in ជោគជ័យ", self.bot.replies[-1][1])

    def test_admin_can_grant_vip(self):
        message = self.make_message(user_id=99, first_name="Admin", text="/communityadmin vip 1")
        self.message_handler("communityadmin")(message)
        self.assertEqual(self.read_points(1)[1], 1)


if __name__ == "__main__":
    unittest.main()
