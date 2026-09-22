import html
import logging
from datetime import datetime
from typing import List, Optional
import aiohttp
from services.gemini_service import AiAnalysisResult

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LENGTH = 4000


def split_message(text: str, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> List[str]:
    """Xabarni Telegram belgilari limitidan oshib ketmasligi uchun bo'laklarga ajratish."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    lines = text.split("\n")
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) + 1 > max_length:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            while len(line) > max_length:
                chunks.append(line[:max_length])
                line = line[max_length:]
            current_chunk = line
        else:
            if current_chunk:
                current_chunk += "\n" + line
            else:
                current_chunk = line

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


class MonitoringNotifier:
    """Monitoring botga xabar jo'natuvchi xizmat."""

    def __init__(self, bot_token: str, my_chat_id: int):
        self.bot_token = bot_token
        self.my_chat_id = my_chat_id
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        self._session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        return self._session

    async def send_message(self, text: str) -> bool:
        """Matnni monitoring botga yuborish (kerak bo'lsa qismlarga bo'lib)."""
        session = await self.get_session()
        chunks = split_message(text)
        success = True

        for chunk in chunks:
            payload = {
                "chat_id": self.my_chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }

            try:
                async with session.post(self.api_url, json=payload) as resp:
                    if resp.status != 200:
                        err = await resp.text()
                        logger.error(f"❌ Telegram Bot API xatosi [{resp.status}]: {err}")
                        success = False
            except Exception as e:
                logger.error(f"❌ Hisobot yuborishda xatolik: {e}", exc_info=True)
                success = False

        if success:
            logger.info(f"✅ Monitoring botga xabar yetkazildi (Chat ID: {self.my_chat_id})")
        return success

    async def send_instant_alert(
        self,
        sender_id: int,
        sender_name: str,
        sender_username: Optional[str],
        original_text: str,
        analysis: AiAnalysisResult,
        action_status: str,
        reply_sent: bool = False
    ) -> bool:
        """Kiruvchi xabar va unga berilgan avto-javob bo'yicha darhol hisobot jo'natish."""
        urgency_emoji = {
            "YUQORI": "🔴 YUQORI (Shoshilinch)",
            "O'RTA": "🟡 O'RTA",
            "PAST": "🟢 PAST"
        }.get(analysis.urgency.upper(), f"⚪ {analysis.urgency}")

        escaped_name = html.escape(sender_name or "Noma'lum")
        escaped_username = f"@{html.escape(sender_username)}" if sender_username else "<i>Mavjud emas</i>"
        escaped_intent = html.escape(analysis.sender_intent)
        escaped_summary = html.escape(analysis.summary)
        escaped_original = html.escape(original_text if original_text else "[Matnsiz media/xabar]")
        escaped_reply = html.escape(analysis.auto_reply_text)
        escaped_status = html.escape(action_status)

        report_html = (
            f"🔔 <b>YANGI XABAR VA AVTO-JAVOB</b>\n\n"
            f"👤 <b>Yuboruvchi:</b>\n"
            f"• <b>Ism:</b> {escaped_name}\n"
            f"• <b>Username:</b> {escaped_username}\n"
            f"• <b>ID:</b> <code>{sender_id}</code>\n\n"
            f"📊 <b>AI Tahlili:</b>\n"
            f"• <b>Maqsad:</b> {escaped_intent}\n"
            f"• <b>Muhimlik:</b> {urgency_emoji}\n"
            f"• <b>Qisqa mazmuni:</b> <i>{escaped_summary}</i>\n\n"
            f"💬 <b>Kelgan xabar:</b>\n"
            f"<blockquote>{escaped_original}</blockquote>\n\n"
            f"🤖 <b>Holat:</b> {escaped_status}\n"
        )

        if reply_sent:
            report_html += f"• <b>Yuborilgan javob:</b>\n<blockquote>{escaped_reply}</blockquote>"

        return await self.send_message(report_html)

    async def send_deleted_message_alert(
        self,
        sender_id: int,
        sender_name: str,
        sender_username: Optional[str],
        text: str,
        sent_at: str,
        deleted_at: Optional[str] = None
    ) -> bool:
        """O'chirilgan xabar aniqlanganda monitoring botga signal yuborish."""
        escaped_name = html.escape(sender_name or "Noma'lum")
        escaped_username = f"@{html.escape(sender_username)}" if sender_username else "<i>Mavjud emas</i>"
        escaped_text = html.escape(text or "[Matnsiz media/xabar]")
        del_time = deleted_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        alert_html = (
            f"🗑 <b>O'CHIRILGAN XABAR TUTILDI (Anti-Delete)</b>\n\n"
            f"👤 <b>Foydalanuvchi:</b>\n"
            f"• <b>Ism:</b> {escaped_name}\n"
            f"• <b>Username:</b> {escaped_username}\n"
            f"• <b>ID:</b> <code>{sender_id}</code>\n\n"
            f"💬 <b>O'chirilgan xabar matni:</b>\n"
            f"<blockquote>{escaped_text}</blockquote>\n\n"
            f"⏰ <b>Yozilgan vaqti:</b> <code>{sent_at}</code>\n"
            f"🚨 <b>O'chirilgan vaqti:</b> <code>{del_time}</code>"
        )
        return await self.send_message(alert_html)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("Notifier sessiyasi yopildi.")
