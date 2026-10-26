"""🎙️ ICOM inside Cooler Hub — push-to-talk on LiveKit, no Sign-in.

Telegram already tells the mini app who the member is (Telegram.WebApp.initData, signed with the
bot token). The page sends that to POST /icom/token; we check the signature, check the member has
paid (or is owner/admin), and hand back a short LiveKit token. Nobody types a phone number.

Railway variables (keep them out of GitHub):
  LIVEKIT_URL         wss://<project>.livekit.cloud
  LIVEKIT_API_KEY     API key from LiveKit Cloud → Settings → Keys
  LIVEKIT_API_SECRET  its secret
  ICOM_ROOM           optional room name (default team-smos-snea)
  ICOM_URL            https://<railway-domain>/icom  (the Hub and /icom open this)

Served by the same small web server as the Messenger webhook (needs PORT + a public domain).
"""
import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl

INIT_MAX_AGE = 24 * 3600
TOKEN_TTL = 6 * 3600


def settings():
    return {
        "url": os.getenv("LIVEKIT_URL", "").strip(),
        "key": os.getenv("LIVEKIT_API_KEY", "").strip(),
        "secret": os.getenv("LIVEKIT_API_SECRET", "").strip(),
        "room": os.getenv("ICOM_ROOM", "").strip() or "team-smos-snea",
    }


def configured():
    s = settings()
    return bool(s["url"] and s["key"] and s["secret"])


def verify_init_data(init_data, bot_token, now=None, max_age=INIT_MAX_AGE):
    """Telegram Mini App check (core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).
    Returns the user dict, or None if the data is missing, forged or too old."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = pairs.pop("hash", "")
    # every other field (including 'signature') stays in the check string, sorted (Telegram spec)
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not got or not hmac.compare_digest(want, got):
        return None
    try:
        if (now or time.time()) - int(pairs.get("auth_date", "0")) > max_age:
            return None
        user = json.loads(pairs.get("user", "{}"))
    except (ValueError, TypeError):
        return None
    return user if isinstance(user, dict) and user.get("id") else None


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def livekit_token(key, secret, identity, name, room, ttl=TOKEN_TTL, now=None, metadata=""):
    """LiveKit access token = HS256 JWT (docs.livekit.io/home/get-started/authentication)."""
    now = int(now or time.time())
    head = {"alg": "HS256", "typ": "JWT"}
    body = {"iss": key, "sub": str(identity), "name": name, "nbf": now - 10, "exp": now + ttl,
            "metadata": metadata,
            "video": {"room": room, "roomJoin": True, "canPublish": True, "canSubscribe": True,
                      "canPublishData": True, "canPublishSources": ["microphone"]}}
    msg = _b64(json.dumps(head, separators=(",", ":")).encode()) + "." + \
        _b64(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode())
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    return msg + "." + _b64(sig)


def is_allowed(config, uid, now=None):
    if uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", []):
        return True
    try:
        import subscription as sub
        return sub.is_active(config, "u", uid, now)
    except Exception:
        return False


def token_response(config, body, now=None):
    """(status, dict) for POST /icom/token. body = raw request bytes (JSON {"initData": "..."})."""
    if not configured():
        return 503, {"error": "not_configured"}
    try:
        init = json.loads(body.decode("utf-8") or "{}").get("initData", "")
    except (ValueError, AttributeError):
        return 400, {"error": "bad_request"}
    user = verify_init_data(init, getattr(config, "BOT_TOKEN", ""), now)
    if not user:
        return 401, {"error": "open_from_telegram"}
    uid = int(user["id"])
    if not is_allowed(config, uid, now):
        return 402, {"error": "members_only"}
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)[:30] or "Member"
    s = settings()
    photo = str(user.get("photo_url", ""))[:300]
    tok = livekit_token(s["key"], s["secret"], f"tg{uid}", name, s["room"], now=now,
                        metadata=json.dumps({"p": photo}, separators=(",", ":")))
    return 200, {"url": s["url"], "token": tok, "room": s["room"], "name": name,
                 "photo": photo}


PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icom.html")


def page_html():
    with open(PAGE_PATH, "rb") as f:
        return f.read()
