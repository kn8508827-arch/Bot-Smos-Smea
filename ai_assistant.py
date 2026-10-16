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

    async with _gemini_throttle_lock:
        elapsed = time.monotonic() - _gemini_last_request_at
        if elapsed < spacing:
            await asyncio.sleep(spacing - elapsed)
        _gemini_last_request_at = time.monotonic()


def _gemini_error_code(response: httpx.Response) -> str:
    """ទាញយកតែ error code ខ្លីៗសម្រាប់ log ដោយមិនបង្ហាញ response body ឬ secret។"""
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        error = {}

    if isinstance(error, dict):
        status = error.get("status")
        if isinstance(status, str) and status:
            return status[:64]
        code = error.get("code")
        if isinstance(code, str) and code:
            return code[:64]
        for detail in error.get("details", []):
            if isinstance(detail, dict):
                reason = detail.get("reason")
                if isinstance(reason, str) and reason:
                    return reason[:64]

    return f"HTTP_{response.status_code}"


def _retry_after_seconds(response: httpx.Response) -> Optional[float]:
    """អាន Retry-After header ឬ retryDelay របស់ Google API ប្រសិនបើមាន។"""
    retry_after = response.headers.get("Retry-After", "").strip()
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                return max(0.0, retry_at.timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                pass

    try:
        details = response.json().get("error", {}).get("details", [])
    except (ValueError, AttributeError):
        details = []

    for detail in details:
        if not isinstance(detail, dict):
            continue
        retry_delay = detail.get("retryDelay")
        if isinstance(retry_delay, (int, float)):
            return max(0.0, float(retry_delay))
        if isinstance(retry_delay, str):
            match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)s\s*", retry_delay)
            if match:
                return max(0.0, float(match.group(1)))
    return None


def _backoff_delay(attempt: int, response: Optional[httpx.Response] = None) -> float:
    base_delay = min(0.6 * (2.0 ** attempt), 2.5)
    server_delay = _retry_after_seconds(response) if response is not None else None
    delay = max(base_delay, server_delay or 0.0)
    return delay + random.uniform(0.05, 0.2)


async def generate_gemini_response(
    api_key: str,
    prompt: str,
    history: List[Dict[str, str]],
    user_name: Optional[str] = None,
    model_name: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None
) -> str:
    primary_model = model_name or getattr(config, "AI_MODEL", None) or DEFAULT_GEMINI_MODEL
    current_model = primary_model

    contents = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({
            "role": role,
            "parts": [{"text": msg["content"]}]
        })
    contents.append({
        "role": "user",
        "parts": [{"text": prompt}]
    })

    system_inst = get_system_instruction(user_name)
    payload = {
        "system_instruction": {
            "parts": [{"text": system_inst}]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1024
        }
    }

    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json"
    }

    http_client = client or await get_shared_http_client()

    for attempt in range(GEMINI_MAX_ATTEMPTS):
        await _wait_for_gemini_request_slot()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent"
        try:
            response = await http_client.post(url, json=payload, headers=headers)
        except httpx.TransportError:
            if attempt >= GEMINI_MAX_ATTEMPTS - 1:
                raise
            delay = _backoff_delay(attempt)
            await asyncio.sleep(delay)
            continue
        
        if response.status_code == 200:
            data = response.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "").strip()
            return "សុំទោស ខ្ញុំមិនអាចបង្កើតចម្លើយបានទេ។"
        
        # ប្តូរទៅកាន់ Fallback Model ប្រសិនបើជួបកំហុស server ឬ model រកមិនឃើញ
        if response.status_code in (404, 500, 502, 503, 504) and current_model != FALLBACK_GEMINI_MODEL:
            logger.warning(f"Gemini {current_model} failed with {response.status_code}. Switching to {FALLBACK_GEMINI_MODEL}...")
            current_model = FALLBACK_GEMINI_MODEL
            await asyncio.sleep(0.2)
            continue

        retryable_status = response.status_code in (408, 429, 500, 502, 503, 504)
        retry_after = _retry_after_seconds(response)
        retry_after_is_reasonable = (
            retry_after is None or retry_after <= GEMINI_MAX_RETRY_AFTER_SECONDS
        )
        if (
            retryable_status
            and attempt < GEMINI_MAX_ATTEMPTS - 1
            and retry_after_is_reasonable
        ):
            delay = _backoff_delay(attempt, response)
            await asyncio.sleep(delay)
            continue

        if response.status_code == 429:
            raise httpx.HTTPStatusError("Gemini Rate Limit Exceeded", request=response.request, response=response)
        elif response.status_code in (401, 403):
            raise PermissionError("Gemini API key is invalid or unauthorized")
        else:
            response.raise_for_status()
            return "មានបញ្ហាមិនប្រក្រតីក្នុងការទាញយកចម្លើយ។"


async def generate_openai_response(
    api_key: str,
    prompt: str,
    history: List[Dict[str, str]],
    user_name: Optional[str] = None,
    model_name: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None
) -> str:
    model = model_name or getattr(config, "AI_MODEL", None) or "gpt-4o-mini"
    url = "https://api.openai.com/v1/chat/completions"

    system_inst = get_system_instruction(user_name)
    messages_list = [{"role": "system", "content": system_inst}]
    for msg in history:
        role = "assistant" if msg["role"] == "assistant" else "user"
        messages_list.append({"role": role, "content": msg["content"]})
    messages_list.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages_list,
        "temperature": 0.7,
        "max_tokens": 1024
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    http_client = client or await get_shared_http_client()
    response = await http_client.post(url, json=payload, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "").strip()
        return "សុំទោស ខ្ញុំមិនអាចបង្កើតចម្លើយបានទេ។"
    elif response.status_code == 429:
        raise httpx.HTTPStatusError("OpenAI Rate Limit Exceeded", request=response.request, response=response)
    elif response.status_code in (401, 403):
        raise PermissionError("OpenAI API key is invalid or unauthorized")
    else:
        response.raise_for_status()
        return "មានបញ្ហាមិនប្រក្រតីក្នុងការទាញយកចម្លើយ។"


async def ask_ai(user_id: int, user_message: str, user_name: Optional[str] = None, db_path: Optional[str] = None) -> str:
    cleaned_prompt = user_message.strip()
    if not cleaned_prompt:
        return "សូមវាយសំណួរ ឬប្រធានបទដែលអ្នកចង់ឱ្យខ្ញុំជួយ!"

    gemini_key = getattr(config, "GEMINI_API_KEY", None)
    openai_key = getattr(config, "OPENAI_API_KEY", None)

    has_gemini = bool(gemini_key and gemini_key != "YOUR_GEMINI_API_KEY_HERE")
    has_openai = bool(openai_key and openai_key != "YOUR_OPENAI_API_KEY_HERE")

    if not has_gemini and not has_openai:
        return getattr(messages, "AI_NOT_CONFIGURED", "AI មិនទាន់ត្រូវបានកំណត់រចនាសម្ព័ន្ធនៅឡើយទេ។")

    allowed, seconds_left = check_rate_limit(user_id)
    if not allowed:
        cooldown_msg = getattr(messages, "AI_COOLDOWN", "សូមរង់ចាំ {seconds} វិនាទីសិន មុនពេលសួរបន្ត!")
        return cooldown_msg.format(seconds=seconds_left)

    daily_limit = getattr(config, "AI_DAILY_LIMIT_PER_USER", 20)
    daily_ok, current_count = database.check_and_increment_daily_usage(
        user_id=user_id,
        max_daily_limit=daily_limit,
        db_path=db_path
    )
    if not daily_ok:
        limit_msg = getattr(messages, "AI_DAILY_LIMIT_REACHED", "អ្នកបានប្រើប្រាស់កំណត់ត្រាប្រចាំថ្ងៃអស់ហើយ ({current_usage}/{max_limit})។")
        return limit_msg.format(
            current_usage=current_count,
            max_limit=daily_limit
        )

    if not user_name:
        with database.get_db(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT first_name FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row and row["first_name"]:
                user_name = row["first_name"]

    history = database.get_chat_history(user_id=user_id, limit=6, db_path=db_path)

    provider = getattr(config, "AI_PROVIDER", "auto")
    if provider == "auto":
        provider = "gemini" if has_gemini else "openai"

    try:
        if provider == "gemini" and has_gemini:
            ai_reply = await generate_gemini_response(
                api_key=gemini_key,
                prompt=cleaned_prompt,
                history=history,
                user_name=user_name
            )
        elif provider == "openai" and has_openai:
            ai_reply = await generate_openai_response(
                api_key=openai_key,
                prompt=cleaned_prompt,
                history=history,
                user_name=user_name
            )
        elif has_gemini:
            ai_reply = await generate_gemini_response(
                api_key=gemini_key,
                prompt=cleaned_prompt,
                history=history,
                user_name=user_name
            )
        elif has_openai:
            ai_reply = await generate_openai_response(
                api_key=openai_key,
                prompt=cleaned_prompt,
                history=history,
                user_name=user_name
            )
        else:
            return getattr(messages, "AI_NOT_CONFIGURED", "AI មិនទាន់ត្រូវបានកំណត់រចនាសម្ព័ន្ធនៅឡើយទេ។")

        database.add_chat_message(user_id=user_id, role="user", content=cleaned_prompt, db_path=db_path)
        database.add_chat_message(user_id=user_id, role="assistant", content=ai_reply, db_path=db_path)

        return ai_reply

    except httpx.TimeoutException:
        return getattr(messages, "AI_TIMEOUT_ERROR", "ប្រព័ន្ធឆ្លើយតបយឺតពេក សូមព្យាយាមម្តងទៀតនៅពេលបន្តិចទៀត។")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            return getattr(messages, "AI_RATE_LIMIT_ERROR", "សំណើច្រើនពេក សូមរង់ចាំមួយភ្លែត។")
        return getattr(messages, "AI_GENERIC_ERROR", "មានបញ្ហាបច្ចេកទេសក្នុងការទាញយកចម្លើយ។")
    except PermissionError:
        return getattr(messages, "AI_AUTH_ERROR", "API Key មិនត្រឹមត្រូវ ឬគ្មានសិទ្ធិប្រើប្រាស់។")
    except Exception as e:
        logger.error("Unexpected AI error for user %s: %s", user_id, e)
        return getattr(messages, "AI_GENERIC_ERROR", "មានបញ្ហាបច្ចេកទេសក្នុងការទាញយកចម្លើយ។")
