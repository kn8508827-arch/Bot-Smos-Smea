"""
ព្យាករណ៍អាកាសធាតុកម្ពុជា ២៥ ខេត្ត សម្រាប់ Team ស្មោះស្នេហ៍ Telegram Bot
ប្រើ Open-Meteo (ឥតគិតថ្លៃ គ្មាន API key) – ទិន្នន័យ CC BY 4.0
"""
import logging
import re
import threading
import time
from datetime import date
from typing import Any, Callable, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_SECONDS = 8.0
CACHE_TTL_SECONDS = 600
FORECAST_DAYS = 3


class WeatherError(Exception):
    """Raised when weather data cannot be fetched or parsed."""


# slug -> (ឈ្មោះខ្មែរ, English name, latitude, longitude, ឈ្មោះផ្សេងៗ)
PROVINCES: Dict[str, tuple] = {
    "phnom_penh": ("ភ្នំពេញ", "Phnom Penh", 11.5564, 104.9282, ()),
    "siem_reap": ("សៀមរាប", "Siem Reap", 13.3671, 103.8448, ()),
    "battambang": ("បាត់ដំបង", "Battambang", 13.0957, 103.2022, ()),
    "sihanoukville": ("ព្រះសីហនុ", "Sihanoukville", 10.6253, 103.5234, ("Preah Sihanouk", "កំពង់សោម")),
    "kampot": ("កំពត", "Kampot", 10.6104, 104.1815, ()),
    "kep": ("កែប", "Kep", 10.4829, 104.3167, ()),
    "kampong_cham": ("កំពង់ចាម", "Kampong Cham", 11.9934, 105.4635, ()),
    "tboung_khmum": ("ត្បូងឃ្មុំ", "Tboung Khmum", 11.9114, 105.6556, ("Tbong Khmum",)),
    "kandal": ("កណ្តាល", "Kandal", 11.4833, 104.9500, ("កណ្ដាល",)),
    "kampong_speu": ("កំពង់ស្ពឺ", "Kampong Speu", 11.4533, 104.5208, ()),
    "takeo": ("តាកែវ", "Takeo", 10.9908, 104.7850, ()),
    "prey_veng": ("ព្រៃវែង", "Prey Veng", 11.4868, 105.3253, ()),
    "svay_rieng": ("ស្វាយរៀង", "Svay Rieng", 11.0878, 105.7993, ()),
    "kampong_chhnang": ("កំពង់ឆ្នាំង", "Kampong Chhnang", 12.2500, 104.6667, ()),
    "kampong_thom": ("កំពង់ធំ", "Kampong Thom", 12.7111, 104.8887, ()),
    "pursat": ("ពោធិ៍សាត់", "Pursat", 12.5388, 103.9192, ("ពោធិសាត់",)),
    "banteay_meanchey": ("បន្ទាយមានជ័យ", "Banteay Meanchey", 13.5859, 102.9737, ()),
    "oddar_meanchey": ("ឧត្តរមានជ័យ", "Oddar Meanchey", 14.1819, 103.5175, ("Otdar Meanchey",)),
    "kratie": ("ក្រចេះ", "Kratie", 12.4881, 106.0188, ()),
    "stung_treng": ("ស្ទឹងត្រែង", "Stung Treng", 13.5259, 105.9683, ()),
    "ratanakiri": ("រតនគិរី", "Ratanakiri", 13.7394, 106.9873, ()),
    "mondulkiri": ("មណ្ឌលគិរី", "Mondulkiri", 12.4551, 107.1881, ()),
    "preah_vihear": ("ព្រះវិហារ", "Preah Vihear", 13.8000, 104.9833, ()),
    "pailin": ("ប៉ៃលិន", "Pailin", 12.8489, 102.6093, ()),
    "koh_kong": ("កោះកុង", "Koh Kong", 11.6153, 102.9840, ()),
}

KHMER_WEEKDAYS = ["ច័ន្ទ", "អង្គារ", "ពុធ", "ព្រហស្បតិ៍", "សុក្រ", "សៅរ៍", "អាទិត្យ"]  # Monday=0

# WMO weather code -> (emoji, ពាក្យខ្មែរ)
_WEATHER_CODES = {
    0: ("☀️", "មេឃច្បាស់"),
    1: ("🌤️", "ភាគច្រើនច្បាស់"),
    2: ("⛅", "មេឃមានពពកខ្លះ"),
    3: ("☁️", "ពពកក្រាស់"),
    45: ("🌫️", "អ័ព្ទ"),
    48: ("🌫️", "អ័ព្ទ"),
    51: ("🌦️", "ភ្លៀងរលឹម"),
    53: ("🌦️", "ភ្លៀងរលឹម"),
    55: ("🌦️", "ភ្លៀងរលឹម"),
    56: ("🌧️", "ភ្លៀងរលឹមត្រជាក់"),
    57: ("🌧️", "ភ្លៀងរលឹមត្រជាក់"),
    61: ("🌧️", "ភ្លៀងតិចៗ"),
    63: ("🌧️", "ភ្លៀងមធ្យម"),
    65: ("🌧️", "ភ្លៀងខ្លាំង"),
    66: ("🌧️", "ភ្លៀងត្រជាក់"),
    67: ("🌧️", "ភ្លៀងត្រជាក់"),
    80: ("🌦️", "ភ្លៀងម្តងម្កាល"),
    81: ("🌧️", "ភ្លៀងម្តងម្កាលខ្លាំង"),
    82: ("🌧️", "ភ្លៀងខ្លាំងបំផុត"),
    95: ("⛈️", "ផ្គររន្ទះ"),
    96: ("⛈️", "ផ្គររន្ទះ និងព្រឹល"),
    99: ("⛈️", "ផ្គររន្ទះ និងព្រឹល"),
}
_RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}
_THUNDER_CODES = {95, 96, 99}

_INTENT_TERMS = ("អាកាសធាតុ", "weather", "forecast")


def describe_code(code: Optional[int]) -> tuple:
    return _WEATHER_CODES.get(code, ("🌡️", "មិនស្គាល់"))


def _normalize(text: str) -> str:
    """Lowercase and drop spaces, hyphens, underscores and zero-width spaces."""
    return re.sub(r"[\s\-_​‌‍]+", "", (text or "").casefold())


def has_weather_intent(text: str) -> bool:
    lowered = (text or "").casefold()
    return any(term in lowered for term in _INTENT_TERMS)


def find_province(text: str) -> Optional[str]:
    """Return the slug of the province named in text (Khmer or English), longest match wins."""
    normalized = _normalize(text)
    if not normalized:
        return None
    best_slug, best_len = None, 0
    for slug, (name_kh, name_en, _lat, _lon, aliases) in PROVINCES.items():
        for name in (name_kh, name_en, *aliases):
            candidate = _normalize(name)
            if candidate and candidate in normalized and len(candidate) > best_len:
                best_slug, best_len = slug, len(candidate)
    return best_slug


def _round(value: Any) -> Optional[int]:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _parse(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        current = payload["current"]
        daily = payload["daily"]
        days: List[Dict[str, Any]] = []
        for index, day in enumerate(daily["time"][:FORECAST_DAYS]):
            rain = daily.get("precipitation_probability_max") or []
            days.append({
                "date": date.fromisoformat(day[:10]),
                "code": daily["weather_code"][index],
                "tmax": _round(daily["temperature_2m_max"][index]),
                "tmin": _round(daily["temperature_2m_min"][index]),
                "rain": _round(rain[index]) if index < len(rain) else None,
            })
        if not days:
            raise KeyError("daily.time is empty")
        return {
            "current": {
                "temp": _round(current.get("temperature_2m")),
                "feels": _round(current.get("apparent_temperature")),
                "humidity": _round(current.get("relative_humidity_2m")),
                "wind": _round(current.get("wind_speed_10m")),
                "code": current.get("weather_code"),
            },
            "days": days,
        }
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise WeatherError(f"unexpected Open-Meteo response ({type(exc).__name__})") from exc


class WeatherService:
    def __init__(self, get: Optional[Callable] = None, clock: Callable[[], float] = time.monotonic):
        self._get = get or httpx.get
        self._clock = clock
        self._cache: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def fetch(self, slug: str) -> Dict[str, Any]:
        if slug not in PROVINCES:
            raise WeatherError("unknown province")
        now = self._clock()
        with self._lock:
            cached = self._cache.get(slug)
            if cached and cached[0] > now:
                return cached[1]

        _kh, _en, lat, lon, _aliases = PROVINCES[slug]
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "Asia/Phnom_Penh",
            "forecast_days": FORECAST_DAYS,
        }
        try:
            response = self._get(OPEN_METEO_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = _parse(response.json())
        except WeatherError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Weather request failed (%s)", type(exc).__name__)
            raise WeatherError("weather provider request failed") from exc

        with self._lock:
            self._cache[slug] = (now + CACHE_TTL_SECONDS, data)
            if len(self._cache) > 64:
                for key in [k for k, v in self._cache.items() if v[0] <= now]:
                    self._cache.pop(key, None)
        return data


weather_service = WeatherService()


def _day_label(index: int, day_date: date) -> str:
    weekday = KHMER_WEEKDAYS[day_date.weekday()]
    if index == 0:
        return f"ថ្ងៃនេះ ({weekday})"
    if index == 1:
        return f"ស្អែក ({weekday})"
    return f"ថ្ងៃ{weekday}"


def _tip(day: Dict[str, Any]) -> str:
    code, rain, tmax = day["code"], day["rain"], day["tmax"]
    if code in _THUNDER_CODES:
        return "⚡ មានផ្គររន្ទះ សូមជៀសវាងដើរទីវាលចំហ និងនៅក្រោមដើមឈើធំៗ"
    if code in _RAIN_CODES or (rain is not None and rain >= 60):
        return "☔ ប្រហែលជាមានភ្លៀង សូមយកឆ័ត្រ ឬអាវភ្លៀងជាមួយ"
    if tmax is not None and tmax >= 36:
        return "🥵 ក្តៅខ្លាំង សូមផឹកទឹកឱ្យបានច្រើន និងជៀសវាងកម្តៅថ្ងៃត្រង់"
    if tmax is not None and tmax >= 33:
        return "☀️ ថ្ងៃក្តៅ កុំភ្លេចផឹកទឹក និងពាក់មួក"
    return "😊 អាកាសធាតុល្អ ដើរលេងបានស្រួល"


def format_forecast(slug: str, data: Dict[str, Any]) -> str:
    name_kh = PROVINCES[slug][0]
    current = data["current"]
    emoji, label = describe_code(current["code"])

    lines = [f"🌤️ អាកាសធាតុ · {name_kh}", "━━━━━━━━━━━━", f"ឥឡូវនេះ៖ {emoji} {label}"]
    temp_line = "🌡️ "
    temp_line += f"{current['temp']}°C" if current["temp"] is not None else "មិនមានទិន្នន័យ"
    if current["feels"] is not None:
        temp_line += f" (មានអារម្មណ៍ {current['feels']}°C)"
    lines.append(temp_line)
    extras = []
    if current["humidity"] is not None:
        extras.append(f"💧 សំណើម {current['humidity']}%")
    if current["wind"] is not None:
        extras.append(f"💨 ខ្យល់ {current['wind']} គម/ម៉ោង")
    if extras:
        lines.append(" · ".join(extras))

    lines += ["", f"📅 {len(data['days'])} ថ្ងៃខាងមុខ"]
    for index, day in enumerate(data["days"]):
        day_emoji, _ = describe_code(day["code"])
        tmin = f"{day['tmin']}°" if day["tmin"] is not None else "?"
        tmax = f"{day['tmax']}°" if day["tmax"] is not None else "?"
        rain = f" · ☔ {day['rain']}%" if day["rain"] is not None else ""
        lines.append(f"{_day_label(index, day['date'])} {day_emoji} {tmin}–{tmax}{rain}")

    lines += ["", _tip(data["days"][0]), "", "ប្រភព៖ Open-Meteo.com"]
    return "\n".join(lines)


def get_weather_text(slug: str) -> str:
    return format_forecast(slug, weather_service.fetch(slug))
