"""Nearby EV chargers / fuel stations from Google Maps, via Gemini "Grounding with Google Maps".

OpenStreetMap knows very few EV chargers in Cambodia; Google Maps knows hundreds. This asks Gemini
(with the googleMaps tool and the user's GPS point) for the nearest places and keeps ONLY the ones
that Google Maps itself returned as grounding sources — so names are real, never invented.

Free quota: 5,000 grounded requests / month on Gemini 3+ models (then paid). Results are cached per
~1 km square for MAPS_CACHE_MINUTES (default 30) to save quota. MAPS_GROUNDING=false turns it off.
"""
import logging
import math
import os
import re
import time
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)
API = "https://generativelanguage.googleapis.com/v1beta"
MODELS = ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash")
WHAT = {"ev": "electric vehicle (EV) charging stations", "fuel": "fuel / gas / petrol stations"}
_cache = {}
FREE_PER_MONTH = 5000      # Google's free grounded requests / month (Gemini 3+)


def monthly_limit():
    """Stay safely under the free quota so the owner never pays (MAPS_MONTHLY_LIMIT, default 4000)."""
    try:
        return int(os.getenv("MAPS_MONTHLY_LIMIT", "4000") or 4000)
    except ValueError:
        return 4000


def _quota_file():
    db = os.getenv("BOT_DB_PATH", "bot_database.db")
    return os.path.join(os.path.dirname(os.path.abspath(db)), "maps_quota.json")


def _month():
    return time.strftime("%Y-%m", time.gmtime())


def used_this_month():
    import json
    try:
        with open(_quota_file()) as f:
            data = json.load(f)
        return int(data.get("used", 0)) if data.get("month") == _month() else 0
    except Exception:
        return 0


def _add_usage(n):
    import json
    total = used_this_month() + n          # read before opening for write (that truncates the file)
    try:
        with open(_quota_file(), "w") as f:
            json.dump({"month": _month(), "used": total}, f)
    except Exception:
        pass


def enabled(api_key):
    return bool(api_key) and os.getenv("MAPS_GROUNDING", "true").strip().lower() not in ("0", "false", "no", "off")


def _km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _norm(text):
    return re.sub(r"[^0-9a-zក-៿]+", "", (text or "").casefold())


def _num(text):
    m = re.search(r"-?\d+(?:\.\d+)?", text or "")
    return float(m.group()) if m else None


def prompt(kind, lat=None, lon=None, place="", n=10):
    where = (f"my current location (latitude {lat:.5f}, longitude {lon:.5f})" if lat is not None
             else f"{place}, Cambodia")
    return (f"Using Google Maps, find the {n} {WHAT.get(kind, WHAT['fuel'])} nearest to {where}. "
            "Nearest first. Output ONLY one line per place, no other text, in this exact format:\n"
            "NAME | ADDRESS | DISTANCE_KM | LATITUDE | LONGITUDE | DETAILS\n"
            "DETAILS = brand/operator, connector types, kW, opening hours if known, otherwise -. "
            "Use the exact place name as shown on Google Maps.")


def parse(text, chunks, kind, lat=None, lon=None):
    """text: model answer; chunks: groundingChunks. Returns station dicts (only places Google returned)."""
    places = []
    for c in chunks or []:
        m = c.get("maps") or {}
        if m.get("title"):
            places.append(m)
    out, seen = [], set()
    for line in (text or "").splitlines():
        parts = [p.strip(" *`-\t") for p in line.split("|")]
        if len(parts) < 3 or not parts[0] or parts[0].upper() == "NAME":
            continue
        name = parts[0]
        key = _norm(name)
        match = next((p for p in places if _norm(p["title"]) == key), None) or next(
            (p for p in places if key and (_norm(p["title"]) in key or key in _norm(p["title"]))), None)
        if not match or match["title"] in seen:
            continue
        seen.add(match["title"])
        plat = _num(parts[3]) if len(parts) > 3 else None
        plon = _num(parts[4]) if len(parts) > 4 else None
        if plat is not None and plon is not None and not (9 <= plat <= 15 and 102 <= plon <= 108):
            plat = plon = None
        dist = _km(lat, lon, plat, plon) if lat is not None and plat is not None else _num(parts[2])
        details = parts[5] if len(parts) > 5 and parts[5] not in ("", "-") else ""
        pid = (match.get("placeId") or "").replace("places/", "")
        dest = {"api": "1", "destination": match["title"]}
        if pid:
            dest["destination_place_id"] = pid
        elif plat is not None:
            dest["destination"] = f"{plat},{plon}"
        out.append({"name": match["title"], "address": parts[1] if parts[1] not in ("", "-") else "Google Maps",
                    "distance_km": dist, "operator": None, "connectors": [], "power": None, "details": details or None,
                    "hours": None, "phone": None, "kind": kind, "lat": plat, "lon": plon, "source": "google",
                    "maps_url": "https://www.google.com/maps/dir/?" + urlencode(dest)})
    # Places Google returned that the model did not list still count (no distance known)
    for p in places:
        if p["title"] not in seen and len(out) < 15:
            seen.add(p["title"])
            out.append({"name": p["title"], "address": "Google Maps", "distance_km": None, "operator": None,
                        "connectors": [], "power": None, "hours": None, "phone": None, "kind": kind,
                        "lat": None, "lon": None, "source": "google",
                        "maps_url": p.get("uri") or "https://www.google.com/maps/search/?" + urlencode(
                            {"api": "1", "query": p["title"]})})
    out.sort(key=lambda s: (s["distance_km"] is None, s["distance_km"] or 0))
    return out


def search(api_key, kind, lat=None, lon=None, place="", client=None, timeout=None):
    """List of station dicts (may be empty). Raises RuntimeError when Gemini can't be reached."""
    kind = kind or "fuel"
    if timeout is None:
        try:
            timeout = float(os.getenv("MAPS_TIMEOUT", "30") or 30)
        except ValueError:
            timeout = 30.0
    key = (kind, round(lat, 2), round(lon, 2)) if lat is not None else (kind, _norm(place))
    ttl = 60 * int(os.getenv("MAPS_CACHE_MINUTES", "30") or 30)
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    if used_this_month() >= monthly_limit():
        raise RuntimeError(f"free monthly quota reached ({used_this_month()}/{monthly_limit()})")
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt(kind, lat, lon, place)}]}],
               "tools": [{"googleMaps": {}}],
               "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096}}
    if lat is not None:
        payload["toolConfig"] = {"retrievalConfig": {"latLng": {"latitude": lat, "longitude": lon}}}
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0))
    errors = []
    try:
        for model in [os.getenv("MAPS_AI_MODEL", "").strip(), *MODELS]:
            if not model:
                continue
            try:
                r = client.post(f"{API}/models/{model}:generateContent", json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                errors.append(f"{model}: {type(exc).__name__}")
                break          # Google is slow right now — don't make the user wait for every model; OSM takes over
            except httpx.HTTPError as exc:
                errors.append(f"{model}: {type(exc).__name__}")
                continue
            if r.status_code != 200:
                errors.append(f"{model}: HTTP {r.status_code}")
                if r.status_code in (401, 403):
                    break
                continue
            cands = r.json().get("candidates") or []
            if not cands:
                errors.append(f"{model}: no candidates")
                continue
            parts = (cands[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text") or "" for p in parts if not p.get("thought"))
            meta = cands[0].get("groundingMetadata") or {}
            chunks = meta.get("groundingChunks") or []
            # Google bills each search the model runs, so count them all (at least 1)
            _add_usage(max(1, len(meta.get("webSearchQueries") or meta.get("retrievalQueries") or [])))
            stations = parse(text, chunks, kind, lat, lon)
            _cache[key] = (time.time() + ttl, stations)
            while len(_cache) > 300:
                _cache.pop(next(iter(_cache)))
            return stations
    finally:
        if own:
            client.close()
    raise RuntimeError("; ".join(errors)[:300] or "no model")
