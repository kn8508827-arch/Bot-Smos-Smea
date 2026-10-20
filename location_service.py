"""Cambodia fuel and EV station search backed by OpenStreetMap data."""
from __future__ import annotations

import math
import re
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import httpx


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
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

_FUEL_TERMS = (
    "ចាក់សាំង", "កន្លែងចាក់សាំង", "ស្ថានីយសាំង", "ប្រេងឥន្ធនៈ",
    "fuel", "petrol", "gas station", "gasoline", "ptt", "caltex",
    "calt​ex", "tela", "totalenergies", "total",
)
_EV_TERMS = (
    "សាកឡាន", "សាករថយន្ត", "សាក ev", "ឡានអគ្គិសនី", "ស្ថានីយសាក",
    "ev charger", "ev charging", "charging station", "charger", "charging",
    "ccs2", "gb/t", "gbt",
)
_REMOVE_TERMS = (
    "near me", "nearby", "near", "find", "search", "show me", "where is",
    "in cambodia", "cambodia", "ជិតខ្ញុំ", "នៅជិតខ្ញុំ", "រក", "ស្វែងរក",
    "កន្លែង", "ស្ថានីយ", "នៅ", "ក្នុង", "មើលផែនទី", "តាមខេត្ត",
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
    return earth_radius_km * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def normalize_query(query: str) -> dict[str, Optional[str]]:
    """Classify fuel/EV intent and extract a location or operator filter."""
    cleaned = " ".join((query or "").strip().split())
    lowered = cleaned.lower()
    kind = "fuel" if any(term in lowered for term in _FUEL_TERMS) else None
    if any(term in lowered for term in _EV_TERMS):
        kind = "ev"

    operator = next((brand for brand in _BRANDS if brand in lowered), None)
    location = lowered
    for term in sorted(_FUEL_TERMS + _EV_TERMS + _REMOVE_TERMS, key=len, reverse=True):
        location = re.sub(re.escape(term), " ", location, flags=re.IGNORECASE)
    location = re.sub(r"[📍⛽⚡🔎🗺️,!?។៖]+", " ", location)
    location = " ".join(location.split()).strip()
    for khmer_name, english_name in PROVINCES.items():
        if khmer_name in location:
            location = location.replace(khmer_name, english_name)
            break
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
        "power": tags.get("charging_station:output") or tags.get("socket:output"),
    }
    return station


class LocationService:
    def __init__(self, get: Callable[..., Any] = httpx.get):
        self._get = get

    def _request_json(self, url: str, *, params: Optional[dict[str, Any]] = None, data: Optional[str] = None):
        global _last_request_at
        with _CACHE_LOCK:
            wait = 1.0 - (time.monotonic() - _last_request_at)
            if wait > 0:
                time.sleep(wait)
            _last_request_at = time.monotonic()
        response = self._get(
            url,
            params=params,
            content=data,
            timeout=20.0,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        return response.json()

    def _geocode(self, location: str) -> Optional[tuple[float, float]]:
        key = location.casefold()
        now = time.monotonic()
        with _CACHE_LOCK:
            cached = _geocode_cache.get(key)
            if cached and cached[0] > now:
                return cached[1]
        response = self._request_json(
            NOMINATIM_URL,
            params={
                "q": f"{location}, Cambodia",
                "countrycodes": "kh",
                "format": "jsonv2",
                "limit": 1,
            },
        )
        coords = None
        if response:
            try:
                lat, lon = float(response[0]["lat"]), float(response[0]["lon"])
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    coords = (lat, lon)
            except (KeyError, TypeError, ValueError):
                coords = None
        with _CACHE_LOCK:
            _geocode_cache[key] = (now + _GEOCODE_CACHE_TTL, coords)
        return coords

    def _fetch_stations(self, lat: float, lon: float, radius: int) -> list[dict[str, Any]]:
        # Tile public station data, rather than retaining an exact user's coordinates.
        tile_lat, tile_lon = round(lat, 2), round(lon, 2)
        cache_key = (tile_lat, tile_lon, radius)
        now = time.monotonic()
        with _CACHE_LOCK:
            cached = _station_cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]

        query = (
            "[out:json][timeout:18];("
            f'nwr(around:{radius},{tile_lat},{tile_lon})["amenity"="fuel"];'
            f'nwr(around:{radius},{tile_lat},{tile_lon})["amenity"="charging_station"];'
            ");out center tags;"
        )
        data = self._request_json(OVERPASS_URL, data=query)
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
            _station_cache[cache_key] = (now + _STATION_CACHE_TTL, stations)
        return stations

    def search(
        self,
        query: str = "",
        *,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        kind: Optional[str] = None,
        radius_m: int = SEARCH_RADIUS_METERS,
    ) -> dict[str, Any]:
        parsed = normalize_query(query)
        kind = kind or parsed["kind"]
        location = parsed["location"]
        operator = parsed["operator"]
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
            stations = self._fetch_stations(lat, lon, radius_m)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise LocationSearchError("Station data is temporarily unavailable.") from exc

        results = []
        seen = set()
        for station in stations:
            if kind and station["kind"] != kind:
                continue
            if operator and operator not in " ".join(
                str(station.get(field) or "") for field in ("name", "operator")
            ).casefold():
                continue
            if kind == "ev" and query:
                lowered = query.casefold()
                requested = "ccs2" if "ccs2" in lowered else ("gb/t" if "gb/t" in lowered else None)
                if requested and requested not in [value.casefold() for value in station["connectors"]]:
                    continue
            key = (station["name"].casefold(), round(station["lat"], 4), round(station["lon"], 4))
            if key in seen:
                continue
            seen.add(key)
            result = dict(station)
            result["distance_km"] = distance_km(lat, lon, station["lat"], station["lon"])
            result["maps_url"] = (
                "https://www.google.com/maps/dir/?"
                + urlencode({"api": "1", "destination": f"{station['lat']},{station['lon']}"})
            )
            results.append(result)
        results.sort(key=lambda station: station["distance_km"])
        return {"status": "ok" if results else "empty", "stations": results, "origin": origin}


location_service = LocationService()
