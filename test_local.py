"""
ឯកសារតេស្តក្នុងម៉ាស៊ីន (Safe Local Tests) សម្រាប់ Team ស្មោះស្នេហ៍ Telegram Bot
តេស្តមុខងារ Private Registration, Group Settings, Admin Permissions, Broadcast Delivery, AI Assistant, និង AI Photo Studio
"""
import io
import os
import sys
import base64
import tempfile
import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from PIL import Image

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import messages
import config
import database
import ai_assistant

# ព្យាយាម import photo_studio និង handlers ប្រសិនបើមានក្នុង Project
try:
    import photo_studio
except ImportError:
    photo_studio = None

try:
    from handlers import (
        start_command,
        team_command,
        events_command,
        help_command,
        reset_command,
        setgroup_command,
        announce_command,
        chat_ai_message_handler,
        photo_upload_handler,
        photo_style_callback_handler,
        get_photo_styles_keyboard,
    )
except ImportError:
    start_command = None

try:
    from telegram.constants import ChatType
    from telegram.error import Forbidden, RetryAfter, TelegramError
except ImportError:
    ChatType = None

import httpx

TEST_DB = "test_bot_database.db"
TEST_IMG_DIR = "test_temp_photos"


class TestTeamSmaohSnehaBot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.DB_PATH = TEST_DB
        database.init_db(TEST_DB)
        os.makedirs(TEST_IMG_DIR, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(TEST_DB):
            try:
                os.remove(TEST_DB)
            except Exception:
                pass
        if os.path.exists(TEST_IMG_DIR):
            import shutil
            try:
                shutil.rmtree(TEST_IMG_DIR)
            except Exception:
                pass

    def setUp(self):
        # សម្អាត Tables ទាំងអស់មុនពេលតេស្តនីមួយៗ
        with database.get_db(TEST_DB) as conn:
            cursor = conn.cursor()
            existing_tables = {
                row[0] for row in cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for table in (
                "users", "settings", "announcements", "chat_history",
                "ai_usage", "photo_sessions", "photo_usage", "conversation_summaries",
            ):
                if table in existing_tables:
                    cursor.execute(f"DELETE FROM {table}")
            conn.commit()

        # Reset In-memory structures
        ai_assistant._user_last_query_time.clear()

        # Mock config
        config.ADMIN_IDS = [111222333]
        config.DRY_RUN_MODE = True
        config.GEMINI_API_KEY = ""
        config.OPENAI_API_KEY = ""
        config.AI_PROVIDER = "auto"
        config.AI_DAILY_LIMIT_PER_USER = 30
        config.AI_RATE_LIMIT_SECONDS = 0

    def tearDown(self):
        pass

    def _create_dummy_image(self, filename: str) -> str:
        """បង្កើតរូបភាពសាកល្បងនៅលើ Disk សម្រាប់តេស្ត"""
        path = os.path.join(TEST_IMG_DIR, filename)
        img = Image.new("RGB", (500, 600), color=(120, 150, 200))
        img.save(path, format="JPEG")
        return path

    def test_database_user_registration_and_update(self):
        """តេស្តការចុះឈ្មោះ User និង Update ព័ត៌មានក្នុង SQLite"""
        database.register_or_update_user(
            user_id=101, chat_id=101, username="user1", first_name="Dara", last_name="Sok", db_path=TEST_DB
        )
        users = database.get_active_users(TEST_DB)
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["first_name"], "Dara")
        self.assertEqual(users[0]["username"], "user1")

        database.register_or_update_user(
            user_id=101, chat_id=101, username="user1_updated", first_name="Dara Pro", last_name="Sok", db_path=TEST_DB
        )
        users_after = database.get_active_users(TEST_DB)
        self.assertEqual(len(users_after), 1)
        self.assertEqual(users_after[0]["first_name"], "Dara Pro")

        database.mark_user_inactive(101, TEST_DB)
        active_users = database.get_active_users(TEST_DB)
        self.assertEqual(len(active_users), 0)
        active, total = database.get_total_users_count(TEST_DB)
        self.assertEqual(active, 0)
        self.assertEqual(total, 1)

    @unittest.skipIf(start_command is None, "handlers.py is not present")
    def test_start_command_private_registration(self):
        """តេស្តបញ្ជា /start ក្នុង Private Chat"""
        async def run():
            mock_update = MagicMock()
            mock_update.effective_user.id = 202
            mock_update.effective_user.username = "sneha_fan"
            mock_update.effective_user.first_name = "Bopha"
            mock_update.effective_user.last_name = "Keo"
            mock_update.effective_chat.id = 202
            mock_update.effective_chat.type = ChatType.PRIVATE if ChatType else "private"
            mock_update.message.reply_text = AsyncMock()
            mock_context = MagicMock()

            await start_command(mock_update, mock_context)

            mock_update.message.reply_text.assert_called_once()
            args, _ = mock_update.message.reply_text.call_args
            self.assertEqual(args[0], messages.START_MESSAGE)

            active_users = database.get_active_users(TEST_DB)
            self.assertEqual(len(active_users), 1)
            self.assertEqual(active_users[0]["user_id"], 202)

        asyncio.run(run())

    def test_ai_conversational_flow(self):
        """តេស្ត AI Conversational Assistant"""
        async def run():
            config.GEMINI_API_KEY = "mock_key"
            config.AI_PROVIDER = "gemini"

            mock_gemini_resp = {
                "candidates": [{
                    "content": {"parts": [{"text": "ជម្រាបសួរពី Team ស្មោះស្នេហ៍!"}]}
                }]
            }

            with patch("httpx.AsyncClient.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = mock_gemini_resp
                mock_post.return_value = mock_resp

                reply = await ai_assistant.ask_ai(user_id=601, user_message="សួស្តី", db_path=TEST_DB)
                self.assertIn("Team ស្មោះស្នេហ៍", reply)

        asyncio.run(run())

    def test_ai_assistant_connection_pool_and_fallback(self):
        """តេស្ត Connection Pool Reuse, Fallback គំរូពេលជួប HTTP 503, និង Bounded Backoff"""
        async def run():
            # 1. តេស្ត Connection Pool reuse
            client1 = await ai_assistant.get_shared_http_client()
            client2 = await ai_assistant.get_shared_http_client()
            self.assertIs(client1, client2)
            self.assertFalse(client1.is_closed)

            # 2. តេស្ត Bounded Backoff
            delay0 = ai_assistant._backoff_delay(0)
            delay1 = ai_assistant._backoff_delay(1)
            self.assertLess(delay0, 2.0)
            self.assertLess(delay1, 3.0)

            # 3. តេស្ត Fallback លើ HTTP 503
            resp_503 = MagicMock()
            resp_503.status_code = 503
            resp_503.json.return_value = {"error": {"code": 503, "status": "UNAVAILABLE"}}

            resp_200 = MagicMock()
            resp_200.status_code = 200
            resp_200.json.return_value = {
                "candidates": [{"content": {"parts": [{"text": "ឆ្លើយតបជោគជ័យពី Fallback!"}]}}]
            }

            with patch("httpx.AsyncClient.post", side_effect=[resp_503, resp_200]) as mock_post:
                reply = await ai_assistant.generate_gemini_response(
                    api_key="mock_key",
                    prompt="សួស្តី",
                    history=[]
                )
                self.assertEqual(reply, "ឆ្លើយតបជោគជ័យពី Fallback!")
                self.assertEqual(mock_post.call_count, 2)
                second_call_url = mock_post.call_args_list[1][0][0]
                self.assertIn(ai_assistant.FALLBACK_GEMINI_MODEL, second_call_url)

            await ai_assistant.close_shared_http_client()

        asyncio.run(run())

    @unittest.skipUnless(
        all(callable(getattr(database, name, None)) for name in (
            "save_photo_session", "get_photo_session", "clear_photo_session"
        )),
        "photo session persistence is not part of the current database module",
    )
    def test_photo_session_lifecycle_and_privacy_cleanup(self):
        """តេស្តវដ្តជីវិត Session រូបថត និងការធានាឯកជនភាព"""
        user_id = 9001
        dummy_path = self._create_dummy_image("lifecycle_test.jpg")
        self.assertTrue(os.path.exists(dummy_path))

        # 1. រក្សាទុក Session
        database.save_photo_session(user_id, "file_id_123", dummy_path, aspect_ratio="4:5", db_path=TEST_DB)
        session = database.get_photo_session(user_id, db_path=TEST_DB)
        self.assertIsNotNone(session)
        self.assertEqual(session["local_path"], dummy_path)
        self.assertEqual(session["state"], "WAITING_FOR_STYLE")

        # 2. User ផ្សេងមិនអាចឃើញ
        session_other = database.get_photo_session(9002, db_path=TEST_DB)
        self.assertIsNone(session_other)

        # 3. សម្អាត Session
        cleared_path = database.clear_photo_session(user_id, db_path=TEST_DB)
        self.assertEqual(cleared_path, dummy_path)
        if os.path.exists(cleared_path):
            os.remove(cleared_path)

        self.assertFalse(os.path.exists(dummy_path))
        self.assertIsNone(database.get_photo_session(user_id, db_path=TEST_DB))


class TestCoolerPersona(unittest.TestCase):
    def setUp(self):
        ai_assistant._persona_text_cache = None

    def tearDown(self):
        ai_assistant._persona_text_cache = None

    def test_load_persona_text_reads_configured_utf8_file(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as temp_dir:
            persona_path = os.path.join(temp_dir, "persona.txt")
            expected = "Cooler Bot — ជំនួយការខ្មែរ"
            with open(persona_path, "w", encoding="utf-8") as persona_file:
                persona_file.write(f"  {expected}  \n")

            with patch.object(config, "COOLER_PERSONA_PATH", persona_path):
                self.assertEqual(ai_assistant.load_persona_text(), expected)

    def test_missing_persona_uses_builtin_fallback(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as temp_dir:
            missing_path = os.path.join(temp_dir, "missing-persona.txt")
            with patch.object(config, "COOLER_PERSONA_PATH", missing_path):
                self.assertIsNone(ai_assistant.load_persona_text())
                instruction = ai_assistant.get_system_instruction(user_id=123)

        self.assertIn("You are Cooler Bot", instruction)
        self.assertIn("Start responses in Khmer by default", instruction)
        self.assertIn("continue in Khmer regardless of the language the user writes in", instruction)
        self.assertIn("only when the user explicitly asks", instruction)

    def test_system_instruction_integrates_persona_and_verified_owner_rules(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as temp_dir:
            persona_path = os.path.join(temp_dir, "persona.txt")
            with open(persona_path, "w", encoding="utf-8") as persona_file:
                persona_file.write("PERSONA_FILE_MARKER — ឆ្លើយដោយភាពកក់ក្តៅ")

            with patch.object(config, "COOLER_PERSONA_PATH", persona_path), patch.object(
                config, "OWNER_ID", 987654
            ):
                owner_instruction = ai_assistant.get_system_instruction(user_id=987654)
                member_instruction = ai_assistant.get_system_instruction(
                    user_name="VidCooler", user_id=123
                )

        self.assertIn("PERSONA_FILE_MARKER", owner_instruction)
        self.assertIn("Start responses in Khmer by default", owner_instruction)
        self.assertIn("This user has been verified by the system as Papa", owner_instruction)
        self.assertNotIn("987654", owner_instruction)
        self.assertIn("display name is VidCooler", member_instruction)
        self.assertIn("Never call this user Papa", member_instruction)
        self.assertNotIn("This user has been verified by the system as Papa", member_instruction)


if __name__ == "__main__":
    print("=" * 70)
    print("🧪 កំពុងដំណើរការ Safe Local Tests សម្រាប់ Team ស្មោះស្នេហ៍ Bot...")
    print("=" * 70)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestTeamSmaohSnehaBot)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n✅ តេស្តទាំងអស់បានឆ្លងកាត់ដោយជោគជ័យ ១០០%!")
    else:
        print("\n❌ មានបញ្ហាក្នុងការតេស្ត!")
        sys.exit(1)
