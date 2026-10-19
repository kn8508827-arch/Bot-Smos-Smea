"""
Database Management សម្រាប់ Team ស្មោះស្នេហ៍ Telegram Bot (SQLite Persistence)
Upgraded with conversation summary support and safer DB wrappers.
"""
import os
import sqlite3
import logging
from contextlib import contextmanager
from typing import Generator, List, Dict, Optional, Tuple, Any
from datetime import datetime
import time

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("BOT_DB_PATH", "bot_database.db")


@contextmanager
def get_db(db_path: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
    path = db_path or DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Optional[str] = None) -> None:
    path = db_path or DB_PATH
    with get_db(path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                status TEXT DEFAULT 'active',
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                chat_type TEXT NOT NULL DEFAULT 'unknown'
            )
        """)

        # Additive migration for databases created before interaction tracking.
        existing_user_columns = {
            row["name"] for row in cursor.execute("PRAGMA table_info(users)").fetchall()
        }
        for column_name, column_ddl in (
            ("first_seen", "TIMESTAMP"),
            ("last_seen", "TIMESTAMP"),
            ("chat_type", "TEXT NOT NULL DEFAULT 'unknown'"),
        ):
            if column_name not in existing_user_columns:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_ddl}")
        cursor.execute("""
            UPDATE users
            SET first_seen = COALESCE(first_seen, joined_at, CURRENT_TIMESTAMP),
                last_seen = COALESCE(last_seen, joined_at, CURRENT_TIMESTAMP),
                chat_type = CASE
                    WHEN COALESCE(NULLIF(chat_type, ''), 'unknown') = 'unknown'
                         AND chat_id = user_id THEN 'private'
                    ELSE COALESCE(NULLIF(chat_type, ''), 'unknown')
                END
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_usage (
                user_id INTEGER,
                date TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, date)
            )
        """)

        # Non-destructive addition: conversation summaries for optional memory compaction
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                user_id INTEGER PRIMARY KEY,
                summary_text TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notification_preferences (
                user_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notification_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                local_date TEXT NOT NULL,
                delivery_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
    logger.info("Database initialized successfully at %s", path)


def register_or_update_user(
    user_id: int,
    chat_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
    db_path: Optional[str] = None,
    chat_type: str = "private",
) -> None:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (
                user_id, chat_id, username, first_name, last_name, status,
                first_seen, last_seen, chat_type
            )
            VALUES (?, ?, ?, ?, ?, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                chat_id=CASE
                    WHEN users.chat_type = 'private' AND excluded.chat_type != 'private'
                    THEN users.chat_id ELSE excluded.chat_id END,
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                status='active',
                last_seen=CURRENT_TIMESTAMP,
                chat_type=CASE
                    WHEN users.chat_type = 'private' AND excluded.chat_type != 'private'
                    THEN users.chat_type ELSE excluded.chat_type END
        """, (user_id, chat_id, username, first_name, last_name, chat_type))
        conn.commit()


def get_notification_enabled(user_id: int, db_path: Optional[str] = None) -> bool:
    """Notifications are opt-in; users without a preference are disabled."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT enabled FROM notification_preferences WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return bool(row[0]) if row else False


def set_notification_enabled(user_id: int, enabled: bool, db_path: Optional[str] = None) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO notification_preferences (user_id, enabled, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                enabled=excluded.enabled,
                updated_at=CURRENT_TIMESTAMP
            """,
            (user_id, 1 if enabled else 0),
        )
        conn.commit()


def get_eligible_notification_recipients(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return opted-in users reachable through their own private bot chat."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT u.user_id, u.chat_id, u.first_name, u.username
            FROM users AS u
            JOIN notification_preferences AS p ON p.user_id = u.user_id
            WHERE u.status = 'active'
              AND u.chat_id IS NOT NULL
              AND u.chat_type = 'private'
              AND p.enabled = 1
            ORDER BY u.user_id
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_eligible_notification_recipient_batch(
    after_user_id: int = 0,
    limit: int = 100,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return a bounded, keyset-paginated batch of opted-in private chats."""
    batch_limit = max(1, min(int(limit), 1000))
    with get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT u.user_id, u.chat_id, u.first_name, u.username
            FROM users AS u
            JOIN notification_preferences AS p ON p.user_id = u.user_id
            WHERE u.status = 'active'
              AND u.chat_id IS NOT NULL
              AND u.chat_type = 'private'
              AND p.enabled = 1
              AND u.user_id > ?
            ORDER BY u.user_id
            LIMIT ?
            """,
            (after_user_id, batch_limit),
        ).fetchall()
        return [dict(row) for row in rows]


def is_eligible_notification_recipient(user_id: int, db_path: Optional[str] = None) -> bool:
    """Recheck opt-in and private-chat reachability immediately before a send."""
    with get_db(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM users AS u
            JOIN notification_preferences AS p ON p.user_id = u.user_id
            WHERE u.user_id = ? AND u.status = 'active'
              AND u.chat_id IS NOT NULL AND u.chat_type = 'private'
              AND p.enabled = 1
            """,
            (user_id,),
        ).fetchone()
        return row is not None


def get_dailycare_enabled(db_path: Optional[str] = None) -> bool:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'dailycare_enabled'"
        ).fetchone()
        return row is not None and row[0] == "1"


def set_dailycare_enabled(enabled: bool, db_path: Optional[str] = None) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES ('dailycare_enabled', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            ("1" if enabled else "0",),
        )
        conn.commit()


def initialize_dailycare_enabled(enabled: bool, db_path: Optional[str] = None) -> None:
    """Seed the persisted global switch from config without overriding admin changes."""
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('dailycare_enabled', ?)",
            ("1" if enabled else "0",),
        )
        conn.commit()


_SCHEDULER_LOCK_KEY = "dailycare_scheduler_lock"


def acquire_scheduler_lock(
    owner_id: str,
    ttl_seconds: int = 60,
    db_path: Optional[str] = None,
    now: Optional[float] = None,
) -> bool:
    """Atomically acquire or renew the SQLite-backed scheduler lease."""
    current_time = time.time() if now is None else float(now)
    expiry = current_time + max(1, int(ttl_seconds))
    with get_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (_SCHEDULER_LOCK_KEY,)
        ).fetchone()
        current_value = row[0] if row else None
        active_owner = None
        active_expiry = 0.0
        if current_value:
            try:
                active_owner, expiry_text = current_value.rsplit("|", 1)
                active_expiry = float(expiry_text)
            except (TypeError, ValueError):
                active_owner, active_expiry = None, 0.0

        if active_owner and active_owner != owner_id and active_expiry > current_time:
            conn.rollback()
            return False

        new_value = f"{owner_id}|{expiry:.6f}"
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (_SCHEDULER_LOCK_KEY, new_value),
        )
        conn.commit()
        return True


def heartbeat_scheduler_lock(
    owner_id: str,
    ttl_seconds: int = 60,
    db_path: Optional[str] = None,
    now: Optional[float] = None,
) -> bool:
    """Extend a lease only while its current owner still holds it."""
    current_time = time.time() if now is None else float(now)
    new_value = f"{owner_id}|{current_time + max(1, int(ttl_seconds)):.6f}"
    with get_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (_SCHEDULER_LOCK_KEY,)
        ).fetchone()
        if not row or not str(row[0]).startswith(f"{owner_id}|"):
            conn.rollback()
            return False
        conn.execute(
            "UPDATE settings SET value = ? WHERE key = ?",
            (new_value, _SCHEDULER_LOCK_KEY),
        )
        conn.commit()
        return True


def release_scheduler_lock(owner_id: str, db_path: Optional[str] = None) -> None:
    """Release this process's lease without touching another owner's lease."""
    with get_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (_SCHEDULER_LOCK_KEY,)
        ).fetchone()
        if row and str(row[0]).rsplit("|", 1)[0] == owner_id:
            conn.execute("DELETE FROM settings WHERE key = ?", (_SCHEDULER_LOCK_KEY,))
        conn.commit()


def notification_delivery_key(user_id: int, local_date: str, category: str) -> str:
    return f"{user_id}:{local_date}:{category}"


def claim_notification_delivery(
    user_id: int,
    chat_id: int,
    category: str,
    local_date: str,
    db_path: Optional[str] = None,
) -> Tuple[bool, str]:
    """Atomically claim one user/category/day so concurrent jobs cannot double-send."""
    delivery_key = notification_delivery_key(user_id, local_date, category)
    with get_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO notification_deliveries
                (user_id, chat_id, category, local_date, delivery_key, status, attempts)
            VALUES (?, ?, ?, ?, ?, 'pending', 0)
            """,
            (user_id, chat_id, category, local_date, delivery_key),
        )
        claimed = cursor.rowcount == 1
        conn.commit()
        return claimed, delivery_key


def update_notification_delivery(
    delivery_key: str,
    status: str,
    attempts: int,
    last_error: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            """
            UPDATE notification_deliveries
            SET status = ?, attempts = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE delivery_key = ?
            """,
            (status, attempts, last_error, delivery_key),
        )
        conn.commit()


def get_notification_delivery_stats(local_date: str, db_path: Optional[str] = None) -> Dict[str, int]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM notification_deliveries
            WHERE local_date = ?
            GROUP BY status
            """,
            (local_date,),
        ).fetchall()
        return {row["status"]: row["count"] for row in rows}


def get_last_notification_delivery_at(db_path: Optional[str] = None) -> Optional[str]:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(updated_at) AS last_updated FROM notification_deliveries"
        ).fetchone()
        return row["last_updated"] if row and row["last_updated"] else None


# Safer wrapper for adding chat messages with minimal retries
def add_chat_message(user_id: int, role: str, content: str, db_path: Optional[str] = None) -> None:
    retries = 2
    for attempt in range(retries):
        try:
            with get_db(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
                conn.commit()
            return
        except sqlite3.OperationalError as e:
            logger.debug(f"SQLite OperationalError when writing chat message (attempt {attempt}): {e}")
            time.sleep(0.05 * (attempt + 1))
            continue
        except Exception as e:
            logger.exception(f"Unexpected error when writing chat message: {e}")
            return


def get_chat_history(user_id: int, limit: int = 6, db_path: Optional[str] = None) -> List[Dict[str, str]]:
    try:
        with get_db(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT role, content FROM chat_history 
                WHERE user_id = ? 
                ORDER BY id DESC LIMIT ?
            """, (user_id, limit))
            rows = cursor.fetchall()
            # Return in chronological order
            return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]
    except Exception as e:
        logger.debug(f"Could not read chat history for user {user_id}: {e}")
        return []


def clear_chat_history(user_id: int, db_path: Optional[str] = None) -> None:
    try:
        with get_db(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
            conn.commit()
    except Exception as e:
        logger.exception(f"Error clearing chat history for user {user_id}: {e}")


def get_daily_usage(user_id: int, db_path: Optional[str] = None) -> int:
    """Read-only: return today's AI usage count for a user (0 on error)."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with get_db(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT count FROM ai_usage WHERE user_id = ? AND date = ?", (user_id, today))
            row = cursor.fetchone()
            return row[0] if row else 0
    except Exception as e:
        logger.debug(f"Could not read daily usage for user {user_id}: {e}")
        return 0


def check_and_increment_daily_usage(user_id: int, max_daily_limit: int, db_path: Optional[str] = None) -> Tuple[bool, int]:
    today = datetime.now().strftime("%Y-%m-%d")
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count FROM ai_usage WHERE user_id = ? AND date = ?", (user_id, today))
        row = cursor.fetchone()
        current_count = row[0] if row else 0

        if max_daily_limit > 0 and current_count >= max_daily_limit:
            return False, current_count

        if row:
            cursor.execute("UPDATE ai_usage SET count = count + 1 WHERE user_id = ? AND date = ?", (user_id, today))
        else:
            cursor.execute("INSERT INTO ai_usage (user_id, date, count) VALUES (?, ?, 1)", (user_id, today))
        conn.commit()
        return True, current_count + 1


# Conversation summary helpers
def get_conversation_summary(user_id: int, db_path: Optional[str] = None) -> Optional[str]:
    try:
        with get_db(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT summary_text FROM conversation_summaries WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.debug(f"Could not fetch summary for user {user_id}: {e}")
        return None


def upsert_conversation_summary(user_id: int, summary_text: str, db_path: Optional[str] = None) -> None:
    try:
        with get_db(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO conversation_summaries (user_id, summary_text, last_updated) VALUES (?, ?, CURRENT_TIMESTAMP)"
                           " ON CONFLICT(user_id) DO UPDATE SET summary_text=excluded.summary_text, last_updated=CURRENT_TIMESTAMP",
                           (user_id, summary_text))
            conn.commit()
    except Exception as e:
        logger.exception(f"Failed to upsert conversation summary for {user_id}: {e}")


# Small utilities used by tests
def get_active_users(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        with get_db(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, username, first_name, last_name FROM users WHERE status = 'active'")
            rows = cursor.fetchall()
            return [{"user_id": r["user_id"], "username": r["username"], "first_name": r["first_name"]} for r in rows]
    except Exception as e:
        logger.debug(f"Could not list active users: {e}")
        return []


def mark_user_inactive(user_id: int, db_path: Optional[str] = None) -> None:
    try:
        with get_db(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET status='inactive' WHERE user_id = ?", (user_id,))
            conn.commit()
    except Exception as e:
        logger.debug(f"Could not mark user {user_id} inactive: {e}")


def get_total_users_count(db_path: Optional[str] = None) -> Tuple[int, int]:
    try:
        with get_db(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM users WHERE status='active'")
            active = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM users")
            total = cursor.fetchone()[0]
            return active, total
    except Exception as e:
        logger.debug(f"Could not get user counts: {e}")
        return 0, 0
