"""Cambodia fuel and EV station search backed by OpenStreetMap data."""
from __future__ import annotations

import math
import re
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import logging

import httpx

_log = logging.getLogger(__name__)


def _describe_http_error(exc: BaseException) -> str:
    """Short, secret-free description of a provider error for the logs."""
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None):
        return f"HTTP {response.status_code}"
    return type(exc).__name__


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Public Overpass mirrors, tried in order when one is busy, rate-limited or blocks the server's IP.
OVERPASS_URLS = (
    OVERPASS_URL,
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "CoolerBot-Cambodia-Location-Search/1.0"
RESULT_LIMIT = 5
SEARCH_RADIUS_METERS = 25000
_STATION_CACHE_TTL = 1800
_GEOCODE_CACHE_TTL = 86400

PROVINCES = {
    "បន្ទាយមានជ័យ": "Banteay Meanchey",
    "បាត់ដំបង": "Battambang",
    "កំពង់ចាម": "Kampong Cham",
    "កំពង់ឆ្នាំង": "Kampong Chhnang",
    "កំពង់ស្ពឺ": "Kampong Speu",
    "កំពង់ធំ": "Kampong Thom",
    "កំពត": "Kampot",
    "កណ្ដាល": "Kandal",
    "កោះកុង": "Koh Kong",
    "ក្រចេះ": "Kratie",
    "មណ្ឌលគិរី": "Mondulkiri",
    "ភ្នំពេញ": "Phnom Penh",
    "ព្រះវិហារ": "Preah Vihear",
    "ព្រៃវែង": "Prey Veng",
    "ពោធិ៍សាត់": "Pursat",
    "រតនគិរី": "Ratanakiri",
    "សៀមរាប": "Siem Reap",
    "ព្រះសីហនុ": "Preah Sihanouk",
    "ស្ទឹងត្រែង": "Stung Treng",
    "ស្វាយរៀង": "Svay Rieng",
    "តាកែវ": "Takeo",
    "ឧត្តរមានជ័យ": "Oddar Meanchey",
    "កែប": "Kep",
    "ប៉ៃលិន": "Pailin",
    "ត្បូងឃ្មុំ": "Tboung Khmum",
}

# Province centres (lat, lon), so the 25 provinces never need the Nominatim geocoder.
PROVINCE_COORDS = {
    "banteay meanchey": (13.5859, 102.9737),
    "battambang": (13.0957, 103.2022),
    "kampong cham": (11.9934, 105.4635),
    "kampong chhnang": (12.2500, 104.6667),
    "kampong speu": (11.4533, 104.5200),
    "kampong thom": (12.7111, 104.8887),
    "kampot": (10.6104, 104.1815),
    "kandal": (11.4550, 104.9390),
    "koh kong": (11.6153, 102.9838),
    "kratie": (12.4881, 106.0188),
    "mondulkiri": (12.4558, 107.1881),
    "phnom penh": (11.5564, 104.9282),
    "preah vihear": (13.8073, 104.9800),
    "prey veng": (11.4868, 105.3253),
    "pursat": (12.5388, 103.9192),
    "ratanakiri": (13.7394, 106.9873),
    "siem reap": (13.3671, 103.8448),
    "preah sihanouk": (10.6253, 103.5234),
    "sihanoukville": (10.6253, 103.5234),
    "stung treng": (13.5259, 105.9683),
    "svay rieng": (11.0878, 105.7993),
    "takeo": (10.9908, 104.7850),
    "oddar meanchey": (14.1818, 103.5176),
    "kep": (10.4829, 104.3167),
    "pailin": (12.8489, 102.6093),
    "tboung khmum": (11.9200, 105.6700),
}


def _province_coords(location: str) -> Optional[tuple[float, float]]:
    """Exact province name first, then the longest province name found inside the text."""
    key = " ".join((location or "").casefold().split())
    if key in PROVINCE_COORDS:
        return PROVINCE_COORDS[key]
    best = None
    for name, coords in PROVINCE_COORDS.items():
        if name in key and (best is None or len(name) > len(best[0])):
            best = (name, coords)
    return best[1] if best else None


_FUEL_TERMS = (
    "ចាក់សាំង", "កន្លែងចាក់សាំង", "ស្ថានីយសាំង", "ស្ថានីយប្រេង",
    "កន្លែងលក់ប្រេង", "ប្រេងឥន្ធនៈ", "សាំង",
    "fuel", "petrol", "gas station", "gasoline", "ptt", "caltex",
    "calt​ex", "tela", "totalenergies", "total",
)
_EV_TERMS = (
    "សាកឡាន", "សាករថយន្ត", "សាកភ្លើង", "សាក ev", "ឡានអគ្គិសនី",
    "រថយន្តអគ្គិសនី", "ស្ថានីយសាក",
    "ev chargers",
    "ev charger", "ev charging", "ev station", "charging station",
    "charging point", "charge point", "charger", "charging",
    "ccs2", "gb/t", "gbt",
)
_REMOVE_TERMS = (
    "near me", "nearby", "near", "find", "search", "show me", "where is",
    "in cambodia", "cambodia", " in ", " of ", " ev ", "province", "within", "stations", "station",
    "fast", "rapid", "slow", "charger", "chargers",
    "ev station", "ev charging", "charging station", "charging point",
    "charging points", "charge point", "charge points",
    "ជិតខ្ញុំ", "នៅជិតខ្ញុំ", "រក", "ស្វែងរក", "ខេត្ត",
    "កន្លែង", "ស្ថានីយ", "សាំង", "ប្រេង", "សាកភ្លើង", "សាករថយន្ត",
    "សាកឡាន", "ឡានអគ្គិសនី", "រថយន្តអគ្គិសនី", "នៅ", "ក្នុង",
    "មើលផែនទី", "តាមខេត្ត",
)
_BRANDS = ("ptt", "caltex", "tela", "totalenergies", "total")
_CACHE_LOCK = threading.Lock()
_station_cache: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = {}
_geocode_cache: dict[str, tuple[float, Optional[tuple[float, float]]]] = {}
_last_request_at = 0.0


class LocationSearchError(Exception):
    """The public location data source could not fulfill a search."""


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in kilometers."""
    earth_radius_km = 6371.0088
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    haversine = min(1.0, max(0.0, haversine))
    return earth_radius_km * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def _fix_khmer(text: str) -> str:
    """Normalise how phones type Khmer: NFC, drop zero-width marks, and put
    AA before NIKAHIT (some keyboards send "ំា" instead of "ាំ", e.g. សំាង)."""
    import unicodedata
    text = unicodedata.normalize("NFC", text or "")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return text.replace("\u17c6\u17b6", "\u17b6\u17c6")


def normalize_query(query: str) -> dict[str, Optional[str]]:
    """Classify fuel/EV intent and extract a location or operator filter."""
    cleaned = " ".join(_fix_khmer(query).strip().split())
    lowered = cleaned.lower()
    kind = "fuel" if any(term in lowered for term in _FUEL_TERMS) else None
    if any(term in lowered for term in _EV_TERMS) or re.search(r"(?<![a-z])ev(?![a-z])", lowered):
        kind = "ev"

    operator = next((brand for brand in _BRANDS if brand in lowered), None)
    location = lowered
    for term in sorted(_FUEL_TERMS + _EV_TERMS + _REMOVE_TERMS, key=len, reverse=True):
        location = re.sub(re.escape(term), " ", location, flags=re.IGNORECASE)
    location = re.sub(r"\bev\b", " ", location, flags=re.IGNORECASE)
    location = re.sub(r"\b\d+(?:\.\d+)?\s*(?:km|kilometers?|គម)\b", " ", location)
    location = re.sub(r"[📍⛽⚡🔎🗺️,!?។៖]+", " ", location)
    location = " ".join(location.split()).strip()
    for khmer_name, english_name in PROVINCES.items():
        if khmer_name in location:
            location = location.replace(khmer_name, english_name)
            break
    location = location.casefold()
    return {
        "kind": kind,
        "location": location or None,
        "operator": operator,
    }


def _station_coordinates(element: dict[str, Any]) -> Optional[tuple[float, float]]:
    lat, lon = element.get("lat"), element.get("lon")
    center = element.get("center") or {}
    lat = lat if lat is not None else center.get("lat")
    lon = lon if lon is not None else center.get("lon")
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _connector_types(tags: dict[str, str]) -> list[str]:
    labels = {
        "socket:type2": "Type 2",
        "socket:type2_combo": "CCS2",
        "socket:ccs": "CCS",
        "socket:chademo": "CHAdeMO",
        "socket:gbt": "GB/T",
        "socket:tesla_supercharger": "Tesla",
    }
    found = []
    for key, label in labels.items():
        value = str(tags.get(key, "")).strip().lower()
        if value and value not in ("no", "0", "false"):
            found.append(label)
    return found


def _power_kw(station: dict[str, Any]) -> Optional[float]:
    value = station.get("power")
    values = re.findall(
        r"(\d+(?:\.\d+)?)\s*(?:kW|kilowatts?)",
        str(value or ""),
        re.IGNORECASE,
    )
    return max((float(item) for item in values), default=None)


def _power_output(tags: dict[str, Any]) -> Optional[str]:
    """Collect station- and connector-level OSM power tags for display/filtering."""
    connector_labels = {
        "socket:type2": "Type 2",
        "socket:type2_combo": "CCS2",
        "socket:ccs": "CCS",
        "socket:chademo": "CHAdeMO",
        "socket:gbt": "GB/T",
        "socket:type1": "Type 1",
        "socket:type1_combo": "CCS1",
        "socket:tesla_supercharger": "Tesla",
    }
    outputs = []
    for key, value in tags.items():
        if key in ("charging_station:output", "socket:output"):
            label = None
        elif key.startswith("socket:") and key.endswith(":output"):
            connector = key[:-len(":output")]
            label = connector_labels.get(connector, connector.removeprefix("socket:"))
        else:
            continue
        text = str(value).strip()
        if text:
            outputs.append(f"{label}: {text}" if label else text)
    return "; ".join(outputs) or None


def _requested_radius(query: str) -> Optional[int]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:km|kilometers?|គម)", query or "", re.IGNORECASE)
    if not match:
        return None
    return max(1000, min(int(float(match.group(1)) * 1000), 100000))


def _parse_element(element: dict[str, Any]) -> Optional[dict[str, Any]]:
    tags = element.get("tags") or {}
    coords = _station_coordinates(element)
    if coords is None:
        return None
    is_ev = tags.get("amenity") == "charging_station"
    if not is_ev and tags.get("amenity") != "fuel":
        return None
    name = tags.get("name") or tags.get("brand") or tags.get("operator")
    address_parts = [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:suburb") or tags.get("addr:city"),
        tags.get("addr:district") or tags.get("addr:county"),
        tags.get("addr:province") or tags.get("addr:state"),
    ]
    address = ", ".join(part for part in address_parts if part)
    if not address:
        address = "អាសយដ្ឋានមិនមានក្នុងទិន្នន័យផែនទី"
    station = {
        "id": f"{element.get('type', '')}/{element.get('id', '')}",
        "name": name or ("ស្ថានីយសាក EV មិនមានឈ្មោះ" if is_ev else "ស្ថានីយប្រេងឥន្ធនៈមិនមានឈ្មោះ"),
        "address": address,
        "operator": tags.get("operator") or tags.get("brand"),
        "kind": "ev" if is_ev else "fuel",
        "lat": coords[0],
        "lon": coords[1],
        "hours": tags.get("opening_hours"),
        "phone": tags.get("contact:phone") or tags.get("phone"),
        "connectors": _connector_types(tags) if is_ev else [],
        "power": _power_output(tags),
    }
    return station


class LocationService:
    def __init__(
        self,
        get: Optional[Callable[..., Any]] = None,
        post: Optional[Callable[..., Any]] = None,
    ):
        self._get = get or httpx.get
        self._post = post or httpx.post

    def _request_json(self, url: str, *, params: Optional[dict[str, Any]] = None, data: Optional[str] = None):
        global _last_request_at
        with _CACHE_LOCK:
            wait = 1.0 - (time.monotonic() - _last_request_at)
            if wait > 0:
                time.sleep(wait)
            _last_request_at = time.monotonic()
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Language": "km,en"}
        if data is None:
            response = self._get(url, params=params, timeout=15.0, headers=headers)
        else:
            # Overpass expects the query as a form field named "data".
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            response = self._post(url, content=urlencode({"data": data}), timeout=25.0, headers=headers)
        response.raise_for_status()
        return response.json()

    def _geocode(self, location: str) -> Optional[tuple[float, float]]:
        key = location.casefold()
        known = PROVINCE_COORDS.get(" ".join(key.split()))
        if known:
            return known
        now = time.monotonic()
        with _CACHE_LOCK:
            cached = _geocode_cache.get(key)
            if cached and cached[0] > now:
                return cached[1]
        try:
            response = self._nominatim(location)
        except (httpx.HTTPError, ValueError, TypeError):
            fallback = _province_coords(location)
            if fallback:
                return fallback
            raise
        coords = None
        if response:
            try:
                lat, lon = float(response[0]["lat"]), float(response[0]["lon"])
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    coords = (lat, lon)
            except (KeyError, TypeError, ValueError):
                coords = None
        if coords is None:
            coords = _province_coords(location)
        with _CACHE_LOCK:
            for old_key, (expires_at, _) in list(_geocode_cache.items()):
                if expires_at <= now:
                    _geocode_cache.pop(old_key, None)
            _geocode_cache[key] = (now + _GEOCODE_CACHE_TTL, coords)
            while len(_geocode_cache) > 500:
                _geocode_cache.pop(next(iter(_geocode_cache)))
        return coords

    def _nominatim(self, location: str):
        return self._request_json(
            NOMINATIM_URL,
            params={
                "q": f"{location}, Cambodia",
                "countrycodes": "kh",
                "format": "jsonv2",
                "limit": 1,
            },
        )

    def _overpass(self, query: str):
        """Ask every Overpass mirror at the same time; the first good answer wins (max ~25 s total)."""
        from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutTimeout
        last_exc: Optional[Exception] = None
        pool = ThreadPoolExecutor(max_workers=len(OVERPASS_URLS))
        futures = {pool.submit(self._request_json, url, data=query): url for url in OVERPASS_URLS}
        try:
            for fut in as_completed(futures, timeout=26):
                url = futures[fut]
                try:
                    data = fut.result()
                    if isinstance(data, dict) and "elements" in data:
                        return data
                    last_exc = ValueError(f"unexpected Overpass response from {url}")
                except (httpx.HTTPError, ValueError) as exc:
                    last_exc = exc
                    _log.warning("Overpass mirror failed: %s (%s)", url, _describe_http_error(exc))
        except FutTimeout:
            last_exc = last_exc or httpx.ReadTimeout("all Overpass mirrors were too slow")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        raise last_exc or ValueError("no Overpass mirror answered")

    def _fetch_stations(self, lat: float, lon: float, radius: int) -> list[dict[str, Any]]:
        # Tile public station data, rather than retaining an exact user's coordinates.
        tile_lat, tile_lon = round(lat, 2), round(lon, 2)
        cache_key = (tile_lat, tile_lon, radius)
        now = time.monotonic()
        with _CACHE_LOCK:
            cached = _station_cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]

        query_radius = radius + 1000
        query = (
            "[out:json][timeout:18];("
            f'nwr(around:{query_radius},{tile_lat},{tile_lon})["amenity"="fuel"];'
            f'nwr(around:{query_radius},{tile_lat},{tile_lon})["amenity"="charging_station"];'
            ");out center tags;"
        )
        data = self._overpass(query)
        elements = data.get("elements", []) if isinstance(data, dict) else []
        stations = []
        seen = set()
        for element in elements:
            station = _parse_element(element)
            if station is None or station["id"] in seen:
                continue
            seen.add(station["id"])
            stations.append(station)
        with _CACHE_LOCK:
            for old_key, (expires_at, _) in list(_station_cache.items()):
                if expires_at <= now:
                    _station_cache.pop(old_key, None)
            _station_cache[cache_key] = (now + _STATION_CACHE_TTL, stations)
            while len(_station_cache) > 250:
                _station_cache.pop(next(iter(_station_cache)))
        return stations

    def search(
        self,
        query: str = "",
        *,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        kind: Optional[str] = None,
        radius_m: Optional[int] = None,
    ) -> dict[str, Any]:
        parsed = normalize_query(query)
        kind = kind or parsed["kind"]
        location = parsed["location"]
        operator = parsed["operator"]
        requested_radius = radius_m or _requested_radius(query) or SEARCH_RADIUS_METERS
        power_match = re.search(
            r"(\d+(?:\.\d+)?)\s*kw", query or "", re.IGNORECASE
        )
        requested_power = float(power_match.group(1)) if power_match else None
        lowered_query = (query or "").casefold()
        fast_requested = any(word in lowered_query for word in ("fast", "rapid", "លឿន"))
        slow_requested = any(word in lowered_query for word in ("slow", "យឺត"))
        origin = "shared location"
        if location:
            origin = "search location"
            try:
                geocoded = self._geocode(location)
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise LocationSearchError("Location lookup is temporarily unavailable.") from exc
            if geocoded is None:
                return {"status": "not_found", "stations": [], "origin": origin}
            lat, lon = geocoded
        elif lat is None or lon is None:
            return {"status": "missing_location", "stations": [], "origin": None}

        try:
            lat, lon = float(lat), float(lon)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("Invalid coordinates")
            stations = self._fetch_stations(lat, lon, requested_radius)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise LocationSearchError("Station data is temporarily unavailable.") from exc

        results = []
        seen = set()
        for station in stations:
            if not station:
                continue
            if kind and station["kind"] != kind:
                continue
            if operator and operator not in " ".join(
                str(station.get(field) or "") for field in ("name", "operator")
            ).casefold():
                continue
            if kind == "ev" and query:
                requested = (
                    "ccs2" if "ccs2" in lowered_query
                    else ("gb/t" if "gb/t" in lowered_query or "gbt" in lowered_query else None)
                )
                if requested and requested not in [value.casefold() for value in station["connectors"]]:
                    continue
                power_kw = _power_kw(station)
                if requested_power is not None and (power_kw is None or power_kw < requested_power):
                    continue
                if fast_requested and (power_kw is None or power_kw < 50):
                    continue
                if slow_requested and (power_kw is None or power_kw > 22):
                    continue
            key = (station["name"].casefold(), round(station["lat"], 4), round(station["lon"], 4))
            if key in seen:
                continue
            seen.add(key)
            result = dict(station)
            result["distance_km"] = distance_km(lat, lon, station["lat"], station["lon"])
            if result["distance_km"] > requested_radius / 1000:
                continue
            result["maps_url"] = (
                "https://www.google.com/maps/dir/?"
                + urlencode({"api": "1", "destination": f"{station['lat']},{station['lon']}"})
            )
            results.append(result)
        results.sort(key=lambda station: station["distance_km"])
        return {"status": "ok" if results else "empty", "stations": results, "origin": origin,
                "center": (lat, lon), "radius_m": requested_radius}


location_service = LocationService()
