import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Optional
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from database.db import Database
from services.gemini_service import GeminiService
from services.notifier import MonitoringNotifier
from services.stats_service import format_stats_header, prepare_daily_transcript
from services.billing_service import check_and_send_billing_reminders

logger = logging.getLogger(__name__)


async def run_daily_digest(
    db: Database,
    gemini_service: GeminiService,
    notifier: MonitoringNotifier,
    target_date: Optional[datetime] = None,
    tz_name: str = "Asia/Tashkent"
) -> bool:
    """
    Kunlik tahlilni hisoblab, Gemini tahlilidan o'tkazib, monitoring botga yuborish.
    target_date berilmasa, bugungi kun uchun hisoblaydi (yoki yarim tunda kechagi kun uchun).
    """
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.UTC

    now = datetime.now(tz)
    if target_date is None:
        target_date = now

    date_str = target_date.strftime("%Y-%m-%d")
    logger.info(f"📊 Kunlik hisobot tayyorlanmoqda ({date_str})...")

    start_dt = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)

    # 1. Ma'lumotlarni bazadan olish
    messages = await db.get_messages_for_period(start_dt, end_dt)
    stats_today = await db.get_today_overview_stats(start_dt, end_dt)
    stats_7days = await db.get_top_contacts_for_days(7)
    stats_month = await db.get_top_contacts_for_days(30)

    # 2. Matematik sarlavha qismini shakllantirish
    header_text = format_stats_header(stats_today, stats_7days, stats_month, date_str)

    # 3. AI Tahlili
    if not messages:
        full_report = (
            f"{header_text}\n\n"
            f"ℹ️ <i>Bugun shaxsiy yozishmalar mavjud bo'lmaganligi sababli AI tahlili talab qilinmadi.</i>"
        )
    else:
        transcript = prepare_daily_transcript(messages)
        ai_analysis = await gemini_service.generate_daily_report(transcript)
        full_report = (
            f"{header_text}\n\n"
            f"🧠 <b>GOOGLE GEMINI STRATEGIK TAHLILI:</b>\n\n"
            f"{ai_analysis}"
        )

    # 4. Monitoring botga yuborish
    success = await notifier.send_message(full_report)
    return success


class DailyScheduler:
    """Kunlik tahlil, to'lov eslatmalari va avto-reklama vazifalarini boshqaruvchi scheduler."""

    def __init__(
        self,
        db: Database,
        gemini_service: GeminiService,
        notifier: MonitoringNotifier,
        broadcaster: Optional[any] = None,
        ad_interval_hours: int = 1,
        tz_name: str = "Asia/Tashkent"
    ):
        self.db = db
        self.gemini_service = gemini_service
        self.notifier = notifier
        self.broadcaster = broadcaster
        self.ad_interval_hours = ad_interval_hours
        self.tz_name = tz_name
        self.scheduler = AsyncIOScheduler()

    def start(self) -> None:
        try:
            tz = pytz.timezone(self.tz_name)
        except Exception:
            tz = pytz.UTC

        # Har kuni soat 00:00 da kechagi kun xulosasini tayyorlash
        async def midnight_job():
            logger.info("🌙 Yarim tun: Kunlik tahlil avtomatik ishga tushdi...")
            yesterday = datetime.now(tz) - timedelta(days=1)
            await run_daily_digest(
                db=self.db,
                gemini_service=self.gemini_service,
                notifier=self.notifier,
                target_date=yesterday,
                tz_name=self.tz_name
            )
            # 3 kundan oshgan eski media fayllarni diskdan tozalash
            await self.db.cleanup_old_media(days=3)

        self.scheduler.add_job(
            midnight_job,
            trigger=CronTrigger(hour=0, minute=0, timezone=tz),
            id="daily_digest_job",
            replace_existing=True
        )

        # Har kuni soat 09:00 da oylik server to'lovlari eslatmasi (3 kunlik ogohlantirish)
        async def morning_billing_job():
            logger.info("☀️ Ertalabki to'lov eslatmalari tekshiruvi ishga tushdi...")
            await check_and_send_billing_reminders(
                db=self.db,
                notifier=self.notifier,
                tz_name=self.tz_name
            )

        self.scheduler.add_job(
            morning_billing_job,
            trigger=CronTrigger(hour=9, minute=0, timezone=tz),
            id="morning_billing_job",
            replace_existing=True
        )

        # Har 1 soatda nishon guruhlarga avto-reklama tarqatish
        if self.broadcaster:
            async def hourly_ad_broadcast_job():
                logger.info("📢 Har 1 soatlik avto-reklama tekshiruvi ishga tushdi...")
                await self.broadcaster.broadcast_now(force=False)

            self.scheduler.add_job(
                hourly_ad_broadcast_job,
                trigger=IntervalTrigger(hours=self.ad_interval_hours),
                id="hourly_ad_broadcast_job",
                replace_existing=True
            )

        self.scheduler.start()
        logger.info(
            f"⏰ Scheduler ishga tushdi: 00:00 da tahlil, 09:00 da to'lov eslatmalari, "
            f"har {self.ad_interval_hours} soatda avto-reklama ({self.tz_name})"
        )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler to'xtatildi.")

