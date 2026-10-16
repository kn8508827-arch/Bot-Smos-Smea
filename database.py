"""
Database Management សម្រាប់ Team ស្មោះស្នេហ៍ Telegram Bot (SQLite Persistence)
"""
import sqlite3
import logging
from contextlib import contextmanager
from typing import Generator, List, Dict, Optional, Tuple, Any
from datetime import datetime

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


def add_chat_message(user_id: int, role: str, content: str, db_path: Optional[str] = None) -> None:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
        conn.commit()


def get_chat_history(user_id: int, limit: int = 6, db_path: Optional[str] = None) -> List[Dict[str, str]]:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT role, content FROM chat_history 
            WHERE user_id = ? 
            ORDER BY id DESC LIMIT ?
        """, (user_id, limit))
        rows = cursor.fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def clear_chat_history(user_id: int, db_path: Optional[str] = None) -> None:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
        conn.commit()


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
