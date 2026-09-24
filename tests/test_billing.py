import asyncio
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from database.db import Database
from services.billing_service import (
    calculate_days_until_payment,
    format_billing_alert_message,
    check_and_send_billing_reminders
)
from services.notifier import MonitoringNotifier


class TestBillingAndClientManagement(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_billing.db")
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_client_and_server_crud(self):
        # 1. Mijoz qo'shish
        client_id = await self.db.add_client(name="Ali Valiyev", contact_info="alivali")
        self.assertGreater(client_id, 0)

        client_2_id = await self.db.add_client(name="TechCorp MCHJ", contact_info="+998901234567")
        self.assertGreater(client_2_id, 0)

        # 2. Mijozlar ro'yxati
        clients = await self.db.get_clients()
        self.assertEqual(len(clients), 2)
        self.assertEqual(clients[0]["name"], "TechCorp MCHJ")
        self.assertEqual(clients[1]["name"], "Ali Valiyev")

        # 3. Serverlar qo'shish
        s1_id = await self.db.add_server(
            client_id=client_id,
            server_name="Hetzner VPS 1 (Web)",
            price="$15/oy",
            payment_day=5
        )
        s2_id = await self.db.add_server(
            client_id=client_id,
            server_name="Postgres DB Node",
            price="200,000 so'm",
            payment_day=20
        )
        self.assertGreater(s1_id, 0)
        self.assertGreater(s2_id, 0)

        # 4. Mijoz serverlarini tekshirish
        ali_servers = await self.db.get_client_servers(client_id)
        self.assertEqual(len(ali_servers), 2)
        self.assertEqual(ali_servers[0]["server_name"], "Hetzner VPS 1 (Web)")
        self.assertEqual(ali_servers[1]["server_name"], "Postgres DB Node")

        # 5. Barcha serverlarni olish
        all_servers = await self.db.get_all_servers_with_clients()
        self.assertEqual(len(all_servers), 2)
        self.assertEqual(all_servers[0]["client_name"], "Ali Valiyev")

        # 6. Bitta serverni o'chirish
        del_s1 = await self.db.delete_server(s1_id)
        self.assertTrue(del_s1)
        ali_servers_after = await self.db.get_client_servers(client_id)
        self.assertEqual(len(ali_servers_after), 1)

        # 7. Mijozni o'chirish (Cascade)
        del_client = await self.db.delete_client(client_id)
        self.assertTrue(del_client)
        clients_after = await self.db.get_clients()
        self.assertEqual(len(clients_after), 1)
        remaining_servers = await self.db.get_client_servers(client_id)
        self.assertEqual(len(remaining_servers), 0)

    def test_calculate_days_until_payment(self):
        # 1. Bugun to'lov kuni (15-sana)
        now_1 = date(2026, 9, 15)
        self.assertEqual(calculate_days_until_payment(now_1, 15), 0)

        # 2. Ertaga to'lov kuni (15-sana, bugun 14)
        now_2 = date(2026, 9, 14)
        self.assertEqual(calculate_days_until_payment(now_2, 15), 1)

        # 3. 3 kun qoldi (18-sana, bugun 15)
        now_3 = date(2026, 9, 15)
        self.assertEqual(calculate_days_until_payment(now_3, 18), 3)

        # 4. Bu oydagi to'lov o'tib ketgan, keyingi oy (bugun 28-sentyabr, to'lov 2-oktyabr)
        # Sentyabr 30 kunlik: 28 -> 29 (1), 30 (2), 1-okt (3), 2-okt (4)
        now_4 = date(2026, 9, 28)
        self.assertEqual(calculate_days_until_payment(now_4, 2), 4)

        # 5. Yil tugashi (30-dekabr, to'lov 2-yanvar)
        # Dekabr 31 kunlik: 30 -> 31 (1), 1-yanv (2), 2-yanv (3) -> 3 kun qoldi
        now_5 = date(2026, 12, 30)
        self.assertEqual(calculate_days_until_payment(now_5, 2), 3)

    def test_format_billing_alert_message(self):
        today = date(2026, 9, 24)
        upcoming_bills = [
            {
                "client_name": "Ali Valiyev",
                "contact_info": "alivali",
                "server_name": "VPS-1",
                "price": "$15",
                "payment_day": 24,
                "days_left": 0
            },
            {
                "client_name": "TechCorp",
                "contact_info": None,
                "server_name": "Dedicated Node",
                "price": "$100",
                "payment_day": 25,
                "days_left": 1
            },
            {
                "client_name": "Bobur",
                "contact_info": "bobur_dev",
                "server_name": "GPU Server",
                "price": "$50",
                "payment_day": 27,
                "days_left": 3
            }
        ]

        text = format_billing_alert_message(upcoming_bills, today)
        self.assertIn("BUGUN TO'LOV KUNI", text)
        self.assertIn("Ali Valiyev", text)
        self.assertIn("ERTAGA TO'LOV KUNI", text)
        self.assertIn("TechCorp", text)
        self.assertIn("TO'LOVGA 3 KUN QOLDI", text)
        self.assertIn("Bobur", text)

    async def test_check_and_send_billing_reminders(self):
        # Add client and server where payment day is today
        now = datetime.now().date()
        client_id = await self.db.add_client(name="Jasur", contact_info="jasur_j")
        await self.db.add_server(
            client_id=client_id,
            server_name="Test Hetzner",
            price="$25",
            payment_day=now.day
        )

        mock_notifier = MagicMock(spec=MonitoringNotifier)
        mock_notifier.send_message = AsyncMock(return_value=True)

        result = await check_and_send_billing_reminders(
            db=self.db,
            notifier=mock_notifier
        )

        self.assertTrue(result)
        self.assertTrue(mock_notifier.send_message.called)
        sent_text = mock_notifier.send_message.call_args[0][0]
        self.assertIn("BUGUN TO'LOV KUNI", sent_text)
        self.assertIn("Jasur", sent_text)


if __name__ == "__main__":
    unittest.main()
