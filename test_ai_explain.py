"""The AI knows the bot's features, the last command a user ran, and is told to explain fully."""
import time
import unittest

import ai_assistant


class ExplainTests(unittest.TestCase):
    def test_feature_guide_is_in_prompt(self):
        prompt = ai_assistant.get_system_instruction("Dara", user_id=777)
        self.assertIn("/cash ចាយ 5000 រៀល", prompt)
        self.assertIn("/report", prompt)
        self.assertIn("step", prompt)

    def test_recent_command_context(self):
        ai_assistant.remember_command(888, "/report")
        prompt = ai_assistant.get_system_instruction("Vid", user_id=888)
        self.assertIn("«/report»", prompt)
        ai_assistant._recent_commands[888] = ("/report", time.time() - 3600)
        self.assertNotIn("«/report»", ai_assistant.get_system_instruction("Vid", user_id=888))

    def test_plain_text_and_start_not_remembered(self):
        ai_assistant.remember_command(999, "សួស្ដី")
        ai_assistant.remember_command(999, "/start")
        self.assertNotIn(999, ai_assistant._recent_commands)


if __name__ == "__main__":
    unittest.main()
