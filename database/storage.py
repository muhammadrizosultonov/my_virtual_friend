import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class BaseStorage(ABC):
    """Xotira va kesh interfeysi (Redis yoki SQLite)."""

    @abstractmethod
    async def initialize(self) -> None:
        """Ma'lumotlar bazasini ishga tushirish."""
        pass

    @abstractmethod
    async def is_replied_today(self, user_id: int, date_str: str) -> bool:
        """Foydalanuvchiga bugun avto-javob yozilganligini tekshirish."""
        pass

    @abstractmethod
    async def mark_as_replied(self, user_id: int, date_str: str, ttl_seconds: int = 86400) -> None:
        """Foydalanuvchiga bugun avto-javob berilganini belgilash."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Ulanishni yopish."""
        pass


class RedisStorage(BaseStorage):
    """Redis orqali ishlovchi asinxron kesh saqlovchisi."""

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.client = None

    async def initialize(self) -> None:
        import redis.asyncio as aioredis
        self.client = aioredis.from_url(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True
        )
        # Test connection
        await self.client.ping()
        logger.info(f"✅ Redis muvaffaqiyatli ulandi: {self.redis_url}")

    async def is_replied_today(self, user_id: int, date_str: str) -> bool:
        if not self.client:
            return False
        key = f"reply:{user_id}:{date_str}"
        exists = await self.client.exists(key)
        return bool(exists)

    async def mark_as_replied(self, user_id: int, date_str: str, ttl_seconds: int = 86400) -> None:
        if not self.client:
            return
        key = f"reply:{user_id}:{date_str}"
        await self.client.set(key, "1", ex=ttl_seconds)
        logger.debug(f"Redis: {key} saqlandi (TTL: {ttl_seconds}s)")

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()
            logger.info("Redis ulanishi yopildi.")


class SQLiteStorage(BaseStorage):
    """SQLite orqali ishlovchi asinxron lokal ma'lumotlar bazasi (Fallback)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = None

    async def initialize(self) -> None:
        import aiosqlite
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS daily_replies (
                user_id INTEGER NOT NULL,
                reply_date TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, reply_date)
            )
        """)
        await self._db.commit()
        logger.info(f"✅ SQLite muvaffaqiyatli ishga tushdi: {self.db_path}")

    async def is_replied_today(self, user_id: int, date_str: str) -> bool:
        if not self._db:
            return False
        async with self._db.execute(
            "SELECT 1 FROM daily_replies WHERE user_id = ? AND reply_date = ?",
            (user_id, date_str)
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None

    async def mark_as_replied(self, user_id: int, date_str: str, ttl_seconds: int = 86400) -> None:
        if not self._db:
            return
        await self._db.execute(
            "INSERT OR REPLACE INTO daily_replies (user_id, reply_date) VALUES (?, ?)",
            (user_id, date_str)
        )
        await self._db.commit()
        logger.debug(f"SQLite: user_id={user_id}, date={date_str} saqlandi")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            logger.info("SQLite ulanishi yopildi.")


async def init_storage(redis_url: Optional[str], sqlite_path: str) -> BaseStorage:
    """
    Kesh saqlash tizimini ishga tushiradi:
    Redis mavjud bo'lsa va ulansa - RedisStorage qaytaradi.
    Aks holda - avtomatik SQLiteStorage ga fallback qiladi.
    """
    if redis_url:
        try:
            storage = RedisStorage(redis_url)
            await storage.initialize()
            return storage
        except Exception as e:
            logger.warning(f"⚠️ Redis ga ulanib bo'lmadi ({e}). SQLite fallback rejimiga o'tilmoqda...")

    storage = SQLiteStorage(sqlite_path)
    await storage.initialize()
    return storage

