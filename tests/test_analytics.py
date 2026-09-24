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
from services.downloader_service import (
    save_content_to_saved_messages,
    resolve_message_from_link,
    TELEGRAM_LINK_PATTERN
)


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

    async def test_media_anti_delete(self):
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
        mock_notifier.send_deleted_media_alert = AsyncMock(return_value=True)
        rate_limiter = RateLimiter(db=self.db)

        register_handlers(
            client=mock_client,
            db=self.db,
            gemini_service=mock_gemini,
            rate_limiter=rate_limiter,
            notifier=mock_notifier
        )

        # Create dummy media file
        media_file = os.path.join(self.test_dir, "test_photo.jpg")
        with open(media_file, "wb") as f:
            f.write(b"fake photo bytes")

        # Save an incoming media message to DB
        await self.db.save_message(
            message_id=999,
            chat_id=444,
            sender_name="Dilshod",
            username="dilshod_d",
            is_outgoing=False,
            text="Bu rasm matni",
            media_type="photo",
            media_path=media_file
        )

        # Find MessageDeleted handler
        deleted_handler = next(h for b, h in registered_handlers if b == events.MessageDeleted)

        # Simulate deletion event
        mock_del_event = MagicMock()
        mock_del_event.deleted_ids = [999]

        await deleted_handler(mock_del_event)

        # Verification: send_deleted_media_alert must be called
        self.assertTrue(mock_notifier.send_deleted_media_alert.called)
        call_kwargs = mock_notifier.send_deleted_media_alert.call_args[1]
        self.assertEqual(call_kwargs["sender_id"], 444)
        self.assertEqual(call_kwargs["sender_name"], "Dilshod")
        self.assertEqual(call_kwargs["media_type"], "photo")
        self.assertEqual(call_kwargs["media_path"], media_file)

    async def test_ttl_media_detection(self):
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
        mock_notifier.send_ttl_media_alert = AsyncMock(return_value=True)
        rate_limiter = RateLimiter(db=self.db)

        register_handlers(
            client=mock_client,
            db=self.db,
            gemini_service=mock_gemini,
            rate_limiter=rate_limiter,
            notifier=mock_notifier
        )

        new_msg_handler = next(h for b, h in registered_handlers if b == events.NewMessage)

        # Create dummy media file
        media_file = os.path.join(self.test_dir, "ttl_photo.jpg")
        with open(media_file, "wb") as f:
            f.write(b"ttl secret photo bytes")

        # Simulate incoming TTL message
        mock_event = MagicMock()
        mock_event.is_private = True
        mock_event.out = False
        mock_event.id = 888
        mock_event.chat_id = 999
        mock_event.photo = True
        mock_event.voice = None
        mock_event.video_note = None
        mock_event.video = None
        mock_event.audio = None
        mock_event.sticker = None
        mock_event.document = None
        mock_event.contact = None
        mock_event.geo = None
        mock_event.raw_text = "5 soniyada o'chuvchi maxfiy rasm"
        mock_event.download_media = AsyncMock(return_value=media_file)

        mock_media = MagicMock()
        mock_media.ttl_seconds = 5
        mock_event.media = mock_media

        mock_user = MagicMock(spec=User)
        mock_user.id = 999
        mock_user.first_name = "Ayyor"
        mock_user.last_name = "User"
        mock_user.username = "ayyor_user"
        mock_user.bot = False
        mock_user.is_self = False
        mock_event.get_chat = AsyncMock(return_value=mock_user)
        mock_event.get_sender = AsyncMock(return_value=mock_user)

        with patch.object(rate_limiter, "is_workday", return_value=False):
            await new_msg_handler(mock_event)

        # Verification: TTL alert must be sent immediately
        self.assertTrue(mock_notifier.send_ttl_media_alert.called)
        call_kwargs = mock_notifier.send_ttl_media_alert.call_args[1]
        self.assertEqual(call_kwargs["sender_id"], 999)
        self.assertEqual(call_kwargs["ttl_seconds"], 5)
        self.assertEqual(call_kwargs["media_type"], "photo")
        self.assertEqual(call_kwargs["media_path"], media_file)

    async def test_cleanup_old_media(self):
        media_file = os.path.join(self.test_dir, "old_video.mp4")
        with open(media_file, "wb") as f:
            f.write(b"video bytes")

        five_days_ago = datetime.now() - timedelta(days=5)
        await self.db.save_message(
            message_id=777,
            chat_id=555,
            sender_name="User",
            username=None,
            is_outgoing=False,
            text="[Video]",
            media_type="video",
            media_path=media_file,
            created_at=five_days_ago
        )

        self.assertTrue(os.path.exists(media_file))
        cleaned = await self.db.cleanup_old_media(days=3)
        self.assertEqual(cleaned, 1)
        self.assertFalse(os.path.exists(media_file))

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

    async def test_save_content_to_saved_messages_media(self):
        mock_client = MagicMock()
        mock_client.send_file = AsyncMock(return_value=True)

        temp_media = os.path.join(self.test_dir, "test_save.jpg")
        with open(temp_media, "wb") as f:
            f.write(b"fake photo data")

        mock_client.download_media = AsyncMock(return_value=temp_media)

        mock_target = MagicMock()
        mock_target.id = 12345
        mock_target.media = MagicMock()
        mock_target.raw_text = "Himoyalangan rasm"
        mock_target.entities = []
        mock_target.voice = False
        mock_target.video_note = False
        mock_target.video = False

        result = await save_content_to_saved_messages(
            client=mock_client,
            target_msg=mock_target,
            temp_dir=os.path.join(self.test_dir, "downloads")
        )

        self.assertTrue(result)
        self.assertTrue(mock_client.send_file.called)
        call_args, call_kwargs = mock_client.send_file.call_args
        self.assertEqual(call_args[0], 'me')
        self.assertEqual(call_kwargs["caption"], "Himoyalangan rasm")
        # Temporary file should be deleted
        self.assertFalse(os.path.exists(temp_media))

    async def test_save_content_to_saved_messages_text_only(self):
        mock_client = MagicMock()
        mock_client.send_message = AsyncMock(return_value=True)

        mock_target = MagicMock()
        mock_target.id = 54321
        mock_target.media = None
        mock_target.raw_text = "Himoyalangan maxfiy matn"
        mock_target.entities = []

        result = await save_content_to_saved_messages(
            client=mock_client,
            target_msg=mock_target
        )

        self.assertTrue(result)
        self.assertTrue(mock_client.send_message.called)
        call_args, call_kwargs = mock_client.send_message.call_args
        self.assertEqual(call_args[0], 'me')
        self.assertEqual(call_kwargs["message"], "Himoyalangan maxfiy matn")

    async def test_handle_save_command_reply(self):
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

        # Find outgoing save handler
        save_handler = None
        for b, h in registered_handlers:
            if hasattr(b, "outgoing") and b.outgoing:
                save_handler = h
                break

        self.assertIsNotNone(save_handler)

        mock_reply_msg = MagicMock()
        mock_reply_msg.id = 777
        mock_reply_msg.media = None
        mock_reply_msg.raw_text = "Guruhdagi muhim post"
        mock_reply_msg.entities = []

        mock_event = MagicMock()
        mock_event.raw_text = ".save"
        mock_event.get_reply_message = AsyncMock(return_value=mock_reply_msg)
        mock_event.delete = AsyncMock()

        mock_client.send_message = AsyncMock(return_value=True)

        await save_handler(mock_event)

        self.assertTrue(mock_client.send_message.called)
        self.assertTrue(mock_event.delete.called)

    async def test_album_and_parallel_messages_rate_limiting(self):
        rate_limiter = RateLimiter(db=self.db)
        with patch.object(rate_limiter, "is_workday", return_value=True), \
             patch.object(rate_limiter, "is_work_hours", return_value=True):
            # 3 photos in the same album (same grouped_id)
            album_id = 987654321
            can_reply_1, _ = await rate_limiter.should_auto_reply(user_id=123, grouped_id=album_id)
            can_reply_2, _ = await rate_limiter.should_auto_reply(user_id=123, grouped_id=album_id)
            can_reply_3, _ = await rate_limiter.should_auto_reply(user_id=123, grouped_id=album_id)

            self.assertTrue(can_reply_1)
            self.assertFalse(can_reply_2)
            self.assertFalse(can_reply_3)

    async def test_work_hours_rate_limiting(self):
        rate_limiter = RateLimiter(db=self.db)
        
        # 1. Ish vaqtida (masalan 14:00)
        with patch.object(rate_limiter, "is_workday", return_value=True), \
             patch.object(rate_limiter, "is_work_hours", return_value=True):
            can_reply, reason = await rate_limiter.should_auto_reply(user_id=999)
            self.assertTrue(can_reply)

        # 2. Ish vaqtidan tashqarida (masalan 20:00 yoki 07:00)
        with patch.object(rate_limiter, "is_workday", return_value=True), \
             patch.object(rate_limiter, "is_work_hours", return_value=False):
            can_reply, reason = await rate_limiter.should_auto_reply(user_id=888)
            self.assertFalse(can_reply)
            self.assertIn("Ish vaqtidan tashqari", reason)

        # 3. Dam olish kunida (Shanba/Yakshanba)
        with patch.object(rate_limiter, "is_workday", return_value=False), \
             patch.object(rate_limiter, "is_work_hours", return_value=True):
            can_reply, reason = await rate_limiter.should_auto_reply(user_id=777)
            self.assertFalse(can_reply)
            self.assertIn("Dam olish kuni", reason)


if __name__ == "__main__":
    unittest.main()
