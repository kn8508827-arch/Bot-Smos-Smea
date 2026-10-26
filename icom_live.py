"""🎙️ ICOM inside Cooler Hub — LiveKit voice for paying members, no Sign-in.

Telegram already tells the mini app who the member is (Telegram.WebApp.initData, signed with the
bot token). Every request carries that initData; we check the signature and that the member is
owner-approved (paid $1, or owner/admin). Unpaid people get nothing — not even the member list.

  👥 Group   : everyone in one LiveKit room (auto-join when ICOM opens)
  👤 Private : tap a member → room dm-<low id>-<high id>, only those two may join
  Talking    : tap to talk, tap again to stop; several people can talk at the same time

HTTP (same small server as the Messenger webhook, needs PORT + a public domain):
  GET  /icom            the page (assets/icom.html)
  POST /icom/token      {initData, peer?}  → LiveKit url + token (group, or private with peer)
  POST /icom/members    {initData}         → approved members only
  POST /icom/ring       {initData, peer}   → bot sends the peer a "📻 ... is calling" button

Railway variables (keep them out of GitHub):
  LIVEKIT_URL         wss://<project>.livekit.cloud
  LIVEKIT_API_KEY     API key from LiveKit Cloud → Settings → Keys
  LIVEKIT_API_SECRET  its secret
  ICOM_ROOM           optional group room name (default team-smos-snea)
  ICOM_URL            https://<railway-domain>/icom  (the Hub and /icom open this)
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
from urllib.parse import parse_qsl, quote

logger = logging.getLogger(__name__)
INIT_MAX_AGE = 24 * 3600
TOKEN_TTL = 6 * 3600
RING_GAP = 30          # seconds between two rings from the same caller to the same member
_rings = {}


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


def page_url():
    return os.getenv("ICOM_URL", "").strip()


# ---------- Telegram login check ----------

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


# ---------- LiveKit token ----------

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


def dm_room(a, b):
    lo, hi = sorted((int(a), int(b)))
    return f"dm-{lo}-{hi}"


def check_livekit(timeout=8):
    """Owner check: ask LiveKit to list rooms with our key — proves URL + key + secret belong together.
    Returns (ok, short text)."""
    if not configured():
        return False, "LIVEKIT_* មិនទាន់គ្រប់"
    s = settings()
    if not s["url"].startswith("wss://"):
        return False, "LIVEKIT_URL ត្រូវចាប់ផ្ដើមដោយ wss://"
    now = int(time.time())
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"iss": s["key"], "sub": "icom-check", "nbf": now - 10, "exp": now + 60,
                            "video": {"roomList": True}}).encode())
    sig = _b64(hmac.new(s["secret"].encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    try:
        import httpx
        r = httpx.post("https://" + s["url"][6:].rstrip("/") + "/twirp/livekit.RoomService/ListRooms",
                       json={}, headers={"Authorization": f"Bearer {head}.{body}.{sig}"}, timeout=timeout)
    except Exception as exc:
        return False, f"ភ្ជាប់ LiveKit មិនបាន ({type(exc).__name__})"
    if r.status_code == 200:
        rooms = r.json().get("rooms", [])
        live = sum(int(x.get("num_participants", 0) or 0) for x in rooms)
        return True, f"LiveKit Key ត្រឹមត្រូវ · {live} នាក់កំពុងនៅក្នុង ICOM"
    if r.status_code in (401, 403):
        return False, "Key/Secret មិនត្រូវគ្នាជាមួយ LIVEKIT_URL (មកពី Project ផ្សេង ឬ Secret ខុស)"
    return False, f"LiveKit ឆ្លើយ {r.status_code}"


# ---------- who is an approved member ----------

def _staff(config):
    ids = set(getattr(config, "ADMIN_IDS", []) or [])
    if getattr(config, "OWNER_ID", 0):
        ids.add(config.OWNER_ID)
    return ids


def is_allowed(config, uid, now=None):
    if uid in _staff(config):
        return True
    try:
        import subscription as sub
        return sub.is_active(config, "u", uid, now)
    except Exception:
        return False


def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS icom_profiles (
        user_id INTEGER PRIMARY KEY, name TEXT, photo TEXT, seen_at INTEGER)""")
    return conn


def remember(config, uid, name, photo, now=None):
    try:
        with _db(config) as conn:
            conn.execute("""INSERT INTO icom_profiles (user_id, name, photo, seen_at) VALUES (?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,
                photo=COALESCE(NULLIF(excluded.photo,''), icom_profiles.photo), seen_at=excluded.seen_at""",
                         (uid, name, photo, int(now or time.time())))
    except sqlite3.Error as exc:
        logger.info("icom profile skipped (%s)", type(exc).__name__)


def members(config, now=None):
    """Owner-approved members only: active paid subscriptions + owner/admins. Unpaid never listed."""
    now = now or time.time()
    ids = set(_staff(config))
    names, photos, seen = {}, {}, {}
    try:
        with _db(config) as conn:
            try:
                ids |= {r[0] for r in conn.execute(
                    "SELECT target_id FROM subscriptions WHERE kind='u' AND paid_until>?", (now,))}
            except sqlite3.Error:
                pass
            try:
                for r in conn.execute("SELECT user_id, first_name, last_name FROM users"):
                    names[r[0]] = " ".join(x for x in (r[1], r[2]) if x)
            except sqlite3.Error:
                pass
            for r in conn.execute("SELECT user_id, name, photo, seen_at FROM icom_profiles"):
                if r["name"]:
                    names[r["user_id"]] = r["name"]
                photos[r["user_id"]] = r["photo"] or ""
                seen[r["user_id"]] = r["seen_at"] or 0
    except sqlite3.Error as exc:
        logger.info("icom members db (%s)", type(exc).__name__)
    staff = _staff(config)
    out = [{"id": i, "name": (names.get(i) or "Member")[:30], "photo": photos.get(i, ""),
            "staff": i in staff, "seen": seen.get(i, 0)} for i in ids]
    out.sort(key=lambda m: (-m["seen"], m["name"]))
    return out


# ---------- requests ----------

def _read(config, body, now):
    try:
        data = json.loads(body.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, AttributeError):
        return None, None, (400, {"error": "bad_request"})
    user = verify_init_data(data.get("initData", ""), getattr(config, "BOT_TOKEN", ""), now)
    if not user:
        return None, None, (401, {"error": "open_from_telegram"})
    uid = int(user["id"])
    if not is_allowed(config, uid, now):
        return None, None, (402, {"error": "members_only"})
    user["_name"] = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)[:30] or "Member"
    user["_photo"] = str(user.get("photo_url", ""))[:300]
    return user, data, None


def _peer(config, data, uid, now):
    try:
        peer = int(data.get("peer") or 0)
    except (TypeError, ValueError):
        return None, (400, {"error": "bad_peer"})
    if peer == uid or peer <= 0:
        return None, (400, {"error": "bad_peer"})
    if not is_allowed(config, peer, now):
        return None, (404, {"error": "not_member"})
    return peer, None


def token_response(config, body, now=None):
    """(status, dict) for POST /icom/token. body = raw JSON {"initData": "...", "peer": 123?}."""
    if not configured():
        return 503, {"error": "not_configured"}
    user, data, err = _read(config, body, now)
    if err:
        return err
    uid = int(user["id"])
    remember(config, uid, user["_name"], user["_photo"], now)
    s = settings()
    room, peer_name = s["room"], ""
    if data.get("peer"):
        peer, err = _peer(config, data, uid, now)
        if err:
            return err
        room = dm_room(uid, peer)
        peer_name = next((m["name"] for m in members(config, now) if m["id"] == peer), "Member")
    tok = livekit_token(s["key"], s["secret"], f"tg{uid}", user["_name"], room, now=now,
                        metadata=json.dumps({"p": user["_photo"]}, separators=(",", ":")))
    return 200, {"url": s["url"], "token": tok, "room": room, "me": uid, "name": user["_name"],
                 "photo": user["_photo"], "private": room != s["room"], "peerName": peer_name}


def members_response(config, body, now=None):
    user, _, err = _read(config, body, now)
    if err:
        return err
    remember(config, int(user["id"]), user["_name"], user["_photo"], now)
    return 200, {"me": int(user["id"]), "members": members(config, now)}


def ring_text(caller_name):
    return (f"📻 <b>{caller_name}</b> កំពុងហៅអ្នកក្នុង <b>Private ICOM</b>\n"
            "ចុចប៊ូតុងខាងក្រោម ដើម្បីនិយាយភ្លាម 🎙️")


def ring_response(config, body, bot=None, now=None):
    user, data, err = _read(config, body, now)
    if err:
        return err
    uid = int(user["id"])
    peer, err = _peer(config, data, uid, now)
    if err:
        return err
    t = now or time.time()
    if t - _rings.get((uid, peer), 0) < RING_GAP:
        return 429, {"error": "wait"}
    _rings[(uid, peer)] = t
    url = page_url()
    if bot is None or not url:
        return 503, {"error": "no_bot"}
    try:
        from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🎙️ ចូល Private ICOM", web_app=WebAppInfo(url=f"{url}?with={uid}")))
        name = user["_name"].replace("<", "").replace(">", "")
        bot.send_message(peer, ring_text(name), reply_markup=m, parse_mode="HTML")
    except Exception as exc:
        logger.info("icom ring failed (%s)", type(exc).__name__)
        return 502, {"error": "ring_failed"}
    return 200, {"ok": True}


def handle_post(path, config, body, bot=None, now=None):
    """Route POST /icom/<x>. Returns (status, dict) or None if the path isn't ICOM's."""
    p = path.rstrip("/")
    if p == "/icom/token":
        return token_response(config, body, now)
    if p == "/icom/members":
        return members_response(config, body, now)
    if p == "/icom/ring":
        return ring_response(config, body, bot, now)
    return None


PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icom.html")


def page_html():
    with open(PAGE_PATH, "rb") as f:
        return f.read()


def hub_link(uid=None):
    """ICOM link for buttons; with uid opens a Private ICOM with that member."""
    url = page_url()
    return f"{url}?with={quote(str(uid))}" if url and uid else url
