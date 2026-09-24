import asyncio
import logging
import random
from typing import List, Optional, Tuple
from telethon import TelegramClient, errors
from telethon.tl.types import Channel, Chat

from database.db import Database

logger = logging.getLogger(__name__)

DEFAULT_AD_TEXT = """
🚀 <b>Sifatli va Tezkor IT Xizmatlari!</b>

Sizga biznesingiz yoki loyihangiz uchun ishonchli yechim kerakmi? Biz sizga quyidagi xizmatlarni qulay narxlarda va yuqori sifatda taqdim etamiz:

🤖 <b>Telegram Botlar:</b> Har qanday murakkablikdagi savdo, to'lov (Click/Payme), AI yordamchi, qabul va avtomatlashtirish botlari.
🌐 <b>Veb-saytlar va Web-ilovalar:</b> Landing page, internet do'kon, vizitka va korporativ platformalar.
📊 <b>CRM & Boshqaruv tizimlari:</b> Biznesingiz hisob-kitobi va mijozlar bazasini to'liq avtomatlashtirish.
🖥 <b>Server & VPS sozlash:</b> 24/7 uzluksiz va xavfsiz ishlash kafolati.

✅ <b>Tez, sifatli, arzon va 100% ishonchli!</b>
📞 <b>Bog'lanish / Lichka:</b> @sultonov_mr
""".strip()


class AdBroadcasterService:
    """
    Belgilangan yirik guruhlarga har 1 soatda xavfsiz (Anti-Flood delay bilan)
    reklama xabarlarini tarqatuvchi xizmat.
    """

    def __init__(
        self,
        client: TelegramClient,
        db: Database,
        target_group_names: List[str]
    ):
        self.client = client
        self.db = db
        self.target_group_names = [name.strip().lower() for name in target_group_names if name.strip()]
        self._target_entity_cache = {}

    async def get_ad_text(self) -> str:
        """Bazadagi maxsus reklama matnini olish yoki standart matndan foydalanish."""
        custom_text = await self.db.get_setting("ad_text")
        return custom_text.strip() if custom_text and custom_text.strip() else DEFAULT_AD_TEXT

    async def find_target_dialogs(self) -> List[Tuple[int, str, any]]:
        """Telegram akkauntdagi dialoglar orasidan nishon guruhlarni topish."""
        found_targets = []
        try:
            async for dialog in self.client.iter_dialogs():
                if not (dialog.is_group or dialog.is_channel):
                    continue

                title = (dialog.name or "").strip()
                title_lower = title.lower()
                username = (getattr(dialog.entity, "username", None) or "").lower()

                for target in self.target_group_names:
                    if target in title_lower or target in username:
                        found_targets.append((dialog.id, title, dialog.entity))
                        break
        except Exception as e:
            logger.error(f"⚠️ Guruhlarni izlashda xatolik: {e}")

        return found_targets

    async def broadcast_now(self, force: bool = False) -> Tuple[int, int, str]:
        """
        Nishon guruhlarga reklamani xavfsiz yuborish.
        Qaytaradi: (yuborildi: int, xatoliklar: int, hisobot: str)
        """
        if not force:
            enabled = await self.db.get_setting("ad_enabled", "1")
            if enabled == "0":
                logger.info("ℹ️ Avto-reklama tizimda vaqtincha o'chirilgan (ad_enabled=0).")
                return 0, 0, "Avto-reklama o'chirilgan."

        if not self.client.is_connected():
            logger.warning("⚠️ Userbot ulanmagan, reklama yuborilmadi.")
            return 0, 0, "Userbot ulanmagan."

        targets = await self.find_target_dialogs()
        if not targets:
            logger.warning("⚠️ Reklama yuborish uchun mos guruhlar topilmadi.")
            return 0, 0, "Nishon guruhlar topilmadi."

        ad_text = await self.get_ad_text()
        sent_count = 0
        error_count = 0
        details = []

        logger.info(f"📢 Reklama tarqatish boshlandi ({len(targets)} ta guruhga)...")

        for idx, (chat_id, title, entity) in enumerate(targets, start=1):
            try:
                # Xabarni yuborish
                await self.client.send_message(
                    entity=entity,
                    message=ad_text,
                    parse_mode="html"
                )
                sent_count += 1
                details.append(f"✅ {title}")
                logger.info(f"✅ Reklama yuborildi [{idx}/{len(targets)}]: {title}")

                # Guruhlar orasida Anti-Flood tanaffus (15–25 soniya)
                if idx < len(targets):
                    delay = random.uniform(15.0, 25.0)
                    logger.info(f"⏳ Anti-flood: {delay:.1f}s kutilmoqda...")
                    await asyncio.sleep(delay)

            except errors.FloodWaitError as fwe:
                logger.warning(f"⚠️ FloodWait [{title}]: {fwe.seconds} soniya.")
                error_count += 1
                details.append(f"⏳ FloodWait ({fwe.seconds}s): {title}")
                if fwe.seconds < 35:
                    await asyncio.sleep(fwe.seconds + 2)
                else:
                    break

            except (errors.ChatWriteForbiddenError, errors.UserBannedInChannelError):
                logger.warning(f"⛔️ Guruhda yozish taqiqlangan: {title}")
                error_count += 1
                details.append(f"⛔️ Ruxsat yo'q: {title}")

            except Exception as e:
                logger.error(f"❌ Guruhga reklama yuborishda xatolik ({title}): {e}")
                error_count += 1
                details.append(f"❌ Xatolik: {title}")

        summary = f"Jami: {sent_count} ta guruhga yuborildi, {error_count} ta xatolik.\n" + "\n".join(details)
        logger.info(f"📢 Reklama tarqatish yakunlandi. {summary}")
        return sent_count, error_count, summary
