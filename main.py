import argparse
import asyncio
import logging
import os
import signal
import sys
from telethon import TelegramClient

from config import settings
from database.db import Database
from handlers.collector_handler import register_handlers
from services.gemini_service import GeminiService
from services.notifier import MonitoringNotifier
from services.rate_limiter import RateLimiter
from services.scheduler_service import DailyScheduler, run_daily_digest

# Loglarni sozlash
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("Userbot")


async def main():
    parser = argparse.ArgumentParser(description="Telegram AI Daily Digest & Auto-Reply Userbot")
    parser.add_argument(
        "--now",
        action="store_true",
        help="Kechani kutmasdan hozirgi yozishmalar asosida darhol test hisobotini monitoring botga yuborish"
    )
    args = parser.parse_args()

    logger.info("🚀 Telegram AI Virtual Assistant & Analytics tizimi ishga tushirilmoqda...")

    # 1. Kerakli papkalarni tekshirish
    os.makedirs("sessions", exist_ok=True)

    # 2. Ma'lumotlar bazasini ishga tushirish
    db = Database(db_path=settings.SQLITE_DB_PATH)
    await db.connect()

    # 3. Servislarni initsializatsiya qilish
    gemini_service = GeminiService(
        api_key=settings.GEMINI_API_KEY,
        model_name=settings.GEMINI_MODEL
    )
    notifier = MonitoringNotifier(
        bot_token=settings.BOT_TOKEN,
        my_chat_id=settings.MY_CHAT_ID
    )
    rate_limiter = RateLimiter(
        db=db,
        tz_name=settings.TIMEZONE
    )

    # Agar --now parametri berilgan bo'lsa: darhol hisobot yaratish va to'xtash
    if args.now:
        logger.info("⚡ Test rejimi: Kunlik tahlil darhol shakllantirilmoqda...")
        await run_daily_digest(
            db=db,
            gemini_service=gemini_service,
            notifier=notifier,
            tz_name=settings.TIMEZONE
        )
        await notifier.close()
        await db.close()
        logger.info("✅ Test hisoboti yakunlandi.")
        return

    # 4. Telethon Userbot mijozini sozlash
    client = TelegramClient(
        session=settings.SESSION_NAME,
        api_id=settings.TELEGRAM_API_ID,
        api_hash=settings.TELEGRAM_API_HASH,
        device_model="Desktop Linux",
        system_version="Linux 6.x",
        app_version="4.16.8",
        lang_code="en",
        system_lang_code="en"
    )

    # 5. Xabarlarni yig'ish va 1 kunda 1 marta avto-javob handlerini ulash
    register_handlers(
        client=client,
        db=db,
        gemini_service=gemini_service,
        rate_limiter=rate_limiter,
        notifier=notifier
    )

    # 6. Tungi 00:00 da hisobot yuboruvchi schedulerni ishga tushirish
    scheduler = DailyScheduler(
        db=db,
        gemini_service=gemini_service,
        notifier=notifier,
        tz_name=settings.TIMEZONE
    )
    scheduler.start()

    # Graceful shutdown hodisalari
    stop_event = asyncio.Event()

    def signal_handler():
        logger.warning("🛑 To'xtatish signali qabul qilindi. Resurslar tozalanmoqda...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    try:
        logger.info("📱 Telegram mijoziga ulanish tekshirilmoqda...")
        await client.start(phone=settings.TELEGRAM_PHONE)
        me = await client.get_me()
        logger.info(
            f"✨ Muvaffaqiyatli ulandi! Akkaunt: {me.first_name} {me.last_name or ''} "
            f"(@{me.username or 'NoUsername'}) [ID: {me.id}]"
        )
        logger.info("🟢 Userbot xabarlarni qabul qilmoqda (1 kunda 1 marta AI avto-javob faol)...")
        logger.info(f"⏰ Har kuni soat 00:00 da ({settings.TIMEZONE}) umumiy kunlik tahlil botga boradi.")

        await client.run_until_disconnected()

    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
    except Exception as e:
        logger.critical(f"❌ Kutilmagan xatolik yuz berdi: {e}", exc_info=True)
    finally:
        logger.info("🧹 Resurslar tozalanmoqda...")
        scheduler.shutdown()
        if client.is_connected():
            await client.disconnect()
        await notifier.close()
        await db.close()
        logger.info("👋 Tizim to'liq to'xtatildi.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
