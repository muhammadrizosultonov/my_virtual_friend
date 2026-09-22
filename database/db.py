import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import aiosqlite

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str = "sessions/messages.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._create_tables()
        logger.info(f"✅ Ma'lumotlar bazasi ishga tushdi: {self.db_path}")

    async def _create_tables(self) -> None:
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                sender_name TEXT,
                username TEXT,
                is_outgoing BOOLEAN NOT NULL,
                text TEXT,
                is_deleted BOOLEAN DEFAULT 0,
                deleted_at TIMESTAMP NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Eski bazalar uchun avtomatik migratsiya (ustunlarni tekshirish)
        async with self._db.execute("PRAGMA table_info(messages)") as cursor:
            columns = [row["name"] for row in await cursor.fetchall()]
            if "is_deleted" not in columns:
                await self._db.execute("ALTER TABLE messages ADD COLUMN is_deleted BOOLEAN DEFAULT 0")
                logger.info("Migratsiya: 'is_deleted' ustuni qo'shildi.")
            if "deleted_at" not in columns:
                await self._db.execute("ALTER TABLE messages ADD COLUMN deleted_at TIMESTAMP NULL")
                logger.info("Migratsiya: 'deleted_at' ustuni qo'shildi.")

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS daily_replies (
                user_id INTEGER NOT NULL,
                reply_date TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, reply_date)
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_chat_time 
            ON messages(chat_id, created_at)
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_msg_chat
            ON messages(message_id, chat_id)
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_time 
            ON messages(created_at)
        """)
        await self._db.commit()

    async def is_replied_today(self, user_id: int, date_str: str) -> bool:
        """Foydalanuvchiga bugun avto-javob yuborilganligini tekshirish."""
        if not self._db:
            return False
        async with self._db.execute(
            "SELECT 1 FROM daily_replies WHERE user_id = ? AND reply_date = ?",
            (user_id, date_str)
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None

    async def mark_as_replied(self, user_id: int, date_str: str) -> None:
        """Foydalanuvchiga bugun avto-javob yuborilganini belgilash."""
        if not self._db:
            return
        await self._db.execute(
            "INSERT OR REPLACE INTO daily_replies (user_id, reply_date) VALUES (?, ?)",
            (user_id, date_str)
        )
        await self._db.commit()

    async def save_message(
        self,
        message_id: int,
        chat_id: int,
        sender_name: str,
        username: Optional[str],
        is_outgoing: bool,
        text: str,
        created_at: Optional[datetime] = None
    ) -> None:
        """Xabarni bazaga saqlash."""
        if not self._db:
            return

        if created_at is None:
            created_at_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        else:
            created_at_str = created_at.strftime("%Y-%m-%d %H:%M:%S")

        await self._db.execute(
            """
            INSERT INTO messages (
                message_id, chat_id, sender_name, username, is_outgoing, text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, chat_id, sender_name, username, is_outgoing, text, created_at_str)
        )
        await self._db.commit()

    async def get_messages_by_ids(
        self, message_ids: List[int], chat_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """O'chirilgan ID lar bo'yicha xabarlarni topish."""
        if not self._db or not message_ids:
            return []

        placeholders = ",".join("?" for _ in message_ids)
        params: List[Any] = list(message_ids)

        query = f"""
            SELECT id, message_id, chat_id, sender_name, username, is_outgoing, text, is_deleted, created_at
            FROM messages
            WHERE message_id IN ({placeholders})
        """
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)

        query += " ORDER BY id DESC"

        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def mark_messages_deleted(
        self, message_ids: List[int], chat_id: Optional[int] = None
    ) -> None:
        """Xabarlarni o'chirilgan deb belgilash."""
        if not self._db or not message_ids:
            return

        deleted_at_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        placeholders = ",".join("?" for _ in message_ids)
        params: List[Any] = [deleted_at_str] + list(message_ids)

        query = f"""
            UPDATE messages
            SET is_deleted = 1, deleted_at = ?
            WHERE message_id IN ({placeholders})
        """
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)

        await self._db.execute(query, params)
        await self._db.commit()

    async def get_messages_for_period(
        self, start_dt: datetime, end_dt: datetime
    ) -> List[Dict[str, Any]]:
        """Berilgan vaqt oralig'idagi barcha xabarlarni tartiblangan holda olish."""
        if not self._db:
            return []

        start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

        async with self._db.execute(
            """
            SELECT message_id, chat_id, sender_name, username, is_outgoing, text, is_deleted, created_at
            FROM messages
            WHERE created_at >= ? AND created_at <= ?
            ORDER BY chat_id, created_at ASC
            """,
            (start_str, end_str)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_today_overview_stats(
        self, start_dt: datetime, end_dt: datetime
    ) -> Dict[str, Any]:
        """Bugungi kunlik umumiy statistikani hisoblash."""
        if not self._db:
            return {
                "total_messages": 0,
                "incoming_count": 0,
                "outgoing_count": 0,
                "unique_contacts": 0,
                "deleted_count": 0,
                "top_contacts_today": []
            }

        start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

        async with self._db.execute(
            """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN is_outgoing = 0 THEN 1 ELSE 0 END) as incoming,
                SUM(CASE WHEN is_outgoing = 1 THEN 1 ELSE 0 END) as outgoing,
                SUM(CASE WHEN is_deleted = 1 THEN 1 ELSE 0 END) as deleted,
                COUNT(DISTINCT chat_id) as unique_chats
            FROM messages
            WHERE created_at >= ? AND created_at <= ?
            """,
            (start_str, end_str)
        ) as cursor:
            row = await cursor.fetchone()
            total = row["total"] or 0
            incoming = row["incoming"] or 0
            outgoing = row["outgoing"] or 0
            deleted = row["deleted"] or 0
            unique_chats = row["unique_chats"] or 0

        async with self._db.execute(
            """
            SELECT 
                chat_id,
                MAX(sender_name) as name,
                MAX(username) as username,
                COUNT(*) as message_count,
                SUM(CASE WHEN is_outgoing = 0 THEN 1 ELSE 0 END) as incoming,
                SUM(CASE WHEN is_outgoing = 1 THEN 1 ELSE 0 END) as outgoing
            FROM messages
            WHERE created_at >= ? AND created_at <= ?
            GROUP BY chat_id
            ORDER BY message_count DESC
            LIMIT 5
            """,
            (start_str, end_str)
        ) as cursor:
            top_today_rows = await cursor.fetchall()
            top_contacts_today = [dict(r) for r in top_today_rows]

        return {
            "total_messages": total,
            "incoming_count": incoming,
            "outgoing_count": outgoing,
            "deleted_count": deleted,
            "unique_contacts": unique_chats,
            "top_contacts_today": top_contacts_today
        }

    async def get_top_contacts_for_days(
        self, days: int, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Oxirgi N kun ichidagi eng faol suhbatdoshlar reytingi."""
        if not self._db:
            return []

        start_dt = datetime.now() - timedelta(days=days)
        start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")

        async with self._db.execute(
            """
            SELECT 
                chat_id,
                MAX(sender_name) as name,
                MAX(username) as username,
                COUNT(*) as total_count,
                SUM(CASE WHEN is_outgoing = 0 THEN 1 ELSE 0 END) as incoming,
                SUM(CASE WHEN is_outgoing = 1 THEN 1 ELSE 0 END) as outgoing
            FROM messages
            WHERE created_at >= ?
            GROUP BY chat_id
            ORDER BY total_count DESC
            LIMIT ?
            """,
            (start_str, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            logger.info("Ma'lumotlar bazasi yopildi.")
