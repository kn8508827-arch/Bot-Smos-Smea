"""Tests for 🎙️ Cooler Voice Studio (voice_web.py) and the new TTS payloads in voice_studio.py."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import voice_studio
import voice_web
from test_icom_live import BOT_TOKEN, make_init

WAV = voice_studio.pcm_to_wav(b"\x00\x00" * 48000)


def cfg(tmp, owner=1):
    return SimpleNamespace(BOT_TOKEN=BOT_TOKEN, OWNER_ID=owner, ADMIN_IDS=[], GEMINI_API_KEY="k",
                           BOT_DB_PATH=os.path.join(tmp, "t.db"))


class Calls:
    def __init__(self):
        self.calls = []

    def __call__(self, turns, voices, style, models):
        self.calls.append((turns, voices, style, models))
        return WAV


def post(config, path, data, uid=1, synth=None, bot=None):
    body = dict(data)
    body["initData"] = make_init({"id": uid, "first_name": "Cooler"})
    return voice_web.handle_post(path, config, json.dumps(body).encode(), bot=bot, synth=synth)


class VoiceWebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.c = cfg(self.tmp)
        voice_studio.init_schema(self.c)
        voice_web._extra.update(t=10 ** 12, voices=[])      # no network
        voice_web._clips.clear()
        voice_web._busy.clear()

    def test_thirty_voices(self):
        self.assertEqual(len(voice_web.VOICES), 30)
        self.assertEqual(sum(1 for v in voice_web.VOICES.values() if v[0] == "f"), 14)

    def test_needs_telegram(self):
        code, body, _, _ = voice_web.handle_post("/voice/me", self.c, b'{"initData":"x"}')
        self.assertEqual(code, 401)

    def test_non_member_locked(self):
        with mock.patch("icom_live.is_allowed", return_value=False):
            code, body, _, _ = post(self.c, "/voice/me", {}, uid=77)
            self.assertEqual(code, 200)
            self.assertFalse(json.loads(body)["member"])
            code, _, _, _ = post(self.c, "/voice/tts", {"text": "សួស្ដី", "voices": ["Puck"]}, uid=77, synth=Calls())
            self.assertEqual(code, 403)

    def test_single_voice_tts_and_quota(self):
        s = Calls()
        with mock.patch("icom_live.is_allowed", return_value=True), mock.patch.dict(os.environ, {"TTS_DAILY_LIMIT": "2"}):
            code, body, ctype, hdr = post(self.c, "/voice/tts", {
                "turns": [{"text": "សួស្ដី <laugh> **បងប្អូន** 😀 <script>"}], "voices": ["Kore"],
                "style": "news anchor", "quality": "fast"}, uid=50, synth=s)
            self.assertEqual((code, ctype), (200, "audio/wav"))
            self.assertEqual(hdr["X-Left"], "1")
            turns, voices, style, models = s.calls[0]
            self.assertEqual(turns[0]["text"], "សួស្ដី <laugh> បងប្អូន")
            self.assertEqual((voices, style, models[0]), (["Kore"], "news anchor", "gemini-3.8-flash-lite-tts"))
            post(self.c, "/voice/tts", {"text": "ម្ដងទៀត", "voices": ["Kore"]}, uid=50, synth=s)
            code, body, _, _ = post(self.c, "/voice/tts", {"text": "ម្ដងទៀត", "voices": ["Kore"]}, uid=50, synth=s)
            self.assertEqual(code, 429)
            self.assertEqual(json.loads(body)["error"], "quota")

    def test_failed_tts_refunds(self):
        def boom(*a):
            raise RuntimeError("HTTP 500")
        with mock.patch("icom_live.is_allowed", return_value=True):
            code, _, _, _ = post(self.c, "/voice/tts", {"text": "សួស្ដី", "voices": ["Puck"]}, uid=51, synth=boom)
            self.assertEqual(code, 502)
            self.assertEqual(voice_studio.used_today(self.c, 51), 0)
            self.assertNotIn(51, voice_web._busy)

    def test_dialogue(self):
        s = Calls()
        code, _, _, _ = post(self.c, "/voice/tts", {"turns": [{"speaker": 0, "text": "សួស្ដីបង"}, {"speaker": 1, "text": "បាទ"}],
                                                     "voices": ["Puck", "Kore"]}, synth=s)
        self.assertEqual(code, 200)
        turns, voices, _, models = s.calls[0]
        self.assertEqual([t["speaker"] for t in turns], [0, 1])
        self.assertEqual(voices, ["Puck", "Kore"])
        self.assertEqual(models[0], "gemini-3.8-flash-tts")

    def test_dialogue_one_speaker_becomes_single(self):
        s = Calls()
        post(self.c, "/voice/tts", {"turns": [{"speaker": 1, "text": "មួយ"}, {"speaker": 1, "text": "ពីរ"}],
                                    "voices": ["Puck", "Kore"]}, synth=s)
        turns, voices, _, _ = s.calls[0]
        self.assertEqual((voices, turns[0]["text"]), (["Kore"], "មួយ\nពីរ"))

    def test_rejects_bad_voice_and_long_text(self):
        code, body, _, _ = post(self.c, "/voice/tts", {"text": "x", "voices": ["Nobody"]}, synth=Calls())
        self.assertEqual(code, 400)
        code, body, _, _ = post(self.c, "/voice/tts", {"text": "ក" * 1300, "voices": ["Puck"]}, synth=Calls())
        self.assertEqual(code, 400)
        self.assertIn("វែងពេក", json.loads(body)["msg"])

    def test_sample_cached(self):
        s = Calls()
        for _ in range(2):
            code, body, ctype, _ = post(self.c, "/voice/sample", {"voice": "Sulafat"}, synth=s)
            self.assertEqual((code, ctype, body[:4]), (200, "audio/wav", b"RIFF"))
        self.assertEqual(len(s.calls), 1)

    def test_send_and_download(self):
        bot = mock.Mock()
        code, _, _, hdr = post(self.c, "/voice/tts", {"text": "សួស្ដី", "voices": ["Puck"]}, synth=Calls())
        clip = hdr["X-Clip"]
        code, body, _, _ = post(self.c, "/voice/send", {"clip": clip}, bot=bot)
        self.assertEqual(code, 200)
        self.assertEqual(bot.send_audio.call_args[0][0], 1)
        # someone else can't send my clip to themselves
        with mock.patch("icom_live.is_allowed", return_value=True):
            code, _, _, _ = post(self.c, "/voice/send", {"clip": clip}, uid=9, bot=bot)
        self.assertEqual(code, 404)
        code, body, ctype, hdr = voice_web.handle_get(f"/voice/file/{clip}.wav")
        self.assertEqual((code, body[:4]), (200, b"RIFF"))
        self.assertIn("attachment", hdr["Content-Disposition"])
        self.assertEqual(voice_web.handle_get("/voice/file/nope-nope-nope")[0], 404)

    def test_pref_used_by_say(self):
        code, _, _, _ = post(self.c, "/voice/pref", {"voice": "Sulafat", "style": "warm storyteller"})
        self.assertEqual(code, 200)
        self.assertEqual(voice_studio.get_pref(self.c, 1), ("Sulafat", "warm storyteller"))
        code, body, _, _ = post(self.c, "/voice/me", {})
        self.assertEqual(json.loads(body)["pref"]["voice"], "Sulafat")
        post(self.c, "/voice/pref", {"voice": ""})
        self.assertEqual(voice_studio.get_pref(self.c, 1), (None, None))

    def test_studio_url(self):
        with mock.patch.dict(os.environ, {"HUB_URL": "https://x.github.io/B/hub/index.html?a=1"}):
            self.assertEqual(voice_web.studio_url(), "https://x.github.io/B/hub/voice.html")


class PayloadTest(unittest.TestCase):
    def test_dialogue_payload(self):
        reqs = voice_studio._requests("gemini-3.8-flash-tts", [{"text": "a", "speaker": 0}, {"text": "b", "speaker": 1}],
                                      ["Puck", "Kore"], "calm")
        url, p = reqs[0]
        self.assertTrue(url.endswith("/interactions"))
        sc = p["generation_config"]["speech_config"]
        self.assertEqual(sc["mode"], "conversational")
        self.assertEqual(sc["speakers"], [{"speaker": "Joe", "voice": "Puck"}, {"speaker": "Jane", "voice": "Kore"}])
        self.assertEqual([c["annotations"][0]["speaker"] for c in p["input"][0]["content"]], ["Joe", "Jane"])
        url, p = reqs[2]
        self.assertIn("multiSpeakerVoiceConfig", p["generationConfig"]["speechConfig"])

    def test_dialogue_falls_back_to_stitching(self):
        import httpx
        sent = []

        def handler(req):
            body = json.loads(req.content)
            sent.append(body)
            sc = (body.get("generation_config") or {}).get("speech_config")
            if isinstance(sc, dict) or "contents" in body and "multiSpeakerVoiceConfig" in json.dumps(body):
                return httpx.Response(400, json={"error": {"message": "multi-speaker not supported"}})
            voice = sc[0]["voice"] if sc else "x"
            pcm = (b"\x01\x00" if voice == "Puck" else b"\x02\x00") * 2400
            return httpx.Response(200, json={"steps": [{"content": [{"type": "audio", "data": __import__("base64").b64encode(
                voice_studio.pcm_to_wav(pcm)).decode(), "mime_type": "audio/wav"}]}]})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        wav = voice_studio.synthesize_turns("k", [{"text": "a", "speaker": 0}, {"text": "b", "speaker": 1}],
                                            ["Puck", "Kore"], style="s", client=client, models=["m"])
        pcm, rate = voice_studio.wav_pcm(wav)
        self.assertEqual(rate, 24000)
        self.assertEqual(len(pcm), 2 * 4800 + 2 * int(24000 * 0.3))
        self.assertEqual(pcm[:2], b"\x01\x00")
        self.assertEqual(pcm[-2:], b"\x02\x00")

    def test_single_payload_unchanged(self):
        url, p = voice_studio._requests("m", [{"text": "hi"}], ["Puck"], "s")[0]
        self.assertEqual(p["generation_config"]["speech_config"], [{"voice": "Puck"}])

    def test_say_uses_pref_voice(self):
        tmp = tempfile.mkdtemp()
        c = cfg(tmp, owner=999)
        bot = mock.Mock()
        handlers = {}
        bot.message_handler.side_effect = lambda **kw: (lambda f: handlers.setdefault(f.__name__, f))
        bot.callback_query_handler.side_effect = lambda **kw: (lambda f: f)
        got = []
        api = voice_studio.register(bot, c, synth=lambda text, voice, style=None: got.append((voice, style)) or WAV)
        voice_studio.set_pref(c, 5, "Achernar", "soft")
        with mock.patch.object(voice_studio, "wav_to_ogg", return_value=b"ogg"):
            api.speak(1, 5, "សួស្ដី")
            api.speak(1, 6, "សួស្ដី")
        self.assertEqual(got, [("Achernar", "soft"), ("Puck", None)])


if __name__ == "__main__":
    unittest.main()
