"""👑 VIP members (SUB_VIP_PRICE, default 10$ for SUB_DAYS days).

A VIP is a full member (the "u" period is extended together with "v") whose daily limits are
raised to "unlimited for a normal person" — every per-person daily cap is multiplied by
VIP_LIMIT_MULTIPLIER (default 10: AI chat 50 → 500, photo edits 3 → 30, ...). Lanes that cost
the most per use (AI songs) use VIP_EXPENSIVE_MULTIPLIER (default 3) so one heavy user cannot
cost more than the bot earns. VIP is NOT admin: no owner commands, revenue, approvals or
member management. Groups are not included (still the group price).
"""
import os
import time

_cache = {}          # uid -> (is_vip, checked_at)
_TTL = 60


def _cfg(config):
    if config is not None:
        return config
    import config as _config
    return _config


def multiplier(expensive=False):
    name, default = ("VIP_EXPENSIVE_MULTIPLIER", 3) if expensive else ("VIP_LIMIT_MULTIPLIER", 10)
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def is_vip(uid, config=None, now=None):
    if uid is None:
        return False
    now = now or time.time()
    hit = _cache.get(uid)
    if hit and now - hit[1] < _TTL:
        return hit[0]
    try:
        import subscription
        ok = subscription.is_active(_cfg(config), "v", int(uid), now)
    except Exception:
        ok = False
    _cache[uid] = (ok, now)
    if len(_cache) > 5000:
        _cache.clear()
    return ok


def forget(uid=None):
    """Drop the cached answer (after a VIP payment is approved or revoked)."""
    if uid is None:
        _cache.clear()
    else:
        _cache.pop(uid, None)


def cap(uid, base, config=None, expensive=False):
    """The daily limit for this person: base for members, raised for VIPs. base <= 0 is left alone."""
    try:
        base = int(base)
    except (TypeError, ValueError):
        return base
    if base <= 0 or not is_vip(uid, config):
        return base
    return base * multiplier(expensive)
