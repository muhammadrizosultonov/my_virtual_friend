import asyncio
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from telethon import events
from telethon.tl.types import User

from database.db import Database
from handlers.collector_handler import register_handlers, extract_content_summary, WORK_SCHEDULE_MESSAGE
from services.gemini_service import GeminiService, AiAnalysisResult
from services.notifier import MonitoringNotifier, split_message
from services.rate_limiter import RateLimiter
from services.scheduler_service import run_daily_digest
from services.stats_service import format_stats_header, prepare_daily_transcript


class TestDailyAnalyticsAndFeatures(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_messages.db")
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_database_message_saving_and_stats(self):
        now = datetime.now()
        yesterday = now - timedelta(days=1)
        four_days_ago = now - timedelta(days=4)

        # 1. Bugungi xabarlar
        await self.db.save_message(
            message_id=1,
            chat_id=101,
            sender_name="Ali Valiyev",
            username="alivali",
            is_outgoing=False,
            text="Salom Muhammadrizo, loyiha tayyormi?",
            created_at=now
        )
        await self.db.save_message(
            message_id=2,
            chat_id=101,
            sender_name="Ali Valiyev",
            username="alivali",
            is_outgoing=True,
            text="Salom Ali, ha 90% qismi bitdi.",
            created_at=now
        )
        await self.db.save_message(
            message_id=3,
            chat_id=102,
            sender_name="Vali Toshmatov",
            username="valit",
            is_outgoing=False,
            text="Ertaga uchrashamizmi?",
            created_at=now
        )

        # 2. O'tmishdagi xabarlar (7 kunlik statistika uchun)
        await self.db.save_message(
            message_id=4,
            chat_id=101,
            sender_name="Ali Valiyev",
            username="alivali",
            is_outgoing=False,
            text="Oldingi xabar",
            created_at=four_days_ago
        )

        # 3. Bugungi statistika tekshiruvi
        start_today = now.replace(hour=0, minute=0, second=0)
        end_today = now.replace(hour=23, minute=59, second=59)

        stats = await self.db.get_today_overview_stats(start_today, end_today)
        self.assertEqual(stats["total_messages"], 3)
        self.assertEqual(stats["incoming_count"], 2)
        self.assertEqual(stats["outgoing_count"], 1)
        self.assertEqual(stats["unique_contacts"], 2)
        self.assertEqual(len(stats["top_contacts_today"]), 2)
        self.assertEqual(stats["top_contacts_today"][0]["chat_id"], 101)
        self.assertEqual(stats["top_contacts_today"][0]["message_count"], 2)

        # 4. 7 kunlik statistika tekshiruvi
        top_7days = await self.db.get_top_contacts_for_days(7)
        self.assertEqual(top_7days[0]["chat_id"], 101)
        self.assertEqual(top_7days[0]["total_count"], 3)

    async def test_malumot_command(self):
        mock_client = MagicMock()
        registered_handlers = []

        def on_decorator(event_builder):
            def wrapper(func):
                registered_handlers.append((event_builder, func))
                return func
            return wrapper

        mock_client.on.side_effect = on_decorator

        mock_gemini = MagicMock(spec=GeminiService)
        mock_notifier = MagicMock(spec=MonitoringNotifier)
        rate_limiter = RateLimiter(db=self.db)

        register_handlers(
            client=mock_client,
            db=self.db,
            gemini_service=mock_gemini,
            rate_limiter=rate_limiter,
            notifier=mock_notifier
        )

        # Find NewMessage handler
        new_msg_handler = next(h for b, h in registered_handlers if b == events.NewMessage)

        # Simulate /malumot message
        mock_event = MagicMock()
        mock_event.is_private = True
        mock_event.out = False
        mock_event.id = 601
        mock_event.chat_id = 777
        mock_event.raw_text = "/malumot"
        mock_event.reply = AsyncMock()

        mock_user = MagicMock(spec=User)
        mock_user.id = 777
        mock_user.first_name = "Olim"
        mock_user.last_name = None
        mock_user.username = "olim_7"
        mock_user.bot = False
        mock_user.is_self = False
        mock_event.get_chat = AsyncMock(return_value=mock_user)
        mock_event.get_sender = AsyncMock(return_value=mock_user)

        await new_msg_handler(mock_event)

        # Must reply with work schedule message
        self.assertTrue(mock_event.reply.called)
        self.assertEqual(mock_event.reply.call_args[0][0], WORK_SCHEDULE_MESSAGE)
        # Should not call gemini for info command
        self.assertFalse(mock_gemini.analyze_single_message.called)

    async def test_anti_delete_detection(self):
        mock_client = MagicMock()
        registered_handlers = []

        def on_decorator(event_builder):
            def wrapper(func):
                registered_handlers.append((event_builder, func))
                return func
            return wrapper

        mock_client.on.side_effect = on_decorator

        mock_gemini = MagicMock(spec=GeminiService)
        mock_notifier = MagicMock(spec=MonitoringNotifier)
        mock_notifier.send_deleted_message_alert = AsyncMock(return_value=True)
        rate_limiter = RateLimiter(db=self.db)

        register_handlers(
            client=mock_client,
            db=self.db,
            gemini_service=mock_gemini,
            rate_limiter=rate_limiter,
            notifier=mock_notifier
        )

        # Save an incoming message to DB first
        await self.db.save_message(
            message_id=909,
            chat_id=333,
            sender_name="Bekzod",
            username="bekzod_b",
            is_outgoing=False,
            text="Bu maxfiy o'chiriladigan xabar edi"
        )

        # Find MessageDeleted handler
        deleted_handler = next(h for b, h in registered_handlers if b == events.MessageDeleted)

        # Simulate deletion event
        mock_del_event = MagicMock()
        mock_del_event.deleted_ids = [909]

        await deleted_handler(mock_del_event)

        # Verification: Notifier must be called
        self.assertTrue(mock_notifier.send_deleted_message_alert.called)
        call_kwargs = mock_notifier.send_deleted_message_alert.call_args[1]
        self.assertEqual(call_kwargs["sender_id"], 333)
        self.assertEqual(call_kwargs["sender_name"], "Bekzod")
        self.assertEqual(call_kwargs["text"], "Bu maxfiy o'chiriladigan xabar edi")

        # In DB, message must be marked as deleted
        found = await self.db.get_messages_by_ids([909])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["is_deleted"], 1)

    async def test_run_daily_digest_flow(self):
        now = datetime.now()
        await self.db.save_message(
            message_id=1,
            chat_id=888,
            sender_name="Jasur",
            username="jasur_dev",
            is_outgoing=False,
            text="Ertaga kodni deploy qilamiz.",
            created_at=now
        )

        mock_gemini = MagicMock(spec=GeminiService)
        mock_gemini.generate_daily_report = AsyncMock(
            return_value="<b>1. ASOSIY MAVZULAR:</b> Deploy masalasi muhokama qilindi."
        )

        mock_notifier = MagicMock(spec=MonitoringNotifier)
        mock_notifier.send_message = AsyncMock(return_value=True)

        success = await run_daily_digest(
            db=self.db,
            gemini_service=mock_gemini,
            notifier=mock_notifier,
            target_date=now
        )

        self.assertTrue(success)
        self.assertTrue(mock_gemini.generate_daily_report.called)
        self.assertTrue(mock_notifier.send_message.called)


if __name__ == "__main__":
    unittest.main()
