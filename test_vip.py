"""Tests for 👑 VIP members (vip.py + subscription kind "v")."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import subscription
import vip


class Vip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.c = SimpleNamespace(BOT_DB_PATH=os.path.join(self.tmp, "t.db"), OWNER_ID=1, ADMIN_IDS=[])
        subscription.init_schema(self.c)
        vip.forget()

    def test_vip_is_also_member(self):
        until = subscription.extend(self.c, "v", 77, 30)
        self.assertTrue(subscription.is_active(self.c, "v", 77))
        self.assertTrue(subscription.is_active(self.c, "u", 77))
        self.assertEqual(subscription.paid_until(self.c, "u", 77), until)

    def test_vip_keeps_longer_member_period(self):
        u_until = subscription.extend(self.c, "u", 77, 90)
        subscription.extend(self.c, "v", 77, 30)
        self.assertEqual(subscription.paid_until(self.c, "u", 77), u_until)

    def test_cap_raises_limits_for_vip_only(self):
        subscription.extend(self.c, "v", 77, 30)
        self.assertEqual(vip.cap(77, 50, self.c), 500)
        self.assertEqual(vip.cap(88, 50, self.c), 50)
        self.assertEqual(vip.cap(77, 2, self.c, expensive=True), 6)
        self.assertEqual(vip.cap(77, 0, self.c), 0)

    def test_multiplier_from_env(self):
        subscription.extend(self.c, "v", 77, 30)
        with mock.patch.dict(os.environ, {"VIP_LIMIT_MULTIPLIER": "4"}):
            self.assertEqual(vip.cap(77, 5, self.c), 20)

    def test_revoke_clears_vip(self):
        subscription.extend(self.c, "v", 77, 30)
        self.assertTrue(vip.is_vip(77, self.c))
        subscription.revoke(self.c, "v", 77)
        self.assertFalse(vip.is_vip(77, self.c))

    def test_expired_vip_is_normal(self):
        subscription.extend(self.c, "v", 77, 30, now=1_000_000)
        self.assertEqual(vip.cap(77, 50, self.c), 50)

    def test_vip_revenue_counts_10(self):
        self.assertEqual(subscription.price_value(subscription.settings().vip_price), 10.0)


if __name__ == "__main__":
    unittest.main()
