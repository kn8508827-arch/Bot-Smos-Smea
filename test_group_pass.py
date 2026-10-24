"""Tests: only paying members (1$) holding a 5$ group pass can add the bot to a group;
non-members can only chat in plain text."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import telebot

import subscription

OWNER = 1
MEMBER = 200
STRANGER = 300
GROUP = -100555


def make_bot():
    bot = telebot.TeleBot("123:TEST", threaded=False)
    bot.send_message = MagicMock()
    bot.send_photo = MagicMock()
    bot.leave_chat = MagicMock()
    bot.answer_callback_query = MagicMock()
    bot.get_me = MagicMock(return_value=SimpleNamespace(username="TeamSmosSneabot"))
    bot.get_chat_member = MagicMock(return_value=SimpleNamespace(status="member"))
    return bot


def join_update(adder, old="left", new="member", chat_type="supergroup"):
    return SimpleNamespace(chat=SimpleNamespace(id=GROUP, type=chat_type, title="Fan Club"),
                           from_user=SimpleNamespace(id=adder),
                           old_chat_member=SimpleNamespace(status=old),
                           new_chat_member=SimpleNamespace(status=new))


def private_msg(uid, text, content_type="text"):
    return SimpleNamespace(from_user=SimpleNamespace(id=uid), chat=SimpleNamespace(id=uid, type="private"),
                           text=text, content_type=content_type)


class GroupPassTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        os.environ.pop("SUBSCRIPTION_ENABLED", None)
        os.environ.pop("SUB_FREE_GROUPS", None)
        self.config = SimpleNamespace(BOT_DB_PATH=self.tmp.name, OWNER_ID=OWNER, ADMIN_IDS=[])
        self.bot = make_bot()
        self.api = subscription.register(self.bot, self.config)
        self.join = self.bot._cooler_on_bot_added

    def tearDown(self):
        os.unlink(self.tmp.name)

    def texts_to(self, chat_id):
        return " ".join(str(c.args[1]) for c in self.bot.send_message.call_args_list if c.args[0] == chat_id)

    # --- adding the bot ---

    def test_stranger_is_refused_and_bot_leaves(self):
        self.join(join_update(STRANGER))
        self.bot.leave_chat.assert_called_once_with(GROUP)
        self.assertFalse(subscription.is_active(self.config, "g", GROUP))
        self.assertIn("សមាជិក", self.texts_to(STRANGER))

    def test_member_without_pass_is_refused(self):
        subscription.extend(self.config, "u", MEMBER, 30)
        self.join(join_update(MEMBER))
        self.bot.leave_chat.assert_called_once_with(GROUP)
        self.assertFalse(subscription.is_active(self.config, "g", GROUP))

    def test_member_with_pass_activates_group_and_uses_pass(self):
        subscription.extend(self.config, "u", MEMBER, 30)
        self.assertEqual(subscription.extend(self.config, "c", MEMBER, 30), 1)
        self.join(join_update(MEMBER))
        self.bot.leave_chat.assert_not_called()
        self.assertTrue(subscription.is_active(self.config, "g", GROUP))
        self.assertEqual(subscription.credits(self.config, MEMBER), 0)

    def test_pass_without_active_membership_is_refused_and_kept(self):
        subscription.add_credit(self.config, STRANGER, 1)
        self.join(join_update(STRANGER))
        self.bot.leave_chat.assert_called_once_with(GROUP)
        self.assertEqual(subscription.credits(self.config, STRANGER), 1)

    def test_owner_and_paid_group_and_promotion_are_untouched(self):
        self.join(join_update(OWNER))
        subscription.extend(self.config, "g", GROUP, 30)
        self.join(join_update(STRANGER))
        self.join(join_update(STRANGER, old="member", new="administrator"))
        self.bot.leave_chat.assert_not_called()

    def test_disabled_subscription_never_leaves(self):
        os.environ["SUBSCRIPTION_ENABLED"] = "false"
        try:
            self.join(join_update(STRANGER))
        finally:
            os.environ.pop("SUBSCRIPTION_ENABLED")
        self.bot.leave_chat.assert_not_called()

    def test_pass_is_used_once(self):
        subscription.add_credit(self.config, MEMBER, 1)
        self.assertTrue(subscription.use_credit(self.config, MEMBER))
        self.assertFalse(subscription.use_credit(self.config, MEMBER))

    # --- non-members: text chat only ---

    def test_non_member_plain_text_passes_but_marked_text_only(self):
        msg = private_msg(STRANGER, "សួស្ដី Bot")
        self.assertFalse(self.api.blocked_message(msg))
        self.assertTrue(getattr(msg, "_cooler_text_only", False))

    def test_non_member_features_photos_and_old_free_tools_are_blocked(self):
        for msg in (private_msg(STRANGER, "/imagine cat"), private_msg(STRANGER, "/health"),
                    private_msg(STRANGER, "/calendar"), private_msg(STRANGER, None, "photo")):
            self.assertTrue(self.api.blocked_message(msg), msg.text)

    def test_non_member_can_still_pay_and_start(self):
        for cmd in ("/start", "/pay", "/addgroup", "/sos", "/follow"):
            self.assertFalse(self.api.blocked_message(private_msg(STRANGER, cmd)), cmd)

    def test_member_is_not_text_only(self):
        subscription.extend(self.config, "u", MEMBER, 30)
        msg = private_msg(MEMBER, "សួស្ដី")
        self.assertFalse(self.api.blocked_message(msg))
        self.assertFalse(getattr(msg, "_cooler_text_only", False))

    def test_group_pass_counts_as_revenue(self):
        with subscription._connect(self.config) as conn:
            conn.execute("INSERT INTO sub_payments(kind, target_id, payer_id, status, created_at, decided_at) "
                         "VALUES ('c', ?, ?, 'approved', 1, strftime('%s','now'))", (MEMBER, MEMBER))
        st = subscription.stats(self.config)
        self.assertEqual(st.rev_today, 5.0)
        self.assertEqual(st.new_g, 1)


if __name__ == "__main__":
    unittest.main()
