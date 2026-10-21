"""Cambodia-time daily-care delivery for opted-in Telegram users."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import ai_assistant
import async_runner
import config
import database
import messages

logger = logging.getLogger(__name__)
CAMBODIA_TIMEZONE = ZoneInfo("Asia/Phnom_Penh")


@dataclass(frozen=True)
class CareSlot:
    category: str
    hour: int
    minute: int
    label: str
    prompt: str


CARE_SLOTS = (
    CareSlot("good_morning", 7, 0, "morning", "a warm good-morning check-in"),
    CareSlot("lunch_care", 12, 0, "lunch", "a friendly lunch and hydration reminder"),
    CareSlot("evening_checkin", 17, 30, "evening", "a gentle evening wellbeing check-in"),
    CareSlot("dinner_care", 19, 0, "dinner", "a caring dinner reminder"),
    CareSlot("rest_reminder", 21, 0, "rest", "a short reminder to rest and relax"),
    CareSlot("good_night", 22, 0, "night", "a kind good-night message"),
)
CARE_SLOTS_BY_CATEGORY = {slot.category: slot for slot in CARE_SLOTS}

_scheduler_guard = threading.Lock()
_scheduler_instance: Optional[BackgroundScheduler] = None
_scheduler_lock_owner: Optional[str] = None


def is_persistent_storage_ready(
    db_path: Optional[str] = None,
    environ: Optional[Dict[str, str]] = None,
) -> bool:
    """Allow local development; on Railway require SQLite to live on its volume."""
    env = os.environ if environ is None else environ
    is_railway = any(
        env.get(name)
        for name in ("RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID", "RAILWAY_ENVIRONMENT")
    )
    if not is_railway:
        return True

    mount_path = env.get("RAILWAY_VOLUME_MOUNT_PATH")
    if not mount_path:
        return False

    configured_db_path = db_path or database.DB_PATH
    absolute_db_path = os.path.abspath(configured_db_path)
    absolute_mount_path = os.path.abspath(mount_path)
    try:
        return os.path.normcase(os.path.commonpath((absolute_db_path, absolute_mount_path))) == os.path.normcase(absolute_mount_path)
    except (OSError, ValueError):
        return False


def _local_date(now: Optional[datetime] = None) -> str:
    current = now or datetime.now(CAMBODIA_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CAMBODIA_TIMEZONE)
    return current.astimezone(CAMBODIA_TIMEZONE).date().isoformat()


def generate_care_message(category: str) -> str:
    """Generate one fresh Khmer message for the slot, with a safe local fallback."""
    slot = CARE_SLOTS_BY_CATEGORY[category]
    api_key = getattr(config, "GEMINI_API_KEY", "")
    try:
        if not api_key:
            raise RuntimeError("Gemini is not configured")
        prompt = (
            "Write one original, warm, concise Khmer Telegram message for a community member. "
            f"The message should be {slot.prompt}. Use one or two short sentences, one suitable emoji, "
            "and natural Khmer. Do not include a name, private details, markdown, links, or a claim "
            "that you know the recipient personally. Return only the message."
        )
        response = async_runner.run_sync(
            ai_assistant.generate_gemini_response(
                api_key=api_key,
                prompt=prompt,
                history=[],
                user_name=None,
                user_id=None,
                db_path=database.DB_PATH,
            )
        )
        if response and response.strip():
            return response.strip()
    except Exception as exc:
        logger.warning(
            "Daily-care message generation failed for %s (%s); using fallback",
            category,
            type(exc).__name__,
        )
    return messages.DAILYCARE_FALLBACK_MESSAGES[category]


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    value = getattr(exc, "retry_after", None)
    if value is None:
        result_json = getattr(exc, "result_json", None)
        if isinstance(result_json, dict):
            parameters = result_json.get("parameters") or {}
            value = parameters.get("retry_after")
    try:
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _error_code(exc: Exception) -> Optional[int]:
    try:
        value = getattr(exc, "error_code", None)
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_permanent_chat_error(exc: Exception) -> bool:
    code = _error_code(exc)
    message_text = str(exc).lower()
    return code == 403 or (
        code == 400
        and any(
            phrase in message_text
            for phrase in ("chat not found", "user is deactivated", "bot was blocked")
        )
    )


def _safe_error_name(exc: Exception) -> str:
    code = _error_code(exc)
    return f"{type(exc).__name__}:{code}" if code is not None else type(exc).__name__


def send_dailycare_slot(
    bot: Any,
    category: str,
    now: Optional[datetime] = None,
    db_path: Optional[str] = None,
    message_generator: Optional[Callable[[str], str]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    rate_limit_seconds: Optional[float] = None,
) -> Dict[str, int]:
    """Generate once per slot and send at most once per opted-in user/day/slot."""
    if category not in CARE_SLOTS_BY_CATEGORY:
        raise ValueError(f"Unknown daily-care category: {category}")
    if not database.get_dailycare_enabled(db_path):
        return {"disabled": 1}

    batch_size = max(1, min(int(getattr(config, "DAILYCARE_BATCH_SIZE", 50)), 1000))
    first_batch = database.get_eligible_notification_recipient_batch(
        after_user_id=0, limit=batch_size, db_path=db_path
    )
    if not first_batch:
        return {"eligible": 0}

    generator = message_generator or generate_care_message
    text = generator(category)
    if not text or not text.strip():
        text = messages.DAILYCARE_FALLBACK_MESSAGES[category]

    local_date = _local_date(now)
    counts: Dict[str, int] = {
        "eligible": 0, "sent": 0, "failed": 0, "blocked": 0,
        "duplicate": 0, "disabled": 0,
    }

    max_attempts = max(1, int(getattr(config, "DAILYCARE_MAX_SEND_ATTEMPTS", 3)))
    max_retry_wait = max(0.0, float(getattr(config, "DAILYCARE_RETRY_MAX_WAIT_SECONDS", 30.0)))
    rate_delay = max(0.0, float(
        getattr(config, "DAILYCARE_RATE_LIMIT_SECONDS", 1.0)
        if rate_limit_seconds is None
        else rate_limit_seconds
    ))

    last_user_id = 0
    send_sequences_started = 0
    while True:
        recipients = first_batch if last_user_id == 0 else database.get_eligible_notification_recipient_batch(
            after_user_id=last_user_id, limit=batch_size, db_path=db_path
        )
        if not recipients:
            break
        counts["eligible"] += len(recipients)

        for recipient in recipients:
            last_user_id = recipient["user_id"]
            if not database.is_eligible_notification_recipient(recipient["user_id"], db_path):
                counts["disabled"] += 1
                continue

            did_claim, delivery_key = database.claim_notification_delivery(
                user_id=recipient["user_id"],
                chat_id=recipient["chat_id"],
                category=category,
                local_date=local_date,
                db_path=db_path,
            )
            if not did_claim:
                counts["duplicate"] += 1
                continue

            if send_sequences_started and rate_delay:
                sleep_fn(rate_delay)
            send_sequences_started += 1
            attempts = 0
            status = "failed"
            last_error: Optional[str] = None

            while attempts < max_attempts:
                attempts += 1
                try:
                    bot.send_message(recipient["chat_id"], text)
                    status = "sent"
                    last_error = None
                    break
                except Exception as exc:
                    last_error = _safe_error_name(exc)
                    if _is_permanent_chat_error(exc):
                        database.mark_user_inactive(recipient["user_id"], db_path)
                        database.set_notification_enabled(recipient["user_id"], False, db_path)
                        status = "blocked"
                        break

                    retry_after = _retry_after_seconds(exc)
                    if retry_after is not None and attempts < max_attempts and retry_after <= max_retry_wait:
                        database.update_notification_delivery(
                            delivery_key, "pending", attempts, last_error, db_path
                        )
                        sleep_fn(retry_after)
                        continue

                    # Unknown transport failures may have reached Telegram. Do not retry
                    # them automatically because a retry could duplicate a delivered DM.
                    status = "failed"
                    logger.warning(
                        "Daily-care send failed for user %s in %s (%s)",
                        recipient["user_id"], category, last_error,
                    )
                    break

            database.update_notification_delivery(
                delivery_key, status, attempts, last_error, db_path
            )
            counts[status] = counts.get(status, 0) + 1

    logger.info(
        "Daily-care slot %s finished: eligible=%s sent=%s failed=%s blocked=%s duplicate=%s disabled=%s",
        category, counts["eligible"], counts["sent"], counts["failed"], counts["blocked"],
        counts["duplicate"], counts["disabled"],
    )
    return counts


def _run_scheduled_slot(bot: Any, category: str) -> None:
    try:
        send_dailycare_slot(bot, category)
    except Exception:
        logger.exception("Daily-care scheduled job failed for %s", category)


def register_dailycare_jobs(scheduler: Any, bot: Any) -> None:
    for slot in CARE_SLOTS:
        scheduler.add_job(
            _run_scheduled_slot,
            trigger=CronTrigger(
                hour=slot.hour,
                minute=slot.minute,
                timezone=CAMBODIA_TIMEZONE,
            ),
            args=(bot, slot.category),
            id=f"dailycare_{slot.category}",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,
        )


def _heartbeat_scheduler_lease(scheduler: Any) -> None:
    owner_id = _scheduler_lock_owner
    if owner_id is None:
        return
    ttl_seconds = int(getattr(config, "DAILYCARE_LOCK_TTL_SECONDS", 90))
    if database.heartbeat_scheduler_lock(owner_id, ttl_seconds=ttl_seconds):
        return
    logger.error("Daily-care scheduler lease was lost; stopping this scheduler")
    shutdown_scheduler(scheduler)


_scheduler_retry_thread: Optional[threading.Thread] = None
_scheduler_retry_stop = threading.Event()
_SCHEDULER_LOCK_RETRY_INTERVAL_SECONDS = 5


def _start_scheduler_with_lease(bot: Any, owner_id: str) -> Optional[BackgroundScheduler]:
    """Start the scheduled jobs after the caller acquires the database lease."""
    global _scheduler_instance, _scheduler_lock_owner
    ttl_seconds = int(getattr(config, "DAILYCARE_LOCK_TTL_SECONDS", 90))
    scheduler = BackgroundScheduler(timezone=CAMBODIA_TIMEZONE, daemon=True)
    try:
        register_dailycare_jobs(scheduler, bot)
        scheduler.add_job(
            _heartbeat_scheduler_lease,
            trigger="interval",
            seconds=max(5, ttl_seconds // 3),
            args=(scheduler,),
            id="dailycare_scheduler_lease_heartbeat",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()
        _scheduler_lock_owner = owner_id
        _scheduler_instance = scheduler
        logger.info("Daily-care scheduler started for Asia/Phnom_Penh")
        return scheduler
    except Exception:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        database.release_scheduler_lock(owner_id)
        logger.exception("Could not start Daily-care scheduler")
        return None


def _retry_start_scheduler(bot: Any) -> None:
    """Retry lease acquisition in the background during overlapping deployments."""
    global _scheduler_retry_thread
    ttl_seconds = int(getattr(config, "DAILYCARE_LOCK_TTL_SECONDS", 90))
    while not _scheduler_retry_stop.wait(_SCHEDULER_LOCK_RETRY_INTERVAL_SECONDS):
        with _scheduler_guard:
            if _scheduler_retry_stop.is_set():
                _scheduler_retry_thread = None
                return
            if _scheduler_instance is not None and _scheduler_instance.running:
                _scheduler_retry_thread = None
                return
            owner_id = f"{os.getpid()}:{uuid.uuid4().hex}"
            if not database.acquire_scheduler_lock(owner_id, ttl_seconds=ttl_seconds):
                continue
            _scheduler_retry_thread = None
            _start_scheduler_with_lease(bot, owner_id)
            return
    with _scheduler_guard:
        _scheduler_retry_thread = None


def start_scheduler(bot: Any) -> Optional[BackgroundScheduler]:
    """Start one leased in-process scheduler; retry asynchronously if a deployment overlaps."""
    global _scheduler_instance, _scheduler_retry_thread
    with _scheduler_guard:
        if _scheduler_instance is not None and _scheduler_instance.running:
            return _scheduler_instance
        if not is_persistent_storage_ready():
            logger.warning(
                "Daily-care scheduler not started: set BOT_DB_PATH inside the Railway volume mount"
            )
            return None

        owner_id = f"{os.getpid()}:{uuid.uuid4().hex}"
        ttl_seconds = int(getattr(config, "DAILYCARE_LOCK_TTL_SECONDS", 90))
        if database.acquire_scheduler_lock(owner_id, ttl_seconds=ttl_seconds):
            return _start_scheduler_with_lease(bot, owner_id)

        logger.info(
            "Daily-care scheduler waiting for the database lease; retrying in the background"
        )
        if _scheduler_retry_thread is None or not _scheduler_retry_thread.is_alive():
            _scheduler_retry_stop.clear()
            _scheduler_retry_thread = threading.Thread(
                target=_retry_start_scheduler,
                args=(bot,),
                name="dailycare-scheduler-lease-retry",
                daemon=True,
            )
            _scheduler_retry_thread.start()
        return None


def shutdown_scheduler(scheduler: Optional[BackgroundScheduler]) -> None:
    global _scheduler_instance, _scheduler_lock_owner
    _scheduler_retry_stop.set()
    with _scheduler_guard:
        target = scheduler if scheduler is not None else _scheduler_instance
        if target is None:
            return
        owner_id = None
        if _scheduler_instance is target:
            _scheduler_instance = None
            owner_id = _scheduler_lock_owner
            _scheduler_lock_owner = None
    try:
        if target.running:
            target.shutdown(wait=False)
    finally:
        if owner_id:
            database.release_scheduler_lock(owner_id)
