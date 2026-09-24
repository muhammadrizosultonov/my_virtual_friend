import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from database.db import Database
from services.gemini_service import AiAnalysisResult
from services.notifier import MonitoringNotifier
from services.rate_limiter import RateLimiter
from handlers.collector_handler import extract_content_summary


class TestUserbotComponents(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_storage.db")
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_sqlite_storage_and_rate_limiter(self):
        rate_limiter = RateLimiter(db=self.db)
        user_id = 123456789

        # Force workday and work hours
        with patch.object(rate_limiter, "is_workday", return_value=True), \
             patch.object(rate_limiter, "is_work_hours", return_value=True):
            # 1. First time on a workday -> should be allowed
            can_reply, reason = await rate_limiter.should_auto_reply(user_id)
            self.assertTrue(can_reply)

            # 2. Second time on same day -> should be blocked by rate limit
            can_reply, reason = await rate_limiter.should_auto_reply(user_id)
            self.assertFalse(can_reply)
            self.assertIn("Bugun allaqachon avto-javob berilgan", reason)

            # 3. Another user -> should be allowed
            other_user = 987654321
            can_reply_other, _ = await rate_limiter.should_auto_reply(other_user)
            self.assertTrue(can_reply_other)

    async def test_weekend_filter(self):
        rate_limiter = RateLimiter(db=self.db)
        user_id = 123456789

        # Force weekend
        with patch.object(rate_limiter, "is_workday", return_value=False), \
             patch.object(rate_limiter, "is_work_hours", return_value=True):
            can_reply, reason = await rate_limiter.should_auto_reply(user_id)
            self.assertFalse(can_reply)
            self.assertIn("Dam olish kuni", reason)

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


if __name__ == "__main__":
    unittest.main()
