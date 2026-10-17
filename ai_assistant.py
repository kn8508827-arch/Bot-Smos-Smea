"""
ម៉ូឌុល AI Conversational Assistant សម្រាប់ Team ស្មោះស្នេហ៍ (Production-safe upgrade)
គាំទ្រ Google Gemini (gemini-2.0-flash / fallback gemini-1.5-flash)
"""
import asyncio
import time
import random
import logging
import re
import base64
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional, Tuple, Any
import httpx

import config
import database
import messages

logger = logging.getLogger(__name__)

# In-memory Rate Limiting
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
    """ទទួលបាន ឬបង្កើត httpx.AsyncClient Connection Pool សម្រាប់ការតភ្ជាប់លឿន"""
    global _shared_client, _shared_client_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if (
        _shared_client is None
        or getattr(_shared_client, "is_closed", False)
        or _shared_client_loop != current_loop
    ):
        if _shared_client is not None and not getattr(_shared_client, "is_closed", False):
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
    if _shared_client is not None and not getattr(_shared_client, "is_closed", False):
        try:
            await _shared_client.aclose()
        except Exception:
            pass
        _shared_client = None
        _shared_client_loop = None


def get_system_instruction(user_name: Optional[str] = None) -> str:
    """Safer bilingual system instruction: prefer Khmer, but respond in English when user writes English.
    Do not fabricate facts; ask concise clarifying questions when needed.
    """
    display_name = user_name if user_name else "មិត្ត"
    return (
        f"You are a helpful, polite AI assistant for Team ស្មោះស្នេហ៍. You speak natural Khmer by default and reply in English when the user writes in English. "
        f"Address the user respectfully as {display_name} when appropriate.\n\n"
        "Do NOT invent facts, names, or claims about real people or system state. If you are unsure, say you don't know and offer to help find out. Ask short clarifying questions when essential information is missing. Keep replies concise and useful."
    )


def normalize_text(text: str) -> str:
    """Conservative text normalization:
    - Collapse repeated whitespace
    - Preserve URLs and code-like tokens
    - Trim surrounding whitespace
    """
    if not text:
        return text
    text = text.strip()

    # Preserve URLs by temporarily replacing them
    urls = {}
    def _url_repl(m):
        key = f"__URL_{len(urls)}__"
        urls[key] = m.group(0)
        return key
    text = re.sub(r"https?://\S+", _url_repl, text)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)

    # Restore URLs
    for k, v in urls.items():
        text = text.replace(k, v)
    return text


def check_rate_limit(user_id: int) -> Tuple[bool, int]:
    now = time.monotonic()
    last_time = _user_last_query_time.get(user_id, 0.0)
    cooldown = getattr(config, "AI_RATE_LIMIT_SECONDS", 3)

    if now - last_time < cooldown:
        remaining = int(cooldown - (now - last_time)) + 1
        return False, remaining

    _user_last_query_time[user_id] = now
    return True, 0


async def _wait_for_gemini_request_slot() -> None:
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
    """ការពារកុំឱ្យ Telegram Parse Error ដោយសារសញ្ញា Markdown មិនពេញលេញ"""
    if not text:
        return ""
    if text.count("*") % 2 != 0:
        text = text.replace("*", "")
    if text.count("_") % 2 != 0:
        text = text.replace("_", "")
    return text.strip()


def build_context(user_id: int, history: List[Dict[str, str]], db_path: Optional[str] = None) -> List[Dict[str, str]]:
    """Build a bounded context for the model. Uses recent messages and optional summary.
    Returns a list of messages in the format expected by generate_gemini_response.
    """
    # Normalize and limit messages
    msg_limit = getattr(config, "AI_CONTEXT_MESSAGE_LIMIT", 8)
    char_limit = getattr(config, "AI_CONTEXT_CHAR_LIMIT", 4000)

    # Optionally include a stored summary (disabled by default)
    include_summary = getattr(config, "AI_SUMMARY_ENABLED", False)
    context: List[Dict[str, str]] = []

    if include_summary:
        try:
            summary = database.get_conversation_summary(user_id, db_path=db_path)
            if summary:
                context.append({"role": "system", "content": f"Conversation summary: {summary}"})
        except Exception as e:
            logger.debug(f"Could not load summary for user {user_id}: {e}")

    # Take the most recent messages (history is already chronological)
    recent = history[-msg_limit:]
    total_chars = 0
    for msg in recent:
        content = normalize_text(msg.get("content", ""))
        if total_chars + len(content) > char_limit:
            # truncate conservatively
            content = content[: max(200, char_limit - total_chars)]
        total_chars += len(content)
        role = "user" if msg.get("role") == "user" else "model"
        context.append({"role": role, "content": content})
        if total_chars >= char_limit:
            break
    return context


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

    # Build contents using safe context builder
    contents = []
    try:
        built = build_context(user_id=0, history=history)  # user_id optional here
        for m in built:
            role = "user" if m["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m["content"][:1500]}]})
    except Exception:
        # fallback: use last few messages raw
        trimmed_history = history[-6:]
        for msg in trimmed_history:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"][:1500]}]})

    contents.append({"role": "user", "parts": [{"text": prompt}]})

    system_inst = get_system_instruction(user_name)
    payload = {
        "system_instruction": {"parts": [{"text": system_inst}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1024,
            "topP": 0.95
        }
    }

    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json"
    }

    http_client = client or await get_shared_http_client()

    last_exc: Optional[Exception] = None
    for attempt in range(GEMINI_MAX_ATTEMPTS):
        await _wait_for_gemini_request_slot()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent"
        try:
            response = await http_client.post(url, json=payload, headers=headers)
        except httpx.TransportError as te:
            last_exc = te
            if attempt >= GEMINI_MAX_ATTEMPTS - 1:
                raise
            delay = _backoff_delay(attempt)
            await asyncio.sleep(delay)
            continue
        except Exception as e:
            last_exc = e
            if attempt >= GEMINI_MAX_ATTEMPTS - 1:
                raise
            await asyncio.sleep(_backoff_delay(attempt))
            continue

        if response.status_code == 200:
            try:
                data = response.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        raw_text = parts[0].get("text", "").strip()
                        return clean_markdown_for_telegram(raw_text)
                return messages.AI_GENERIC_ERROR
            except Exception:
                return messages.AI_GENERIC_ERROR

        # switch to fallback model for server errors
        if response.status_code in (404, 500, 502, 503, 504) and current_model != FALLBACK_GEMINI_MODEL:
            logger.warning(f"Gemini {current_model} error {response.status_code}. Switching to {FALLBACK_GEMINI_MODEL}...")
            current_model = FALLBACK_GEMINI_MODEL
            await asyncio.sleep(0.2)
            continue

        if attempt < GEMINI_MAX_ATTEMPTS - 1 and response.status_code in (408, 429, 500, 502, 503, 504):
            delay = _backoff_delay(attempt, response)
            await asyncio.sleep(delay)
            continue

        if response.status_code == 429:
            raise httpx.HTTPStatusError("Rate Limit", request=response.request, response=response)
        elif response.status_code in (401, 403):
            raise PermissionError("Invalid Key")
        else:
            try:
                response.raise_for_status()
            except Exception as e:
                logger.debug(f"Gemini unexpected response: {getattr(response, 'text', '')}")
                raise

    if last_exc:
        raise last_exc
    return messages.AI_GENERIC_ERROR


async def ask_ai(user_id: int, user_message: str, user_name: Optional[str] = None, db_path: Optional[str] = None) -> str:
    cleaned_prompt = normalize_text(user_message or "")
    if not cleaned_prompt:
        return "សូមវាយសំណួរ ឬប្រធានបទដែលអ្នកចង់ឱ្យខ្ញុំជួយ!"

    gemini_key = getattr(config, "GEMINI_API_KEY", "")
    if not gemini_key:
        return getattr(messages, "AI_NOT_CONFIGURED", "AI មិនទាន់កំណត់ API Key នៅឡើយទេ។")

    allowed, seconds_left = check_rate_limit(user_id)
    if not allowed:
        return getattr(messages, "AI_COOLDOWN", "សូមរង់ចាំ {seconds} វិនាទីសិន!").format(seconds=seconds_left)

    daily_limit = getattr(config, "AI_DAILY_LIMIT_PER_USER", 50)
    daily_ok, current_count = database.check_and_increment_daily_usage(
        user_id=user_id,
        max_daily_limit=daily_limit,
        db_path=db_path
    )
    if not daily_ok:
        return getattr(messages, "AI_DAILY_LIMIT_REACHED", "អ្នកប្រើអស់កូតាថ្ងៃនេះហើយ ({current_usage}/{max_limit})។").format(
            current_usage=current_count,
            max_limit=daily_limit
        )

    if not user_name:
        try:
            with database.get_db(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT first_name FROM users WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
                if row and row["first_name"]:
                    user_name = row["first_name"]
        except Exception:
            user_name = user_name

    history = []
    try:
        history = database.get_chat_history(user_id=user_id, limit=getattr(config, "AI_CONTEXT_MESSAGE_LIMIT", 6), db_path=db_path)
    except Exception:
        history = []

    try:
        ai_reply = await generate_gemini_response(
            api_key=gemini_key,
            prompt=cleaned_prompt,
            history=history,
            user_name=user_name
        )

        try:
            database.add_chat_message(user_id=user_id, role="user", content=cleaned_prompt, db_path=db_path)
            database.add_chat_message(user_id=user_id, role="assistant", content=ai_reply, db_path=db_path)
        except Exception:
            logger.debug("Could not write chat messages to DB; continuing without persisting")

        return ai_reply

    except httpx.TimeoutException:
        return getattr(messages, "AI_TIMEOUT_ERROR", "ប្រព័ន្ធឆ្លើយតបយឺតពេក (Timeout)។")
    except httpx.HTTPStatusError as e:
        try:
            if e.response.status_code == 429:
                return getattr(messages, "AI_RATE_LIMIT_ERROR", "សំណើច្រើនពេក សូមរង់ចាំមួយភ្លែត។")
        except Exception:
            pass
        return getattr(messages, "AI_GENERIC_ERROR", "មានបញ្ហាបច្ចេកទេស។")
    except PermissionError:
        return getattr(messages, "AI_AUTH_ERROR", "API Key មិនត្រឹមត្រូវ។")
    except Exception as e:
        logger.error(f"Error AI: {e}")
        return getattr(messages, "AI_GENERIC_ERROR", "មានបញ្ហាបច្ចេកទេស។")


async def ask_ai_with_audio(user_id: int, audio_bytes: bytes, mime_type: str = "audio/ogg", user_name: Optional[str] = None, db_path: Optional[str] = None) -> str:
    """បញ្ជូនសំឡេង Voice Note ទៅឱ្យ Gemini 2.0 Flash ស្តាប់ផ្ទាល់ (Multimodal)"""
    gemini_key = getattr(config, "GEMINI_API_KEY", "")
    if not gemini_key:
        return getattr(messages, "AI_NOT_CONFIGURED", "AI មិនទាន់កំណត់ API Key នៅឡើយទេ។")

    allowed, seconds_left = check_rate_limit(user_id)
    if not allowed:
        return getattr(messages, "AI_COOLDOWN", "សូមរង់ចាំ {seconds} វិនាទីសិន!").format(seconds=seconds_left)

    daily_limit = getattr(config, "AI_DAILY_LIMIT_PER_USER", 50)
    daily_ok, current_count = database.check_and_increment_daily_usage(user_id, daily_limit, db_path)
    if not daily_ok:
        return getattr(messages, "AI_DAILY_LIMIT_REACHED", "អ្នកប្រើអស់កូតាថ្ងៃនេះហើយ ({current_usage}/{max_limit})។").format(
            current_usage=current_count,
            max_limit=daily_limit
        )

    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    system_inst = get_system_instruction(user_name)

    payload = {
        "system_instruction": {"parts": [{"text": system_inst}]},
        "contents": [{
            "role": "user",
            "parts": [
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": audio_b64
                    }
                },
                {
                    "text": "សូមស្តាប់សារសំឡេងនេះ ហើយឆ្លើយតបទៅកាន់សំណួរ ឬការសន្ទនា" 
                }
            ]
        }],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1024}
    }

    headers = {"x-goog-api-key": gemini_key, "Content-Type": "application/json"}
    http_client = await get_shared_http_client()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_GEMINI_MODEL}:generateContent"

    try:
        response = await http_client.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            data = response.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    reply = clean_markdown_for_telegram(parts[0].get("text", "").strip())
                    try:
                        database.add_chat_message(user_id, "user", "[Voice Message]", db_path)
                        database.add_chat_message(user_id, "assistant", reply, db_path)
                    except Exception:
                        logger.debug("Could not persist voice message to DB")
                    return reply
            return messages.AI_GENERIC_ERROR

        logger.warning(f"Audio processing status code: {response.status_code}")
        return messages.AI_GENERIC_ERROR
    except Exception as e:
        logger.error(f"Error processing voice message: {e}")
        return getattr(messages, "AI_GENERIC_ERROR", "មានបញ្ហាក្នុងការស្តាប់សារសំឡេង។")
