"""Staff attendance: location radius, selfie step, late / early marks, owner notice."""
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import telebot

import attendance as att

OWNER, STAFF = 1, 50
HOME = (11.5564, 104.9282)


def msg(uid, text=None, ctype="text", **extra):
    m = SimpleNamespace(from_user=SimpleNamespace(id=uid, first_name="Dara", last_name=None, username=None),
                        chat=SimpleNamespace(id=uid, type="private"), text=text, content_type=ctype,
                        message_id=1, forward_origin=None, forward_date=None)
    m.__dict__.update(extra)
    return m


class AttendanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.config = SimpleNamespace(BOT_DB_PATH=self.tmp.name, OWNER_ID=OWNER, ADMIN_IDS=[])
        self.bot = telebot.TeleBot("1:T", threaded=False)
        for name in ("send_message", "send_photo", "reply_to", "answer_callback_query", "edit_message_reply_markup"):
            setattr(self.bot, name, MagicMock())
        self.api = att.register(self.bot, self.config)
        att.set_place(self.config, *HOME)
        with att._db(self.config) as c:
            c.execute("INSERT INTO att_staff(user_id, name, status) VALUES (?, 'Dara', 'active')", (STAFF,))

    def tearDown(self):
        os.unlink(self.tmp.name)

    def run_msg(self, m):
        self.bot.process_new_messages([m])

    def checkin(self, lat, lon, when):
        with patch.object(att, "now_kh", return_value=when):
            self.run_msg(msg(STAFF, "/workin", entities=None))
            self.run_msg(msg(STAFF, ctype="location", location=SimpleNamespace(latitude=lat, longitude=lon)))
            self.run_msg(msg(STAFF, ctype="photo", photo=[SimpleNamespace(file_id="SELFIE")]))

    def test_distance(self):
        self.assertLess(att.distance_m(*HOME, 11.5565, 104.9283), 20)

    def test_far_away_is_refused(self):
        self.checkin(11.60, 104.95, datetime(2026, 10, 26, 8, 0, tzinfo=att.TZ_KH))
        self.assertEqual(att.today_logs(self.config, "2026-10-26"), [])

    def test_on_time_checkin_notifies_owner_with_selfie(self):
        self.checkin(11.5565, 104.9283, datetime(2026, 10, 26, 7, 58, tzinfo=att.TZ_KH))
        logs = att.today_logs(self.config, "2026-10-26")
        self.assertEqual((logs[0]["kind"], logs[0]["late_min"], logs[0]["photo"]), ("in", 0, "SELFIE"))
        self.assertEqual(self.bot.send_photo.call_args.args[0], OWNER)

    def test_late_and_early(self):
        t = datetime(2026, 10, 26, 8, 40, tzinfo=att.TZ_KH)
        self.assertEqual(att.minutes_late(t, (8, 0), 5), 40)
        self.assertEqual(att.minutes_late(t.replace(minute=4), (8, 0), 5), 0)
        self.assertEqual(att.minutes_early(t.replace(hour=17, minute=30), (18, 0)), 30)

    def test_unknown_person_cannot_check_in(self):
        with patch.object(att, "now_kh", return_value=datetime(2026, 10, 26, 8, 0, tzinfo=att.TZ_KH)):
            self.run_msg(msg(77, "/workin", entities=None))
        self.assertFalse(self.api["busy"](77))


if __name__ == "__main__":
    unittest.main()
