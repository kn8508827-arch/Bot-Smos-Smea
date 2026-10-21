"""Offline tests for Daily Care preferences, scheduling, and delivery safety."""
import asyncio
import importlib
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import ai_assistant
import config
import database
import messages
import scheduler


class FakeTelegramBot:
    def __init__(self, *args, **kwargs):
        self.handlers = []
        self.send_message = MagicMock()
        self.reply_to = MagicMock()
        self.answer_callback_query = MagicMock()
        self.set_my_commands = MagicMock()

    def message_handler(self, *args, **kwargs):
        def decorate(handler):
            self.handlers.append(("message", args, kwargs, handler))
            return handler
        return decorate

    def callback_query_handler(self, *args, **kwargs):
        def decorate(handler):
            self.handlers.append(("callback", args, kwargs, handler))
            return handler
        return decorate


class TestDailyCareDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "dailycare.db")
        database.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def register(self, user_id, chat_id=None, chat_type="private"):
        database.register_or_update_user(
            user_id=user_id,
            chat_id=chat_id if chat_id is not None else user_id,
            username=f"user{user_id}",
            first_name="Dara",
            last_name=None,
            db_path=self.db_path,
            chat_type=chat_type,
        )

    def test_existing_schema_migrates_without_losing_users(self):
        legacy_path = os.path.join(self.temp_dir.name, "legacy.db")
        with database.get_db(legacy_path) as conn:
            conn.execute("""
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    status TEXT DEFAULT 'active',
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                "INSERT INTO users (user_id, chat_id, username, first_name) VALUES (7, 7, 'seven', 'Seven')"
            )
            conn.commit()

        database.init_db(legacy_path)
        with database.get_db(legacy_path) as conn:
            row = conn.execute(
                "SELECT user_id, first_seen, last_seen, chat_type FROM users WHERE user_id = 7"
            ).fetchone()
        self.assertEqual(row["user_id"], 7)
        self.assertIsNotNone(row["first_seen"])
        self.assertIsNotNone(row["last_seen"])
        self.assertEqual(row["chat_type"], "private")

    def test_notifications_default_off_and_only_private_opt_ins_are_eligible(self):
        self.register(1)
        self.register(2, chat_id=-1002, chat_type="group")
        self.register(3)
        self.assertFalse(database.get_notification_enabled(1, self.db_path))

        database.set_notification_enabled(1, True, self.db_path)
        database.set_notification_enabled(2, True, self.db_path)
        database.set_notification_enabled(3, True, self.db_path)
        database.mark_user_inactive(3, self.db_path)

        recipients = database.get_eligible_notification_recipients(self.db_path)
        self.assertEqual([user["user_id"] for user in recipients], [1])

    def test_delivery_claim_is_unique_per_user_date_and_category(self):
        claimed, first_key = database.claim_notification_delivery(11, 11, "good_morning", "2026-10-19", self.db_path)
        claimed_again, second_key = database.claim_notification_delivery(11, 11, "good_morning", "2026-10-19", self.db_path)
        next_slot, third_key = database.claim_notification_delivery(11, 11, "lunch_care", "2026-10-19", self.db_path)
        next_day, fourth_key = database.claim_notification_delivery(11, 11, "good_morning", "2026-10-20", self.db_path)

        self.assertTrue(claimed)
        self.assertFalse(claimed_again)
        self.assertTrue(next_slot)
        self.assertTrue(next_day)
        self.assertEqual(first_key, second_key)
        self.assertEqual(len({first_key, third_key, fourth_key}), 3)

        database.init_db(self.db_path)
        claimed_after_restart, _ = database.claim_notification_delivery(
            11, 11, "good_morning", "2026-10-19", self.db_path
        )
        self.assertFalse(claimed_after_restart)

    def test_concurrent_claims_and_scheduler_lease_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(
                lambda _index: database.claim_notification_delivery(
                    22, 22, "lunch_care", "2026-10-19", self.db_path
                )[0],
                range(8),
            ))
        self.assertEqual(sum(claims), 1)

        self.assertTrue(database.acquire_scheduler_lock("owner-a", ttl_seconds=30, db_path=self.db_path, now=100))
        self.assertFalse(database.acquire_scheduler_lock("owner-b", ttl_seconds=30, db_path=self.db_path, now=110))
        self.assertTrue(database.heartbeat_scheduler_lock("owner-a", ttl_seconds=30, db_path=self.db_path, now=115))
        self.assertFalse(database.acquire_scheduler_lock("owner-b", ttl_seconds=30, db_path=self.db_path, now=130))
        database.release_scheduler_lock("owner-a", self.db_path)
        self.assertTrue(database.acquire_scheduler_lock("owner-b", ttl_seconds=30, db_path=self.db_path, now=131))

    def test_group_interaction_does_not_replace_a_private_dm_route(self):
        self.register(33, chat_id=33, chat_type="private")
        self.register(33, chat_id=-10033, chat_type="group")
        database.set_notification_enabled(33, True, self.db_path)
        recipient = database.get_eligible_notification_recipients(self.db_path)[0]
        self.assertEqual(recipient["chat_id"], 33)


class TestDailyCareScheduler(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "dailycare.db")
        database.init_db(self.db_path)
        self._old_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        self._old_batch_size = config.DAILYCARE_BATCH_SIZE
        config.DAILYCARE_MAX_SEND_ATTEMPTS = 3
        config.DAILYCARE_RETRY_MAX_WAIT_SECONDS = 30
        config.DAILYCARE_RATE_LIMIT_SECONDS = 0

    def tearDown(self):
        database.DB_PATH = self._old_db_path
        config.DAILYCARE_BATCH_SIZE = self._old_batch_size
        self.temp_dir.cleanup()

    def add_recipient(self, user_id, enabled=True, chat_type="private"):
        database.register_or_update_user(
            user_id, user_id, f"user{user_id}", "Dara", None,
            db_path=self.db_path, chat_type=chat_type,
        )
        if enabled:
            database.set_notification_enabled(user_id, True, self.db_path)

    def test_six_jobs_use_cambodia_timezone_and_requested_times(self):
        class RecordingScheduler:
            def __init__(self):
                self.jobs = []

            def add_job(self, func, **kwargs):
                self.jobs.append((func, kwargs))

        fake_scheduler = RecordingScheduler()
        scheduler.register_dailycare_jobs(fake_scheduler, object())

        self.assertEqual(len(fake_scheduler.jobs), 6)
        start = datetime(2026, 10, 19, 0, 0, tzinfo=scheduler.CAMBODIA_TIMEZONE)
        actual_times = []
        for _func, job in fake_scheduler.jobs:
            trigger = job["trigger"]
            self.assertEqual(getattr(trigger.timezone, "key", None), "Asia/Phnom_Penh")
            next_fire = trigger.get_next_fire_time(None, start)
            actual_times.append((next_fire.hour, next_fire.minute))
        self.assertEqual(actual_times, [(7, 0), (12, 0), (17, 30), (19, 0), (21, 0), (22, 0)])

    def test_scheduler_retries_lease_after_overlapping_deployment(self):
        started = threading.Event()

        class RuntimeScheduler:
            def __init__(self, *args, **kwargs):
                self.running = False

            def add_job(self, *args, **kwargs):
                pass

            def start(self):
                self.running = True
                started.set()

            def shutdown(self, wait=False):
                self.running = False

        retry_stop = scheduler._scheduler_retry_stop
        retry_stop.clear()
        with patch.object(scheduler, "_scheduler_instance", None), \
             patch.object(scheduler, "_scheduler_lock_owner", None), \
             patch.object(scheduler, "_scheduler_retry_thread", None), \
             patch.object(scheduler, "_SCHEDULER_LOCK_RETRY_INTERVAL_SECONDS", 0.01), \
             patch.object(scheduler, "is_persistent_storage_ready", return_value=True), \
             patch.object(scheduler, "BackgroundScheduler", RuntimeScheduler), \
             patch.object(scheduler, "register_dailycare_jobs"), \
             patch.object(database, "acquire_scheduler_lock", side_effect=[False, True]) as acquire:
            try:
                self.assertIsNone(scheduler.start_scheduler(object()))
                self.assertTrue(started.wait(1))
                deadline = time.monotonic() + 1
                while scheduler._scheduler_instance is None and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertIsNotNone(scheduler._scheduler_instance)
                self.assertEqual(acquire.call_count, 2)
            finally:
                scheduler.shutdown_scheduler(scheduler._scheduler_instance)


    def test_storage_guard_requires_database_path_inside_railway_volume(self):
        env = {
            "RAILWAY_PROJECT_ID": "project",
            "RAILWAY_VOLUME_MOUNT_PATH": "C:\\app\\data",
        }
        self.assertTrue(
            scheduler.is_persistent_storage_ready("C:\\app\\data\\bot.db", environ=env)
        )
        self.assertFalse(
            scheduler.is_persistent_storage_ready("C:\\app\\bot.db", environ=env)
        )
        self.assertFalse(
            scheduler.is_persistent_storage_ready("C:\\app\\bot.db", environ={"RAILWAY_PROJECT_ID": "project"})
        )
        self.assertTrue(scheduler.is_persistent_storage_ready("bot.db", environ={}))

    def test_delivery_is_once_per_opted_in_user_and_slot(self):
        self.add_recipient(101, enabled=True)
        self.add_recipient(102, enabled=False)
        database.set_dailycare_enabled(True, self.db_path)
        fake_bot = SimpleNamespace(send_message=MagicMock())
        generator = MagicMock(return_value="សារថ្មី")
        run_at = datetime(2026, 10, 19, 7, 0, tzinfo=scheduler.CAMBODIA_TIMEZONE)

        first = scheduler.send_dailycare_slot(
            fake_bot, "good_morning", now=run_at, db_path=self.db_path,
            message_generator=generator, rate_limit_seconds=0,
        )
        second = scheduler.send_dailycare_slot(
            fake_bot, "good_morning", now=run_at, db_path=self.db_path,
            message_generator=generator, rate_limit_seconds=0,
        )

        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["duplicate"], 1)
        fake_bot.send_message.assert_called_once_with(101, "សារថ្មី")
        self.assertEqual(database.get_notification_delivery_stats("2026-10-19", self.db_path), {"sent": 1})

    def test_recipient_processing_is_keyset_paginated_in_configured_batches(self):
        for user_id in range(201, 206):
            self.add_recipient(user_id)
        database.set_dailycare_enabled(True, self.db_path)
        config.DAILYCARE_BATCH_SIZE = 2
        fake_bot = SimpleNamespace(send_message=MagicMock())
        result = scheduler.send_dailycare_slot(
            fake_bot, "lunch_care", now=datetime(2026, 10, 19, tzinfo=scheduler.CAMBODIA_TIMEZONE),
            db_path=self.db_path, message_generator=MagicMock(return_value="text"), rate_limit_seconds=0,
        )
        self.assertEqual(result["eligible"], 5)
        self.assertEqual(result["sent"], 5)
        self.assertEqual(fake_bot.send_message.call_count, 5)

    def test_global_switch_off_prevents_any_send(self):
        self.add_recipient(111)
        fake_bot = SimpleNamespace(send_message=MagicMock())
        result = scheduler.send_dailycare_slot(
            fake_bot, "lunch_care", now=datetime(2026, 10, 19, tzinfo=scheduler.CAMBODIA_TIMEZONE),
            db_path=self.db_path, message_generator=MagicMock(return_value="text"), rate_limit_seconds=0,
        )
        self.assertEqual(result, {"disabled": 1})
        fake_bot.send_message.assert_not_called()

    def test_retry_after_is_bounded_and_success_is_recorded(self):
        self.add_recipient(121)
        database.set_dailycare_enabled(True, self.db_path)

        class RateLimitError(Exception):
            retry_after = 2
            error_code = 429

        fake_bot = SimpleNamespace(
            send_message=MagicMock(side_effect=[RateLimitError("rate limited"), None])
        )
        sleep_fn = MagicMock()
        result = scheduler.send_dailycare_slot(
            fake_bot, "dinner_care", now=datetime(2026, 10, 19, tzinfo=scheduler.CAMBODIA_TIMEZONE),
            db_path=self.db_path, message_generator=MagicMock(return_value="text"),
            sleep_fn=sleep_fn, rate_limit_seconds=0,
        )
        self.assertEqual(result["sent"], 1)
        self.assertEqual(fake_bot.send_message.call_count, 2)
        sleep_fn.assert_called_once_with(2.0)
        self.assertEqual(database.get_notification_delivery_stats("2026-10-19", self.db_path), {"sent": 1})

    def test_permanent_telegram_failure_disables_recipient(self):
        self.add_recipient(131)
        database.set_dailycare_enabled(True, self.db_path)

        class ForbiddenError(Exception):
            error_code = 403

        fake_bot = SimpleNamespace(send_message=MagicMock(side_effect=ForbiddenError("blocked")))
        result = scheduler.send_dailycare_slot(
            fake_bot, "rest_reminder", now=datetime(2026, 10, 19, tzinfo=scheduler.CAMBODIA_TIMEZONE),
            db_path=self.db_path, message_generator=MagicMock(return_value="text"), rate_limit_seconds=0,
        )
        self.assertEqual(result["blocked"], 1)
        self.assertFalse(database.get_notification_enabled(131, self.db_path))
        self.assertEqual(database.get_eligible_notification_recipients(self.db_path), [])

    def test_ambiguous_transport_error_is_not_retried_to_avoid_duplicate_dm(self):
        self.add_recipient(141)
        database.set_dailycare_enabled(True, self.db_path)
        fake_bot = SimpleNamespace(send_message=MagicMock(side_effect=TimeoutError("network timeout")))
        result = scheduler.send_dailycare_slot(
            fake_bot, "good_night", now=datetime(2026, 10, 19, tzinfo=scheduler.CAMBODIA_TIMEZONE),
            db_path=self.db_path, message_generator=MagicMock(return_value="text"), rate_limit_seconds=0,
        )
        self.assertEqual(result["failed"], 1)
        fake_bot.send_message.assert_called_once()

    def test_gemini_failure_uses_category_fallback_without_user_quota_path(self):
        config.GEMINI_API_KEY = "test-key"
        with patch.object(ai_assistant, "generate_gemini_response", new=AsyncMock(side_effect=RuntimeError("offline"))) as generate:
            result = scheduler.generate_care_message("good_morning")
        self.assertEqual(result, messages.DAILYCARE_FALLBACK_MESSAGES["good_morning"])
        kwargs = generate.call_args.kwargs
        self.assertIsNone(kwargs["user_id"])
        self.assertEqual(kwargs["history"], [])

    def test_gemini_success_uses_one_shared_slot_message(self):
        config.GEMINI_API_KEY = "test-key"
        with patch.object(
            ai_assistant, "generate_gemini_response", new=AsyncMock(return_value="សារថែទាំថ្មី")
        ) as generate:
            result = scheduler.generate_care_message("lunch_care")
        self.assertEqual(result, "សារថែទាំថ្មី")
        generate.assert_awaited_once()


class TestBotInteractionRegistration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "bot.db")
        database.init_db(self.db_path)
        self._old_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        self.fake_bot = FakeTelegramBot()
        self._old_bot_module = sys.modules.pop("bot", None)
        with patch.object(config, "BOT_TOKEN", "test-token"), patch("telebot.TeleBot", return_value=self.fake_bot):
            self.bot_module = importlib.import_module("bot")

    def tearDown(self):
        sys.modules.pop("bot", None)
        if self._old_bot_module is not None:
            sys.modules["bot"] = self._old_bot_module
        database.DB_PATH = self._old_db_path
        self.temp_dir.cleanup()

    def message(self, user_id, chat_id=None, chat_type="private"):
        return SimpleNamespace(
            from_user=SimpleNamespace(
                id=user_id, username=f"user{user_id}", first_name="Sophea", last_name="Keo"
            ),
            chat=SimpleNamespace(id=chat_id if chat_id is not None else user_id, type=chat_type),
        )

    def test_start_and_other_message_handlers_register_real_users(self):
        self.bot_module.send_welcome(self.message(201))
        self.bot_module.send_menu(self.message(202, chat_id=-100202, chat_type="group"))

        with database.get_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT user_id, chat_id, chat_type, first_seen, last_seen FROM users ORDER BY user_id"
            ).fetchall()
        self.assertEqual([row["user_id"] for row in rows], [201, 202])
        self.assertEqual(rows[0]["chat_type"], "private")
        self.assertEqual(rows[1]["chat_type"], "group")
        self.assertIsNotNone(rows[0]["first_seen"])
        self.assertIsNotNone(rows[0]["last_seen"])

    def test_generic_incoming_message_handler_registers_user(self):
        message = self.message(203)
        message.text = "សួស្តី"
        @self.bot_module.registered_message_handler(func=lambda _message: True)
        def dummy_handler(_message):
            return "handled"

        self.assertEqual(dummy_handler(message), "handled")
        self.assertEqual(database.get_active_users(self.db_path)[0]["user_id"], 203)

    def test_callback_interaction_registers_user_and_notification_opt_in(self):
        call = SimpleNamespace(
            id="cb-1",
            data="notifications:enable",
            from_user=SimpleNamespace(id=211, username="member", first_name="Bopha", last_name=None),
            message=SimpleNamespace(chat=SimpleNamespace(id=211, type="private")),
        )
        self.bot_module.callback_query(call)

        self.assertTrue(database.get_notification_enabled(211, self.db_path))
        self.assertEqual(database.get_eligible_notification_recipients(self.db_path)[0]["user_id"], 211)

        call.data = "notifications:disable"
        self.bot_module.callback_query(call)
        self.assertFalse(database.get_notification_enabled(211, self.db_path))

    def test_admin_command_can_enable_global_switch_but_non_admin_callback_cannot(self):
        with patch.object(config, "ADMIN_IDS", [999]), patch.object(
            scheduler, "is_persistent_storage_ready", return_value=True
        ):
            admin_message = self.message(999)
            self.bot_module.dailycare_admin_command(admin_message)
            admin_call = SimpleNamespace(
                id="admin-cb", data="dailycare:enable",
                from_user=admin_message.from_user,
                message=SimpleNamespace(chat=admin_message.chat),
            )
            self.bot_module.callback_query(admin_call)
            self.assertTrue(database.get_dailycare_enabled(self.db_path))

            user_call = SimpleNamespace(
                id="user-cb", data="dailycare:disable",
                from_user=SimpleNamespace(id=212, username="member", first_name="Member", last_name=None),
                message=SimpleNamespace(chat=SimpleNamespace(id=212, type="private")),
            )
            self.bot_module.callback_query(user_call)
            self.assertTrue(database.get_dailycare_enabled(self.db_path))

    def test_dailycare_command_is_admin_only(self):
        with patch.object(config, "ADMIN_IDS", [999]):
            self.bot_module.dailycare_admin_command(self.message(212))
        self.fake_bot.reply_to.assert_called_once()
        self.assertIn(messages.DAILYCARE_ADMIN_ONLY, self.fake_bot.reply_to.call_args.args[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
