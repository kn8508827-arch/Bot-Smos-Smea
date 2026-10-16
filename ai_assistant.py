"""
ម៉ូឌុល AI Conversational Assistant សម្រាប់ Team ស្មោះស្នេហ៍ (Luxury & Production Grade)
គាំទ្រ Google Gemini (gemini-2.0-flash / fallback gemini-1.5-flash) និង OpenAI
"""
import asyncio
import time
import random
import logging
import re
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional, Tuple, Any
import httpx

import config
import database
import messages

logger = logging.getLogger(__name__)

# កត់ត្រាពេលវេលាសំណួរចុងក្រោយរបស់ User (In-memory Rate Limiting)
_user_last_query_time: Dict[int, float] = {}
_gemini_throttle_lock = asyncio.Lock()
_gemini_last_request_at = 0.0

DEFAULT_GEMINI_MODEL = getattr(config, "AI_MODEL", None) or "gemini-2.0-flash"
FALLBACK_GEMINI_MODEL = "gemini-1.5-flash"
GEMINI_MAX_ATTEMPTS = 3
GEMINI_MAX_RETRY_AFTER_SECONDS = 15.0

_shared_client: Optional[httpx.AsyncClient] = None
_shared_client_loop: Optional[asyncio.AbstractEventLoop] = None


async def get_shared_http_client() -> httpx.AsyncClient:
    """ទទួលបាន ឬបង្កើត httpx.AsyncClient ដែលចែករំលែកសម្រាប់ការតភ្ជាប់លឿន (Connection Pooling & Keep-Alive)"""
    global _shared_client, _shared_client_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if (
        _shared_client is None
        or _shared_client.is_closed
        or _shared_client_loop != current_loop
    ):
        if _shared_client is not None and not _shared_client.is_closed:
            try:
                await _shared_client.aclose()
            except Exception:
                pass
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(25.0, connect=8.0),
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=20,
                keepalive_expiry=60.0
            )
        )
        _shared_client_loop = current_loop
    return _shared_client


async def close_shared_http_client() -> None:
    """បិទ Shared HTTP Client ពេល bot បិទ ឬ reset"""
    global _shared_client, _shared_client_loop
    if _shared_client is not None and not _shared_client.is_closed:
        try:
            await _shared_client.aclose()
        except Exception:
            pass
        _shared_client = None
        _shared_client_loop = None


def get_system_instruction(user_name: Optional[str] = None) -> str:
    display_name = user_name if user_name else "មិត្តភក្តិដ៏មានតម្លៃ"
    return (
        f"You are Cooler Bot, the elite, highly sophisticated, and official AI Assistant of 'Team ស្មោះស្នេហ៍' (ក្រុម Team ស្មោះស្នេហ៍).\n"
        f"This prestigious Cambodian community is anchored in solidarity, deep loyalty, mutual prosperity, and aristocratic warmth. "
        f"You are currently conversing with {display_name}.\n\n"
        
        "⚜️ SPECIAL IDENTITY & HONORIFIC PROTOCOL:\n"
        "- The esteemed founder, visionary, and creator of Team ស្មោះស្នេហ៍ is Chan Taravid Net (ប៉ា).\n"
        "- IF the user chatting is identified as Chan Taravid Net, or explicitly introduces themselves as Papa (ប៉ា), "
        "address them with utmost reverence, filial respect, and warmth as 'Papa' (ប៉ា) or 'លោកប៉ា'.\n"
        "- For all other esteemed members, VIP guests, and visitors, address them with refined elegance, warmth, and respect using their name "
        "(e.g., 'លោក/អ្នកនាង/បង/មិត្តភក្តិ {display_name}'). NEVER refer to regular users or women as 'Papa' or 'ប៉ា'.\n\n"
        
        "👑 ARISTOCRATIC TONE & COMMUNICATION STANDARDS:\n"
        "1. Identity: When asked who you are, introduce yourself proudly and elegantly as 'Cooler Bot ជំនួយការ AI វៃឆ្លាតផ្លូវការប្រចាំ Team ស្មោះស្នេហ៍'.\n"
        "2. Language & Elegance: Default to polite, articulate, culturally respectful, and natural Khmer (ភាសាខ្មែរ). "
        "If addressed in English or another language, reply with impeccable professional fluency in that language.\n"
        "3. High-Class Hospitality: Maintain an executive, inspiring, polished, and encouraging tone. Reflect high status and prestige without being arrogant.\n"
        "4. Intellectual Mastery: Deliver top-tier insights on business, technology, creative writing, productivity, culture, science, and life wisdom.\n"
        "5. Telegram Formatting: Structure replies cleanly for mobile screens using elegant emojis (✨, ⚜️, 👑, 💎), clear paragraphs, bold highlights, and neat bullet points. Avoid cluttered walls of text.\n"
        "6. Brevity & Depth: Keep everyday answers concise, crisp, and direct. When deep analysis or long answers are sought, deliver comprehensive masterclasses.\n"
        "7. AI Photo Studio Guidance: If users inquire about transforming portraits, editing photos, or creating high-fashion avatars, "
        "warmly guide them to send a clear portrait photo directly into this chat to activate the AI Photo Studio 9 Luxury Styles menu."
    )


def check_rate_limit(user_id: int) -> Tuple[bool, int]:
    """ពិនិត្យមើលថាតើ User សួរញឹកញាប់ពេកដែរឬទេ (Cooldown)"""
    now = time.monotonic()
    last_time = _user_last_query_time.get(user_id, 0.0)
    cooldown = getattr(config, "AI_RATE_LIMIT_SECONDS", 3)
    
    if now - last_time < cooldown:
        remaining = int(cooldown - (now - last_time)) + 1
        return False, remaining
    
    _user_last_query_time[user_id] = now
    return True, 0


async def _wait_for_gemini_request_slot() -> None:
    """កំណត់ចន្លោះសំណើ Gemini ទូទាំង User ទាំងអស់ក្នុង process មួយ"""
    global _gemini_last_request_at
    spacing = getattr(config, "AI_GLOBAL_RATE_LIMIT_SECONDS", 0.5)
    if spacing <= 0:
        return

    async with _gemini_throttle_lock:
        elapsed = time.monotonic() - _gemini_last_request_at
        if elapsed < spacing:
            await asyncio.sleep(spacing - elapsed)
        _gemini_last_request_at = time.monotonic()


def _retry_after_seconds(response: httpx.Response) -> Optional[float]:
    retry_after = response.headers.get("Retry-After", "").strip()
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                return max(0.0, retry_at.timestamp() - time.time())
            except Exception:
                pass
    return None


def _backoff_delay(attempt: int, response: Optional[httpx.Response] = None) -> float:
    base_delay = min(0.6 * (2.0 ** attempt), 2.5)
    server_delay = _retry_after_seconds(response) if response is not None else None
    delay = max(base_delay, server_delay or 0.0)
    return delay + random.uniform(0.05, 0.2)


def clean_markdown_for_telegram(text: str) -> str:
    """ការពារកុំឱ្យ Telegram
