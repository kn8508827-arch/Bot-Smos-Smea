"""
ម៉ូឌុល AI Conversational Assistant សម្រាប់ Team ស្មោះស្នេហ៍
គាំទ្រ Google Gemini និង OpenAI ជាមួយប្រព័ន្ធ Context Memory និង Cost Control
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

# គំរូ Gemini លំនាំដើម និងគំរូជំនួស (Fast & Free Tier compatible)
DEFAULT_GEMINI_MODEL = getattr(config, "AI_MODEL", None) or "gemini-2.0-flash"
FALLBACK_GEMINI_MODEL = "gemini-1.5-flash"
GEMINI_MAX_ATTEMPTS = 3
GEMINI_MAX_RETRY_AFTER_SECONDS = 15.0

# Persistent HTTP Client Connection Pool សម្រាប់ reuse TCP/TLS keep-alive
_shared_client: Optional[httpx.AsyncClient] = None
_shared_client_loop: Optional[asyncio.AbstractEventLoop] = None
_client_lock = asyncio.Lock()


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
            timeout=httpx.Timeout(30.0, connect=10.0),
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
    display_name = user_name if user_name else "មិត្តភក្តិ"
    return (
        f"You are Cooler Bot, the official AI Assistant of 'Team ស្មោះស្នេហ៍' (ក្រុម Team ស្មោះស្នេហ៍), "
        f"a Cambodian community founded on love, solidarity, friendship, and mutual sharing. You are currently chatting with {display_name}.\n"
        "Special Identity & Addressing Rule:\n"
        "- The main creator and founder of this bot is Chan Taravid Net (ប៉ា). If the user chatting is Chan Taravid Net or if specifically addressed as Papa, acknowledge and address them respectfully as Papa (ប៉ា).\n"
        "- For all other general members, guests, or visitors chatting with you, address them politely and naturally by their actual name. DO NOT call regular guests or women 'Papa' or 'ប៉ា'. Treat everyone with polite, professional, and welcoming Khmer hospitality.\n"
        "Guidelines:\n"
        "1. When asked what your name is, always proudly introduce yourself as Cooler Bot (ជំនួយការ Cooler Bot) របស់ Team ស្មោះស្នេហ៍.\n"
        "2. Address the user politely and naturally by their name.\n"
        "3. Always respond in polite, warm, and natural Khmer (ភាសាខ្មែរ) by default.\n"
        "4. If the user asks in English or requests another language, respond accurately in that language.\n"
        "5. Assist members with general knowledge, writing, translation, science, technology, summaries, problem solving, and daily questions.\n"
        "6. Maintain a supportive, respectful, and encouraging tone representing Team ស្មោះស្នេហ៍.\n"
        "7. Format responses cleanly for Telegram (using clear paragraphs and bullet points where helpful).\n"
        "8. Keep answers concise unless a detailed explanation is explicitly requested.\n"
        "9. When members ask how to create, edit, generate, or transform photos with AI (AI Photo Studio), explain warmly and clearly in Khmer that they should upload/send a photo directly to this Telegram bot, and then select one of the available styles from the interactive menu."
    )


def check_rate_limit(user_id: int) -> Tuple[bool, int]:
    """
    ពិនិត្យមើលថាតើ User សួរញឹកញាប់ពេកដែរឬទេ (Cooldown)
    ត្រឡប់៖ (is_allowed, seconds_left_to_wait)
    """
    now = time.monotonic()
    last_time = _user_last_query_time.get(user_id, 0.0)
    cooldown = getattr(config, "AI_RATE_LIMIT_SECONDS", 3)
    
    if now - last_time < cooldown:
        remaining = int(cooldown - (now - last_time)) + 1
        return False, remaining
    
    _user_last_query_time[user_id] = now
    return True, 0


async def _wait_for_gemini_request_slot() -> None:
    """កំណត់ចន្លោះសំណើ Gemini ទូទាំង User ទាំងអស់ក្នុង process មួយ។"""
    global _gemini_last_request_at

    spacing = getattr(config, "AI_GLOBAL_RATE_LIMIT_SECONDS", 0.5)
    if spacing <= 0:
        return

    async with _gemini_throttle_lock
