import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

from database.db import Database
from services.gemini_service import GeminiService, LeadAnalysisResult
from services.notifier import MonitoringNotifier
from services.sniper_service import LeadSniperService, quick_is_potential_lead
from services.broadcaster_service import AdBroadcasterService, DEFAULT_AD_TEXT


class TestLeadSniperAndBroadcaster(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_sniper.db")
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_regex_quick_filters(self):
        # 1. Haqiqiy buyurtmalar (O'tishi kerak)
        pos_1 = "Assalomu alaykum! Bizga Telegramda magazin uchun bot yasaydigan dasturchi kerak."
        pos_2 = "Kim python biladi? Katta loyihaga backend dasturchi qidiryapmiz, oylik yaxshi."
        pos_3 = "Veb-sayt ochtirmoqchiman, narxini kelishamiz."
        pos_4 = "Server sozlash kerak vps hetznerda botlarimizni ko'tarishga"
        pos_5 = "Zakaz bor: Click va Payme ulangan savdo boti qilish kerak"

        self.assertTrue(quick_is_potential_lead(pos_1))
        self.assertTrue(quick_is_potential_lead(pos_2))
        self.assertTrue(quick_is_potential_lead(pos_3))
        self.assertTrue(quick_is_potential_lead(pos_4))
        self.assertTrue(quick_is_potential_lead(pos_5))

        # 2. Ish qidiruvchilar / Rezyumelar / Spamlarni elash (O'tmasligi kerak)
        neg_1 = "Assalomu alaykum. Men Python dasturchiman, tajribam 1 yil, ish qidiryapman."
        neg_2 = "Rezyume tashlayapman, junior python backend vakansiya bormi?"
        neg_3 = "Salom hammaga qandaysizlar"
        neg_4 = "STARS NARXLARI YANGILANDI! Arzon telegram yulduzlar"
        neg_5 = "Sifatli va Tezkor IT Xizmatlari! Bog'lanish: @sultonov_mr"

        self.assertFalse(quick_is_potential_lead(neg_1))
        self.assertFalse(quick_is_potential_lead(neg_2))
        self.assertFalse(quick_is_potential_lead(neg_3))
        self.assertFalse(quick_is_potential_lead(neg_4))
        self.assertFalse(quick_is_potential_lead(neg_5))

    async def test_db_leads_and_settings(self):
        # 1. Sozlamalar
        await self.db.set_setting("sniper_enabled", "1")
        self.assertEqual(await self.db.get_setting("sniper_enabled"), "1")
        await self.db.set_setting("sniper_enabled", "0")
        self.assertEqual(await self.db.get_setting("sniper_enabled"), "0")

        # 2. Lead saqlash
        lead_id = await self.db.save_lead(
            chat_id=-100123456,
            chat_title="BIZNES PLUS group",
            sender_id=987654,
            sender_name="Javohir",
            username="javohir_dev",
            message_id=101,
            original_text="Menga tezkor bot kerak",
            service_type="Telegram Bot",
            task_summary="Savdo boti yasatmoqchi",
            budget="$50",
            urgency="YUQORI"
        )
        self.assertGreater(lead_id, 0)
        self.assertTrue(await self.db.is_lead_saved(-100123456, 101))
        self.assertFalse(await self.db.is_lead_saved(-100123456, 102))

        # 3. Hisoblagichlar
        total_leads = await self.db.get_leads_count(today_only=False)
        self.assertEqual(total_leads, 1)

        recent_leads = await self.db.get_recent_leads(5)
        self.assertEqual(len(recent_leads), 1)
        self.assertEqual(recent_leads[0]["sender_name"], "Javohir")

    async def test_lead_sniper_process(self):
        mock_client = MagicMock()
        mock_gemini = MagicMock(spec=GeminiService)
        mock_gemini.analyze_lead_message = AsyncMock(
            return_value=LeadAnalysisResult(
                is_lead=True,
                service_type="Telegram Bot",
                task_summary="Restoran uchun yetkazib berish boti kerak",
                budget="1,000,000 so'm",
                urgency="YUQORI"
            )
        )
        mock_notifier = MagicMock(spec=MonitoringNotifier)
        mock_notifier.send_lead_alert = AsyncMock(return_value=True)

        sniper = LeadSniperService(
            client=mock_client,
            db=self.db,
            gemini_service=mock_gemini,
            notifier=mock_notifier,
            lead_chat_id=-1003080764126,
            target_group_names=["Ortada turb berish", "BIZNES PLUS", "uzbekadmins"]
        )

        # Nishon guruh tekshiruvi
        self.assertTrue(sniper.is_target_chat(-100999, "BIZNES PLUS group", None))
        self.assertTrue(sniper.is_target_chat(-100888, "O'rtada turib berish gruppasi", None))
        self.assertTrue(sniper.is_target_chat(-100777, "uzbekadmins", "uzbekadmins"))
        self.assertFalse(sniper.is_target_chat(-100111, "Begona guruh", None))

        # Mock Event
        mock_event = MagicMock()
        mock_event.is_private = False
        mock_event.out = False
        mock_event.chat_id = -100999
        mock_event.id = 555
        mock_event.raw_text = "Salom, bizga tezkor yetkazib berish boti kerak, yaxshi dasturchi bormi?"

        mock_chat = MagicMock()
        mock_chat.title = "BIZNES PLUS group"
        mock_chat.username = "biznesplus"
        mock_event.get_chat = AsyncMock(return_value=mock_chat)

        mock_sender = MagicMock()
        mock_sender.bot = False
        mock_sender.is_self = False
        mock_sender.id = 112233
        mock_sender.first_name = "Akmal"
        mock_sender.last_name = "Karimov"
        mock_sender.username = "akmal_k"
        mock_event.get_sender = AsyncMock(return_value=mock_sender)

        await sniper.process_incoming_group_message(mock_event)

        # Tekshirish: Gemini tahlili chaqirildi va Notifier ga yuborildi
        self.assertTrue(mock_gemini.analyze_lead_message.called)
        self.assertTrue(mock_notifier.send_lead_alert.called)

        # Bazaga saqlanganligini tekshirish
        is_saved = await self.db.is_lead_saved(-100999, 555)
        self.assertTrue(is_saved)

    async def test_broadcaster_ad_text(self):
        mock_client = MagicMock()
        broadcaster = AdBroadcasterService(
            client=mock_client,
            db=self.db,
            target_group_names=["BIZNES PLUS"]
        )

        # Standart matn
        default_text = await broadcaster.get_ad_text()
        self.assertIn("@sultonov_mr", default_text)
        self.assertIn("Telegram Botlar", default_text)

        # Maxsus matn o'rnatish
        await self.db.set_setting("ad_text", "Yangi maxsus reklama matni @sultonov_mr")
        custom_text = await broadcaster.get_ad_text()
        self.assertEqual(custom_text, "Yangi maxsus reklama matni @sultonov_mr")


if __name__ == "__main__":
    unittest.main()
