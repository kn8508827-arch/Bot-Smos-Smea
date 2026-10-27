"""Tests for ✨ Cooler AI Studio (studio_web.py) — the Hub's 8-tool web app backend."""
import base64
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import ai_extras
import music_studio
import studio_web
from test_icom_live import BOT_TOKEN, make_init

PNG = b"\x89PNG" + b"0" * 2000
PHOTO = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8" + b"1" * 3000).decode()


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.c = SimpleNamespace(BOT_TOKEN=BOT_TOKEN, OWNER_ID=1, ADMIN_IDS=[], GEMINI_API_KEY="k",
                                 BOT_DB_PATH=os.path.join(self.tmp, "t.db"))
        ai_extras.init_schema(self.c)
        music_studio.init_schema(self.c)
        self.photo_left = {50: 2}
        self.recorded = []
        self.pbusy = set()
        self.bot = mock.Mock()
        studio_web.register(self.bot, self.c, {
            "photo_remaining": lambda uid: self.photo_left.get(uid, 3),
            "photo_record": lambda uid: self.recorded.append(uid),
            "photo_busy": self.pbusy,
            "photo_run": lambda data, prompt: b"\xff\xd8JPEGOUT" + prompt.encode()[:20],
            "prompt_ask": lambda system, prompt: "🎬 Prompt មេ\n<<<\nA cinematic shot\n>>>",
            "ask": lambda prompt: "Yo Cooler rap verse",
        })
        studio_web._jobs.clear()
        studio_web._busy.clear()
        self.allowed = mock.patch("icom_live.is_allowed", return_value=True)
        self.allowed.start()
        self.addCleanup(self.allowed.stop)

    def post(self, path, data, uid=50):
        body = dict(data, initData=make_init({"id": uid, "first_name": "Fan"}))
        code, raw, ctype, _ = studio_web.handle_post(path, self.c, json.dumps(body).encode(), start_thread=False)
        return code, json.loads(raw)

    def run_tool(self, uid=50, **data):
        code, out = self.post("/studio/run", data, uid)
        if code != 200 or out.get("done"):
            return code, out
        code, job = self.post("/studio/job", {"job": out["job"]}, uid)
        job["id"] = out["job"]
        return code, job


class StudioWebTest(Env):
    def test_me_has_catalog_and_limits(self):
        code, out = self.post("/studio/me", {})
        self.assertEqual(code, 200)
        self.assertTrue(out["member"])
        cat = out["catalog"]
        self.assertGreaterEqual(len(cat["photoStyles"]), 9)
        self.assertEqual(len(cat["animals"]), 12)
        self.assertTrue(any(i[2] == "hair" for i in cat["items"]))
        self.assertEqual(set(out["left"]), {"image", "photo", "music", "prompt", "rap", "fortune"})

    def test_non_member(self):
        with mock.patch("icom_live.is_allowed", return_value=False):
            code, out = self.post("/studio/run", {"tool": "rap", "words": "hi"}, uid=77)
        self.assertEqual(code, 403)

    def test_imagine_shares_image_quota_and_serves_file(self):
        with mock.patch.object(ai_extras, "generate_image", return_value=PNG) as gen, \
                mock.patch.dict(os.environ, {"IMAGINE_DAILY_LIMIT": "1"}):
            code, job = self.run_tool(tool="imagine", prompt="ឆ្មា", style="pixar", aspect="9:16")
            self.assertEqual(job["status"], "done")
            self.assertIn("Pixar", gen.call_args[0][1])
            self.assertEqual(gen.call_args[0][2], "9:16")
            fid = job["result"]["files"][0]
            self.assertEqual(studio_web.handle_get(f"/studio/file/{fid}")[1], PNG)
            code, out = self.run_tool(tool="imagine", prompt="ម្ដងទៀត")
            self.assertEqual(code, 400)
            self.assertIn("អស់", out["msg"])
        self.assertEqual(ai_extras.images_used(self.c, 50), 1)

    def test_failure_gives_quota_back(self):
        with mock.patch.object(ai_extras, "generate_image", side_effect=RuntimeError("HTTP 500")):
            code, job = self.run_tool(tool="imagine", prompt="ឆ្មា")
        self.assertEqual(job["status"], "error")
        self.assertIn("មិនកាត់", job["msg"])
        self.assertEqual(ai_extras.images_used(self.c, 50), 0)
        self.assertEqual(studio_web._busy, {})

    def test_photo_and_style_use_studio_hooks(self):
        code, job = self.run_tool(tool="photo", photo=PHOTO, styleKey="kdrama")
        self.assertEqual(job["status"], "done")
        self.assertEqual(self.recorded, [50])
        code, job = self.run_tool(tool="style", photo=PHOTO, item="m_fade")
        self.assertEqual(job["status"], "done")
        self.assertEqual(self.pbusy, set())
        self.photo_left[50] = 0
        code, out = self.run_tool(tool="photo", photo=PHOTO, styleKey="kdrama")
        self.assertEqual(code, 400)
        code, out = self.run_tool(tool="photo", styleKey="kdrama")
        self.assertEqual(code, 400)

    def test_song_and_beat_share_music_limit(self):
        with mock.patch.object(music_studio, "write_lyrics", return_value="[Verse] ..."), \
                mock.patch.object(music_studio, "generate_music", return_value=(b"ID3mp3", "audio/mpeg", "lyria")) as gm, \
                mock.patch.dict(os.environ, {"SONG_DAILY_LIMIT": "2"}):
            code, job = self.run_tool(tool="song", topic="ម៉ាក់", styleKey="rap")
            self.assertEqual((job["status"], job["result"]["text"]), ("done", "[Verse] ..."))
            code, job = self.run_tool(tool="beat", preset="trap", bpm=150)
            self.assertIn("150 BPM", gm.call_args[0][1])
            self.assertNotIn("140 BPM", gm.call_args[0][1])
            code, out = self.run_tool(tool="beat", preset="lofi")
            self.assertEqual(code, 400)

    def test_prompt_rap_fortune(self):
        code, job = self.run_tool(tool="prompt", mode="video", idea="ឡានហោះ")
        self.assertEqual(job["result"]["kind"], "prompt")
        code, job = self.run_tool(tool="prompt", mode="story", cont=True)
        self.assertEqual(job["status"], "done")
        code, job = self.run_tool(tool="rap", words="មិត្តភាព")
        self.assertEqual(job["result"]["text"], "Yo Cooler rap verse")
        code, out = self.run_tool(tool="fortune", what="daily", year="1995")
        self.assertTrue(out["done"])
        self.assertIn("ឆ្នាំកុរ", out["result"]["text"])
        code, out = self.run_tool(tool="fortune", what="number", date="15/08/1995")
        self.assertIn("លេខជីវិត", out["result"]["text"])
        code, out = self.run_tool(tool="fortune", what="stick")
        self.assertIn("ឆ្នោតលេខ", out["result"]["text"])
        code, job = self.run_tool(tool="fortune", what="dream", q="សុបិនឃើញពស់")
        self.assertIn("ទាយសុបិន", job["result"]["text"])

    def test_send_and_privacy(self):
        code, job = self.run_tool(tool="rap", words="hi")
        code, out = self.post("/studio/send", {"job": job["id"]})
        self.assertEqual(code, 200)
        self.assertEqual(self.bot.send_message.call_args[0][0], 50)
        code, out = self.post("/studio/job", {"job": job["id"]}, uid=51)
        self.assertEqual(code, 404)
        with mock.patch.object(ai_extras, "generate_image", return_value=PNG):
            code, job = self.run_tool(tool="imagine", prompt="x")
        self.post("/studio/send", {"job": job["id"]})
        self.assertTrue(self.bot.send_photo.called)

    def test_owner_unlimited(self):
        code, out = self.post("/studio/me", {}, uid=1)
        self.assertEqual(out["left"]["image"], None)


if __name__ == "__main__":
    unittest.main()
