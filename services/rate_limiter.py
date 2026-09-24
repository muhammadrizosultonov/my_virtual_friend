import asyncio
import logging
from datetime import datetime
import pytz
from typing import Tuple
from database.db import Database

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Ish kunlari (Dushanba-Juma) va ish vaqti (09:00 - 18:00) nazorati hamda har bir
    foydalanuvchiga kuniga faqat 1 marta avto-javob qaytarish cheklovini boshqaruvchi servis
    (Race condition va albomlar debouncing bilan).
    """

    def __init__(
        self,
        db: Database,
        tz_name: str = "Asia/Tashkent",
        work_start_hour: int = 9,
        work_end_hour: int = 18
    ):
        self.db = db
        try:
            self.tz = pytz.timezone(tz_name)
        except Exception:
            self.tz = pytz.UTC
        self.work_start_hour = work_start_hour
        self.work_end_hour = work_end_hour
        self._user_locks: dict[int, asyncio.Lock] = {}
        self._processed_albums: set[int] = set()

    def _get_now(self) -> datetime:
        return datetime.now(self.tz)

    def is_workday(self) -> bool:
        """Faqat Dushanba (0) dan Juma (4) gacha bo'lgan kunlarni ruxsat etadi."""
        return 0 <= self._get_now().weekday() <= 4

    def is_work_hours(self) -> bool:
        """Faqat 09:00 dan 18:00 gacha bo'lgan vaqt oralig'ini ruxsat etadi."""
        now = self._get_now()
        start_time = now.replace(hour=self.work_start_hour, minute=0, second=0, microsecond=0)
        end_time = now.replace(hour=self.work_end_hour, minute=0, second=0, microsecond=0)
        return start_time <= now <= end_time

    def get_today_date_str(self) -> str:
        """Bugungi sana: YYYY-MM-DD."""
        return self._get_now().strftime("%Y-%m-%d")

    def _get_user_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    async def should_auto_reply(self, user_id: int, grouped_id: int | None = None) -> Tuple[bool, str]:
        """
        Avto-javob berish mumkinligini tekshiradi:
        1. Faqat ish kunlari (Dushanba - Juma);
        2. Faqat ish vaqtida (09:00 dan 18:00 gacha);
        3. Har bir foydalanuvchiga 1 kunda faqat 1 marta;
        4. Albomlar va parallel xabarlarda faqat 1 marta (debouncing).
        """
        # 1. Dam olish kunlari tekshiruvi
        if not self.is_workday():
            return False, "Dam olish kuni (Shanba/Yakshanba): Avto-javob yuborilmadi"

        # 2. Ish vaqti tekshiruvi (09:00 dan 18:00 gacha)
        if not self.is_work_hours():
            return False, "Ish vaqtidan tashqari vaqt (Faqat 09:00 - 18:00 oralig'ida yuboriladi)"

        # 3. Agar albom (grouped_id) bo'lsa va bu albomga allaqachon javob berilgan bo'lsa
        if grouped_id is not None:
            if grouped_id in self._processed_albums:
                return False, "Ushbu albom uchun avto-javob allaqachon ko'rib chiqilgan"
            self._processed_albums.add(grouped_id)
            if len(self._processed_albums) > 1000:
                self._processed_albums.clear()

        # 4. Foydalanuvchi darajasidagi atomik qulflash (Atomic Lock)
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
