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
        # បើក WAL mode សម្រាប់ SQLite ដើម្បីដំណើរការលឿននិងមានស្ថេរភាព
        cursor.execute("PRAGMA journal_mode=WAL;")
        
        # តារាង Users
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
        
        # តារាង Settings
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        # តារាង Chat History សម្រាប់ AI
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # តារាង AI Usage
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_usage (
                user_id INTEGER,
                date TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, date)
            )
        """)
        
        # តារាង Photo Sessions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS photo_sessions (
                user_id INTEGER PRIMARY KEY,
                file_id TEXT,
                local_path TEXT,
                aspect_ratio TEXT DEFAULT 'auto',
                selected_style TEXT DEFAULT 'realistic',
                state TEXT DEFAULT 'WAITING_FOR_STYLE',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # តារាង Photo Usage
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS photo_usage (
                user_id INTEGER,
                date TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, date)
            )
        """)
        
        # តារាង Announcements
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                message TEXT,
                group_chat_id INTEGER,
                group_status TEXT,
                private_success_count INTEGER,
                private_fail_count INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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


def mark_user_inactive(user_id: int, db_path: Optional[str] = None) -> None:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET status='inactive' WHERE user_id = ?", (user_id,))
        conn.commit()


def get_active_users(db_path: Optional[str] = None) -> List[sqlite3.Row]:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE status = 'active'")
        return cursor.fetchall()


def get_total_users_count(db_path: Optional[str] = None) -> Tuple[int, int]:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE status = 'active'")
        active = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users")
        total = cursor.fetchone()[0]
        return active, total


def set_configured_group(group_id: int, group_title: str, db_path: Optional[str] = None) -> None:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('group_id', ?)", (str(group_id),))
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('group_title', ?)", (group_title,))
        conn.commit()


def get_configured_group(db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'group_id'")
        row_id = cursor.fetchone()
        cursor.execute("SELECT value FROM settings WHERE key = 'group_title'")
        row_title = cursor.fetchone()
        if row_id and row_title:
            return {"group_id": int(row_id[0]), "group_title": row_title[0]}
    return None


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


def save_photo_session(user_id: int, file_id: str, local_path: str, aspect_ratio: str = "auto", db_path: Optional[str] = None) -> None:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO photo_sessions (user_id, file_id, local_path, aspect_ratio, selected_style, state, updated_at)
            VALUES (?, ?, ?, ?, 'realistic', 'WAITING_FOR_STYLE', CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                file_id=excluded.file_id,
                local_path=excluded.local_path,
                aspect_ratio=excluded.aspect_ratio,
                selected_style='realistic',
                state='WAITING_FOR_STYLE',
                updated_at=CURRENT_TIMESTAMP
        """, (user_id, file_id, local_path, aspect_ratio))
        conn.commit()


def get_photo_session(user_id: int, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM photo_sessions WHERE user_id = ?", (user_id,))
        return cursor.fetchone()


def update_photo_session_state(user_id: int, state: str, selected_style: Optional[str] = None, aspect_ratio: Optional[str] = None, db_path: Optional[str] = None) -> None:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        if selected_style and aspect_ratio:
            cursor.execute("UPDATE photo_sessions SET state = ?, selected_style = ?, aspect_ratio = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?", (state, selected_style, aspect_ratio, user_id))
        elif selected_style:
            cursor.execute("UPDATE photo_sessions SET state = ?, selected_style = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?", (state, selected_style, user_id))
        elif aspect_ratio:
            cursor.execute("UPDATE photo_sessions SET state = ?, aspect_ratio = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?", (state, aspect_ratio, user_id))
        else:
            cursor.execute("UPDATE photo_sessions SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?", (state, user_id))
        conn.commit()


def clear_photo_session(user_id: int, db_path: Optional[str] = None) -> Optional[str]:
    session = get_photo_session(user_id, db_path)
    local_path = session["local_path"] if session else None
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM photo_sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    return local_path


def check_and_increment_daily_photo_usage(user_id: int, max_daily_limit: int, db_path: Optional[str] = None) -> Tuple[bool, int]:
    today = datetime.now().strftime("%Y-%m-%d")
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count FROM photo_usage WHERE user_id = ? AND date = ?", (user_id, today))
        row = cursor.fetchone()
        current_count = row[0] if row else 0
        
        if max_daily_limit > 0 and current_count >= max_daily_limit:
            return False, current_count
        
        if row:
            cursor.execute("UPDATE photo_usage SET count = count + 1 WHERE user_id = ? AND date = ?", (user_id, today))
        else:
            cursor.execute("INSERT INTO photo_usage (user_id, date, count) VALUES (?, ?, 1)", (user_id, today))
        conn.commit()
        return True, current_count + 1


def record_announcement(admin_id: int, message: str, group_chat_id: Optional[int], group_status: str, private_success_count: int, private_fail_count: int, db_path: Optional[str] = None) -> None:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO announcements (admin_id, message, group_chat_id, group_status, private_success_count, private_fail_count)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (admin_id, message, group_chat_id, group_status, private_success_count, private_fail_count))
        conn.commit()
