"""Tests for Game Center's interactive Rock–Paper–Scissors round."""
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest import mock

import game_center


class FakeBot:
    def __init__(self):
        self.callback_handler = None
        self.edited_messages = []
        self.edited_captions = []
        self.sent_messages = []
        self.sent_photos = []
        self.answered_callbacks = []

    def message_handler(self, **kwargs):
        return lambda fn: fn

    def callback_query_handler(self, **kwargs):
        def register(fn):
            self.callback_handler = fn
            return fn
        return register

    def answer_callback_query(self, *args, **kwargs):
        self.answered_callbacks.append((args, kwargs))

    def edit_message_text(self, *args, **kwargs):
        self.edited_messages.append((args, kwargs))

    def edit_message_caption(self, *args, **kwargs):
        self.edited_captions.append((args, kwargs))

    def send_message(self, *args, **kwargs):
        self.sent_messages.append((args, kwargs))

    def send_photo(self, *args, **kwargs):
        photo_bytes = args[1].read()
        self.sent_photos.append((args[0], photo_bytes, kwargs))
        self.last_photo_message = SimpleNamespace(
            chat=SimpleNamespace(id=args[0], type="private"),
            message_id=99,
            caption=kwargs.get("caption"),
            content_type="photo",
        )
        return self.last_photo_message


class GameCenterRpsTest(unittest.TestCase):
    def test_every_arcade_game_name_starts_with_cooler(self):
        self.assertTrue(all(title.startswith("Cooler ") for _emoji, title, _score in game_center.GAMES.values()))

    def test_opponent_variants_use_matching_colors(self):
        self.assertEqual(set(game_center.RPS_OPPONENTS), {"red", "green"})
        for color, (name, path) in game_center.RPS_OPPONENTS.items():
            with self.subTest(color=color):
                self.assertEqual(name, f"Cooler {color.title()}")
                self.assertTrue(os.path.isfile(path))

    def test_all_rps_outcomes(self):
        for player in game_center.RPS_CHOICES:
            for cooler in game_center.RPS_CHOICES:
                with self.subTest(player=player, cooler=cooler):
                    points, _verdict, won = game_center.rps_duel(player, cooler)
                    if player == cooler:
                        self.assertEqual(points, 2)
                        self.assertFalse(won)
                    elif (player, cooler) in game_center._RPS_WINS:
                        self.assertEqual(points, 8)
                        self.assertTrue(won)
                    else:
                        self.assertEqual(points, 0)
                        self.assertFalse(won)

    def test_invalid_choice_is_rejected(self):
        with self.assertRaises(ValueError):
            game_center.rps_duel("water", "rock")

    def test_rps_callback_awards_points_and_community_bonus(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            db_path = os.path.join(directory, "game.db")
            with closing(sqlite3.connect(db_path)) as conn:
                with conn:
                    conn.execute("CREATE TABLE community_points (user_id INTEGER PRIMARY KEY, points INTEGER DEFAULT 0)")
            config = SimpleNamespace(BOT_DB_PATH=db_path)
            bot = FakeBot()
            api = game_center.register(bot, config)

            menu_callbacks = [
                button.callback_data
                for row in api.menu().keyboard
                for button in row
                if button.callback_data
            ]
            self.assertIn("gc:rps", menu_callbacks)

            message = SimpleNamespace(chat=SimpleNamespace(id=42, type="private"), message_id=7)
            user = SimpleNamespace(id=123, first_name="Test <Player>")
            menu_call = SimpleNamespace(id="menu", data="gc:rps", message=message, from_user=user)
            with mock.patch("game_center.random.choice", return_value="red"):
                bot.callback_handler(menu_call)
            self.assertTrue(bot.sent_photos)
            self.assertTrue(bot.sent_photos[-1][1].startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertIn("Cooler Red", bot.sent_photos[-1][2]["caption"])
            with open(game_center.RPS_OPPONENTS["red"][1], "rb") as red_image:
                self.assertEqual(bot.sent_photos[-1][1], red_image.read())
            rps_markup = bot.sent_photos[-1][2]["reply_markup"]
            choice_callbacks = [button.callback_data for row in rps_markup.keyboard for button in row]
            self.assertEqual(choice_callbacks[:3], ["gc:rps:red:rock", "gc:rps:red:paper", "gc:rps:red:scissors"])

            play_call = SimpleNamespace(id="play", data="gc:rps:red:rock", message=bot.last_photo_message, from_user=user)
            with mock.patch("game_center.random.choice", return_value="scissors"), \
                    mock.patch("game_center.time.monotonic", return_value=100):
                bot.callback_handler(play_call)

            sent_text = bot.edited_captions[-1][0][0]
            self.assertIn("Test &lt;Player&gt;", sent_text)
            self.assertIn("Cooler Red", sent_text)
            self.assertIn("+8", sent_text)
            self.assertIn("+4 ពិន្ទុសហគមន៍", sent_text)
            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(
                    conn.execute("SELECT points, plays, wins FROM game_scores WHERE user_id=123").fetchone(),
                    (8, 1, 1),
                )
                self.assertEqual(
                    conn.execute("SELECT points FROM community_points WHERE user_id=123").fetchone(),
                    (4,),
                )

            menu_again = SimpleNamespace(id="again", data="gc:menu", message=bot.last_photo_message, from_user=user)
            bot.callback_handler(menu_again)
            menu_caption, _chat_id, _message_id = bot.edited_captions[-1][0]
            self.assertIn("Cooler Game Center", menu_caption)
            self.assertIn("gc:rps", [
                button.callback_data
                for row in bot.edited_captions[-1][1]["reply_markup"].keyboard
                for button in row
            ])


if __name__ == "__main__":
    unittest.main()
