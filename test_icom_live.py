"""Tests for ICOM in the Cooler Hub: Telegram initData check, LiveKit token, member gate."""
import base64
import hashlib
import hmac
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode

import icom_live

BOT_TOKEN = "123456:TEST-token"


def make_init(user, token=BOT_TOKEN, auth_date=None, extra=None):
    fields = {"auth_date": str(int(auth_date or time.time())), "query_id": "AAE1",
              "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False)}
    fields.update(extra or {})
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def jwt_parts(tok):
    def dec(x):
        return json.loads(base64.urlsafe_b64decode(x + "=" * (-len(x) % 4)))
    h, b, s = tok.split(".")
    return dec(h), dec(b), s


ENV = {"LIVEKIT_URL": "wss://demo.livekit.cloud", "LIVEKIT_API_KEY": "APIkey", "LIVEKIT_API_SECRET": "sekret"}


class InitDataTest(unittest.TestCase):
    def test_valid(self):
        u = icom_live.verify_init_data(make_init({"id": 42, "first_name": "សុខា"}), BOT_TOKEN)
        self.assertEqual(u["id"], 42)

    def test_signature_field_is_kept_in_check(self):
        init = make_init({"id": 7}, extra={"signature": "abc"})
        self.assertEqual(icom_live.verify_init_data(init, BOT_TOKEN)["id"], 7)

    def test_wrong_token(self):
        self.assertIsNone(icom_live.verify_init_data(make_init({"id": 42}), "999:other"))

    def test_tampered(self):
        init = make_init({"id": 42}).replace("42", "43")
        self.assertIsNone(icom_live.verify_init_data(init, BOT_TOKEN))

    def test_too_old(self):
        init = make_init({"id": 42}, auth_date=time.time() - 3 * 86400)
        self.assertIsNone(icom_live.verify_init_data(init, BOT_TOKEN))

    def test_empty(self):
        self.assertIsNone(icom_live.verify_init_data("", BOT_TOKEN))
        self.assertIsNone(icom_live.verify_init_data("garbage", BOT_TOKEN))


class TokenTest(unittest.TestCase):
    def test_jwt_shape_and_signature(self):
        tok = icom_live.livekit_token("APIkey", "sekret", "tg42", "សុខា", "room1", now=1000)
        h, b, s = jwt_parts(tok)
        self.assertEqual(h["alg"], "HS256")
        self.assertEqual((b["iss"], b["sub"], b["exp"]), ("APIkey", "tg42", 1000 + icom_live.TOKEN_TTL))
        self.assertTrue(b["video"]["roomJoin"] and b["video"]["canPublish"])
        msg = tok.rsplit(".", 1)[0]
        want = base64.urlsafe_b64encode(hmac.new(b"sekret", msg.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        self.assertEqual(s, want)


class ResponseTest(unittest.TestCase):
    def cfg(self, owner=0):
        return SimpleNamespace(BOT_TOKEN=BOT_TOKEN, OWNER_ID=owner, ADMIN_IDS=[])

    def body(self, uid):
        return json.dumps({"initData": make_init({"id": uid, "first_name": "Dara"})}).encode()

    def test_not_configured(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(icom_live.token_response(self.cfg(), self.body(1))[0], 503)

    def test_owner_gets_token(self):
        with mock.patch.dict(os.environ, ENV):
            code, data = icom_live.token_response(self.cfg(owner=5), self.body(5))
        self.assertEqual(code, 200)
        self.assertEqual(data["room"], "team-smos-snea")
        self.assertEqual(jwt_parts(data["token"])[1]["video"]["room"], "team-smos-snea")

    def test_unpaid_blocked(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(icom_live, "is_allowed", return_value=False):
            self.assertEqual(icom_live.token_response(self.cfg(), self.body(9))[0], 402)

    def test_paid_member(self):
        with mock.patch.dict(os.environ, ENV), mock.patch("subscription.is_active", return_value=True):
            self.assertEqual(icom_live.token_response(self.cfg(), self.body(9))[0], 200)

    def test_forged(self):
        with mock.patch.dict(os.environ, ENV):
            bad = json.dumps({"initData": make_init({"id": 5}, token="1:x")}).encode()
            self.assertEqual(icom_live.token_response(self.cfg(owner=5), bad)[0], 401)

    def test_page_exists(self):
        self.assertIn(b"livekit-client", icom_live.page_html())


class MembersAndPrivateTest(unittest.TestCase):
    def setUp(self):
        import tempfile, sqlite3
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        self.cfg = SimpleNamespace(BOT_TOKEN=BOT_TOKEN, OWNER_ID=5, ADMIN_IDS=[], BOT_DB_PATH=self.tmp)
        now = time.time()
        with sqlite3.connect(self.tmp) as c:
            c.execute("CREATE TABLE subscriptions (kind TEXT, target_id INTEGER, paid_until INTEGER, reminded_for INTEGER)")
            c.execute("CREATE TABLE users (user_id INTEGER, first_name TEXT, last_name TEXT)")
            c.executemany("INSERT INTO subscriptions VALUES ('u',?,?,NULL)", [(10, now + 86400), (11, now - 10)])
            c.executemany("INSERT INTO users VALUES (?,?,?)", [(10, "Sokha", ""), (11, "Unpaid", "Guy"), (5, "Vid", "Cooler")])
        icom_live._rings.clear()

    def tearDown(self):
        os.unlink(self.tmp)

    def body(self, uid, **extra):
        d = {"initData": make_init({"id": uid, "first_name": "U%d" % uid, "photo_url": "https://t.me/p.jpg"})}
        d.update(extra)
        return json.dumps(d).encode()

    def test_list_hides_unpaid(self):
        with mock.patch("subscription.is_active", side_effect=lambda c, k, i, n=None: i == 10):
            code, data = icom_live.members_response(self.cfg, self.body(10))
        self.assertEqual(code, 200)
        ids = {m["id"] for m in data["members"]}
        self.assertEqual(ids, {5, 10})
        self.assertNotIn(11, ids)

    def test_unpaid_cannot_see_list(self):
        with mock.patch("subscription.is_active", return_value=False):
            self.assertEqual(icom_live.members_response(self.cfg, self.body(11))[0], 402)

    def test_private_room_both_members(self):
        with mock.patch.dict(os.environ, ENV), \
                mock.patch("subscription.is_active", side_effect=lambda c, k, i, n=None: i == 10):
            code, data = icom_live.token_response(self.cfg, self.body(10, peer=5))
            self.assertEqual((code, data["room"], data["private"]), (200, "dm-5-10", True))
            self.assertEqual(jwt_parts(data["token"])[1]["video"]["room"], "dm-5-10")
            # an unpaid peer can't be called
            self.assertEqual(icom_live.token_response(self.cfg, self.body(10, peer=11))[0], 404)
            self.assertEqual(icom_live.token_response(self.cfg, self.body(10, peer=10))[0], 400)

    def test_ring_sends_once(self):
        bot = mock.Mock()
        with mock.patch.dict(os.environ, {"ICOM_URL": "https://x.test/icom"}), \
                mock.patch("subscription.is_active", side_effect=lambda c, k, i, n=None: i == 10):
            self.assertEqual(icom_live.ring_response(self.cfg, self.body(10, peer=5), bot)[0], 200)
            self.assertEqual(icom_live.ring_response(self.cfg, self.body(10, peer=5), bot)[0], 429)
        self.assertEqual(bot.send_message.call_count, 1)
        self.assertEqual(bot.send_message.call_args[0][0], 5)

    def test_router(self):
        self.assertIsNone(icom_live.handle_post("/icom/other", self.cfg, b"{}"))
        self.assertEqual(icom_live.handle_post("/icom/members", self.cfg, b"{}")[0], 401)


if __name__ == "__main__":
    unittest.main()
