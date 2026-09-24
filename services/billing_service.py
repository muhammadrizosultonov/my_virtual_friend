import calendar
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional
import pytz
from database.db import Database
from services.notifier import MonitoringNotifier

logger = logging.getLogger(__name__)


def calculate_days_until_payment(now_date: date, payment_day: int) -> int:
    """
    To'lov kunigacha qancha kun qolganini aniq hisoblaydi (oy uzunliklari va yil o'tishlarini hisobga olgan holda).
    """
    year = now_date.year
    month = now_date.month

    # Joriy oydagi oxirgi kun soni (masalan 28, 30 yoki 31)
    _, max_days_current_month = calendar.monthrange(year, month)
    target_day_current = min(payment_day, max_days_current_month)
    target_date_current = date(year, month, target_day_current)

    if target_date_current >= now_date:
        return (target_date_current - now_date).days
    else:
        # Bu oydagi to'lov o'tib ketgan, keyingi oyni hisoblaymiz
        if month == 12:
            next_year = year + 1
            next_month = 1
        else:
            next_year = year
            next_month = month + 1

        _, max_days_next_month = calendar.monthrange(next_year, next_month)
        target_day_next = min(payment_day, max_days_next_month)
        target_date_next = date(next_year, next_month, target_day_next)

        return (target_date_next - now_date).days


def format_billing_alert_message(upcoming_bills: List[Dict[str, Any]], target_date: date) -> str:
    """Yaqinlashayotgan to'lovlar haqida chiroyli Telegram HTML xabar tayyorlaydi."""
    if not upcoming_bills:
        return ""

    lines = [
        "💳 <b>OYLIK SERVER TO'LOVLARI ESLATMASI</b>",
        f"📅 <i>Sana: {target_date.strftime('%d.%m.%Y')}</i>\n"
    ]

    # Guruhlash: Bugun, 1 kun, 2 kun, 3 kun qolganlar
    today_bills = [b for b in upcoming_bills if b["days_left"] == 0]
    tomorrow_bills = [b for b in upcoming_bills if b["days_left"] == 1]
    two_days_bills = [b for b in upcoming_bills if b["days_left"] == 2]
    three_days_bills = [b for b in upcoming_bills if b["days_left"] == 3]

    if today_bills:
        lines.append("🚨 <b>BUGUN TO'LOV KUNI BO'LGAN SERVERLAR:</b>")
        for b in today_bills:
            contact = f" (@{b['contact_info']})" if b.get("contact_info") else ""
            lines.append(
                f"• 👤 <b>{b['client_name']}</b>{contact}\n"
                f"  🖥 <b>Server:</b> {b['server_name']} | 💰 <b>Narxi:</b> {b['price']}\n"
                f"  🗓 <i>Har oyning {b['payment_day']}-sanasi</i>"
            )
        lines.append("")

    if tomorrow_bills:
        lines.append("⚠️ <b>ERTAGA TO'LOV KUNI (1 kun qoldi):</b>")
        for b in tomorrow_bills:
            contact = f" (@{b['contact_info']})" if b.get("contact_info") else ""
            lines.append(
                f"• 👤 <b>{b['client_name']}</b>{contact}\n"
                f"  🖥 <b>Server:</b> {b['server_name']} | 💰 <b>Narxi:</b> {b['price']}"
            )
        lines.append("")

    if two_days_bills:
        lines.append("⏳ <b>TO'LOVGA 2 KUN QOLDI:</b>")
        for b in two_days_bills:
            lines.append(
                f"• 👤 <b>{b['client_name']}</b> — 🖥 {b['server_name']} (💰 {b['price']})"
            )
        lines.append("")

    if three_days_bills:
        lines.append("⏳ <b>TO'LOVGA 3 KUN QOLDI:</b>")
        for b in three_days_bills:
            lines.append(
                f"• 👤 <b>{b['client_name']}</b> — 🖥 {b['server_name']} (💰 {b['price']})"
            )
        lines.append("")

    lines.append("💡 <i>Iltimos, mijozlar bilan bog'lanib to'lovlarni o'z vaqtida qabul qiling!</i>")
    return "\n".join(lines).strip()


async def check_and_send_billing_reminders(
    db: Database,
    notifier: MonitoringNotifier,
    tz_name: str = "Asia/Tashkent"
) -> bool:
    """
    Barcha serverlarni tekshirib, to'loviga 3 kun, 2 kun, 1 kun qolgan va
    bugun to'lov kuni bo'lgan serverlar haqida monitoring botga xabar jo'natadi.
    """
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.UTC

    now_date = datetime.now(tz).date()
    servers = await db.get_all_servers_with_clients()

    if not servers:
        logger.info("ℹ️ Tizimda ro'yxatdan o'tgan serverlar mavjud emas.")
        return False

    upcoming_bills = []
    for s in servers:
        days_left = calculate_days_until_payment(now_date, s["payment_day"])
        if 0 <= days_left <= 3:
            s_copy = dict(s)
            s_copy["days_left"] = days_left
            upcoming_bills.append(s_copy)

    if not upcoming_bills:
        logger.info(f"ℹ️ {now_date}: Yaqin 3 kunda to'lovi keladigan serverlar topilmadi.")
        return False

    upcoming_bills.sort(key=lambda x: x["days_left"])
    alert_text = format_billing_alert_message(upcoming_bills, now_date)

    if alert_text:
        sent = await notifier.send_message(alert_text, parse_mode="HTML")
        if sent:
            logger.info(f"✅ Oylik to'lov eslatmalari monitoring botga yuborildi ({len(upcoming_bills)} ta server)")
            return True
        else:
            logger.error("❌ Oylik to'lov eslatmalarini botga jo'natishda xatolik yuz berdi.")
            return False

    return False
