import asyncio
import logging
from datetime import datetime
import pytz
from typing import Tuple
from database.db import Database

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Ish kunlari nazorati va har bir foydalanuvchiga kuniga faqat 1 marta avto-javob
    qaytarish cheklovini boshqaruvchi servis (Race condition va albomlar debouncing bilan).
    """

    def __init__(self, db: Database, tz_name: str = "Asia/Tashkent"):
        self.db = db
        try:
            self.tz = pytz.timezone(tz_name)
        except Exception:
            self.tz = pytz.UTC
        self._user_locks: dict[int, asyncio.Lock] = {}
        self._processed_albums: set[int] = set()

    def _get_now(self) -> datetime:
        return datetime.now(self.tz)

    def is_workday(self) -> bool:
        """Faqat Dushanba (0) dan Juma (4) gacha bo'lgan kunlarni ruxsat etadi."""
        return 0 <= self._get_now().weekday() <= 4

    def get_today_date_str(self) -> str:
        """Bugungi sana: YYYY-MM-DD."""
        return self._get_now().strftime("%Y-%m-%d")

    def _get_user_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    async def should_auto_reply(self, user_id: int, grouped_id: int | None = None) -> Tuple[bool, str]:
        """
        Avto-javob berish mumkinligini tekshiradi va poyga holati (race condition) hamda
        bir nechta rasm/albom kelganda takrorlanishning oldini oladi.
        """
        # 1. Dam olish kunlari tekshiruvi
        if not self.is_workday():
            return False, "Dam olish kuni (Shanba/Yakshanba): Avto-javob yuborilmadi"

        # 2. Agar albom (grouped_id) bo'lsa va bu albomga allaqachon javob berilgan bo'lsa
        if grouped_id is not None:
            if grouped_id in self._processed_albums:
                return False, "Ushbu albom uchun avto-javob allaqachon ko'rib chiqilgan"
            self._processed_albums.add(grouped_id)
            if len(self._processed_albums) > 1000:
                self._processed_albums.clear()

        # 3. Foydalanuvchi darajasidagi atomik qulflash (Atomic Lock)
        lock = self._get_user_lock(user_id)
        async with lock:
            today = self.get_today_date_str()
            already_replied = await self.db.is_replied_today(user_id, today)
            if already_replied:
                return False, "Bugun allaqachon avto-javob berilgan (Limit: 1 marta/kun)"

            # Muhim: Darhol bazada bron qilib qo'yish (boshqa parallel xabarlar takrorlanmasligi uchun)!
            await self.db.mark_as_replied(user_id, today)
            return True, "Avto-javob bron qilindi ✅"

    async def record_reply(self, user_id: int) -> None:
        """Foydalanuvchiga bugun avto-javob berilganini bazada belgilash."""
        today = self.get_today_date_str()
        await self.db.mark_as_replied(user_id, today)
        logger.debug(f"User {user_id} uchun {today} sanasiga avto-javob belgilandi.")
