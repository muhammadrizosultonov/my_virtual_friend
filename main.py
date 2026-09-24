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
from services.sniper_service import LeadSniperService
from services.broadcaster_service import AdBroadcasterService
from bot.admin_bot import create_admin_bot

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

    # 5. Lead Sniper va Ad Broadcaster xizmatlarini initsializatsiya qilish
    sniper_targets = [g.strip() for g in settings.SNIPER_TARGET_GROUPS.split(",") if g.strip()]
    ad_targets = [g.strip() for g in settings.AD_TARGET_GROUPS.split(",") if g.strip()]

    sniper_service = LeadSniperService(
        client=client,
        db=db,
        gemini_service=gemini_service,
        notifier=notifier,
        lead_chat_id=settings.LEAD_DESTINATION_CHAT_ID,
        target_group_names=sniper_targets
    )

    broadcaster = AdBroadcasterService(
        client=client,
        db=db,
        target_group_names=ad_targets
    )

    # 6. Xabarlarni yig'ish va handlerlarni ulash
    register_handlers(
        client=client,
        db=db,
        gemini_service=gemini_service,
        rate_limiter=rate_limiter,
        notifier=notifier,
        sniper_service=sniper_service
    )

    # 7. Schedulerni ishga tushirish (00:00 tahlil, 09:00 to'lov eslatmalari, har 1 soatda avto-reklama)
    scheduler = DailyScheduler(
        db=db,
        gemini_service=gemini_service,
        notifier=notifier,
        broadcaster=broadcaster,
        ad_interval_hours=settings.AD_BROADCAST_INTERVAL_HOURS,
        tz_name=settings.TIMEZONE
    )
    scheduler.start()

    # 8. Aiogram Admin Botni initsializatsiya qilish
    admin_bot, dp = create_admin_bot(
        bot_token=settings.BOT_TOKEN,
        admin_chat_id=settings.MY_CHAT_ID,
        db=db,
        tz_name=settings.TIMEZONE,
        broadcaster=broadcaster
    )

    async def run_userbot():
        logger.info("📱 Telegram Userbot ulanish tekshirilmoqda...")
        await client.start(phone=settings.TELEGRAM_PHONE)
        me = await client.get_me()
        logger.info(
            f"✨ Userbot ulandi! Akkaunt: {me.first_name} {me.last_name or ''} "
            f"(@{me.username or 'NoUsername'}) [ID: {me.id}]"
        )
        logger.info("🟢 Userbot xabarlarni qabul qilmoqda...")
        await client.run_until_disconnected()

    async def run_admin_bot():
        logger.info("🤖 Admin Bot (Aiogram) ishga tushirilmoqda...")
        await admin_bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(admin_bot)

    try:
        # Userbot va Admin Botni bir vaqtda parallel ishlatish
        await asyncio.gather(
            run_userbot(),
            run_admin_bot()
        )

    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
    except Exception as e:
        logger.critical(f"❌ Kutilmagan xatolik yuz berdi: {e}", exc_info=True)
    finally:
        logger.info("🧹 Resurslar tozalanmoqda...")
        scheduler.shutdown()
        if client.is_connected():
            await client.disconnect()
        if admin_bot.session:
            await admin_bot.session.close()
        await notifier.close()
        await db.close()
        logger.info("👋 Tizim to'liq to'xtatildi.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
