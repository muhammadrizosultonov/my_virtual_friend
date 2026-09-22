import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from database.storage import SQLiteStorage, init_storage
from services.gemini_service import AiAnalysisResult
from services.notifier import MonitoringNotifier
from services.rate_limiter import RateLimiter
from handlers.message_handler import extract_content_summary


class TestUserbotComponents(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_storage.db")
        self.storage = SQLiteStorage(self.db_path)
        await self.storage.initialize()

    async def asyncTearDown(self):
        await self.storage.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_sqlite_storage_and_rate_limiter(self):
        rate_limiter = RateLimiter(storage=self.storage)
        user_id = 123456789

        # Force workday
        with patch.object(rate_limiter, "is_workday", return_value=True):
            # 1. First time on a workday -> should be allowed
            can_reply, reason = await rate_limiter.should_auto_reply(user_id)
            self.assertTrue(can_reply)
            self.assertIn("Avto-javob yuborildi", reason)

            # Record reply
            await rate_limiter.record_reply(user_id)

            # 2. Second time on same day -> should be blocked by rate limit
            can_reply, reason = await rate_limiter.should_auto_reply(user_id)
            self.assertFalse(can_reply)
            self.assertIn("Bugun allaqachon avto-javob berilgan", reason)

            # 3. Another user -> should be allowed
            other_user = 987654321
            can_reply_other, _ = await rate_limiter.should_auto_reply(other_user)
            self.assertTrue(can_reply_other)

    async def test_weekend_filter(self):
        rate_limiter = RateLimiter(storage=self.storage)
        user_id = 123456789

        # Force weekend
        with patch.object(rate_limiter, "is_workday", return_value=False):
            can_reply, reason = await rate_limiter.should_auto_reply(user_id)
            self.assertFalse(can_reply)
            self.assertIn("Dam olish kuni", reason)

    async def test_storage_fallback(self):
        # In case redis URL is invalid, should fallback to SQLite seamlessly
        storage = await init_storage(
            redis_url="redis://127.0.0.1:9999/0",  # non-existent redis port
            sqlite_path=os.path.join(self.test_dir, "fallback.db")
        )
        self.assertIsInstance(storage, SQLiteStorage)
        await storage.close()

    def test_pydantic_ai_model(self):
        data = {
            "sender_intent": "Ish taklifi",
            "urgency": "YUQORI",
            "summary": "Mijoz yangi loyiha bo'yicha hamkorlik taklif qilmoqda.",
            "auto_reply_text": "Assalomu alaykum! Men Muhammadrizoning yordamchisiman..."
        }
        model = AiAnalysisResult(**data)
        self.assertEqual(model.sender_intent, "Ish taklifi")
        self.assertEqual(model.urgency, "YUQORI")

    async def test_notifier_html_formatting(self):
        notifier = MonitoringNotifier(bot_token="123456:TEST_TOKEN", my_chat_id=12345678)
        analysis = AiAnalysisResult(
            sender_intent="Do'stona suhbat",
            urgency="PAST",
            summary="Do'st salom yo'lladi.",
            auto_reply_text="Assalomu alaykum! Men Muhammadrizoning yordamchisiman."
        )

    async def test_handler_full_flow(self):
        # Setup mock dependencies
        mock_client = MagicMock()
        registered_handlers = []

        def on_decorator(event_builder):
            def wrapper(func):
                registered_handlers.append(func)
                return func
            return wrapper

        mock_client.on.side_effect = on_decorator

        mock_gemini = MagicMock()
        mock_gemini.analyze_message = AsyncMock(return_value=AiAnalysisResult(
            sender_intent="Ish taklifi",
            urgency="YUQORI",
            summary="Loyiha bo'yicha taklif.",
            auto_reply_text="Assalomu alaykum! Men yordamchiman..."
        ))

        rate_limiter = RateLimiter(storage=self.storage)
        mock_notifier = MagicMock()
        mock_notifier.send_report = AsyncMock(return_value=True)

        # Register handlers
        from handlers.message_handler import register_handlers
        register_handlers(mock_client, mock_gemini, rate_limiter, mock_notifier)

        self.assertEqual(len(registered_handlers), 1)
        handler_func = registered_handlers[0]

        # Simulate incoming private message
        mock_event = MagicMock()
        mock_event.is_private = True
        mock_event.out = False
        mock_event.raw_text = "Salom, yangi loyiha bor edi."
        mock_event.reply = AsyncMock()

        from telethon.tl.types import User
        mock_sender = MagicMock(spec=User)
        mock_sender.id = 55555
        mock_sender.first_name = "Botir"
        mock_sender.last_name = "Karimov"
        mock_sender.username = "botir_k"
        mock_sender.bot = False
        mock_sender.is_self = False

        mock_event.get_sender = AsyncMock(return_value=mock_sender)

        # Force workday
        with patch.object(rate_limiter, "is_workday", return_value=True):
            await handler_func(mock_event)

            # Verification
            self.assertTrue(mock_gemini.analyze_message.called)
            self.assertTrue(mock_event.reply.called)
            self.assertEqual(mock_event.reply.call_args[0][0], "Assalomu alaykum! Men yordamchiman...")
            self.assertTrue(mock_notifier.send_report.called)
            self.assertEqual(mock_notifier.send_report.call_args[1]["sender_id"], 55555)
            self.assertTrue(mock_notifier.send_report.call_args[1]["reply_sent"])


if __name__ == "__main__":
    unittest.main()



