"""
Database Management សម្រាប់ Team ស្មោះស្នេហ៍ Telegram Bot (SQLite Persistence)
Upgraded with conversation summary support and safer DB wrappers.
"""
import sqlite3
import logging
from contextlib import contextmanager
from typing import Generator, List, Dict, Optional, Tuple, Any
from datetime import datetime
import time

logger = logging.getLogger(__name__)

DB_PATH = "bot_database.db"


@contextmanager
def get_db(db_path: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
    path = db_path or DB_PATH
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
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
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

        conn.commit()
    logger.info("Database initialized successfully at %s", path)


def register_or_update_user(user_id: int, chat_id: int, username: Optional[str], first_name: Optional[str], last_name: Optional[str], db_path: Optional[str] = None) -> None:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (user_id, chat_id, username, first_name, last_name, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            ON CONFLICT(user_id) DO UPDATE SET
                chat_id=excluded.chat_id,
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                status='active'
        """, (user_id, chat_id, username, first_name, last_name))
        conn.commit()


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
