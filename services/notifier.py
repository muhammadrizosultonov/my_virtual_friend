import html
import logging
import os
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
    """Monitoring botga xabar va media fayllarni jo'natuvchi xizmat."""

    def __init__(self, bot_token: str, my_chat_id: int):
        self.bot_token = bot_token
        self.my_chat_id = my_chat_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        return self._session

    async def send_message(self, text: str, chat_id: Optional[int] = None, parse_mode: str = "HTML") -> bool:
        """Matnni monitoring botga yoki ko'rsatilgan chatga yuborish (kerak bo'lsa qismlarga bo'lib)."""
        session = await self.get_session()
        chunks = split_message(text)
        success = True
        target_chat_id = chat_id if chat_id is not None else self.my_chat_id

        for chunk in chunks:
            payload = {
                "chat_id": target_chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True
            }

            try:
                async with session.post(f"{self.base_url}/sendMessage", json=payload) as resp:
                    if resp.status != 200:
                        err = await resp.text()
                        logger.error(f"❌ Telegram Bot API xatosi [{resp.status}]: {err}")
                        success = False
            except Exception as e:
                logger.error(f"❌ Xabar yuborishda xatolik: {e}", exc_info=True)
                success = False

        if success:
            logger.info(f"✅ Xabar yetkazildi (Chat ID: {target_chat_id})")
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
        """O'chirilgan matnli xabar aniqlanganda monitoring botga signal yuborish."""
        escaped_name = html.escape(sender_name or "Noma'lum")
        escaped_username = f"@{html.escape(sender_username)}" if sender_username else "<i>Mavjud emas</i>"
        escaped_text = html.escape(text or "[Matnsiz xabar]")
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

    async def send_deleted_media_alert(
        self,
        sender_id: int,
        sender_name: str,
        sender_username: Optional[str],
        text: str,
        media_type: str,
        media_path: str,
        sent_at: str,
        deleted_at: Optional[str] = None
    ) -> bool:
        """O'chirilgan rasm/video/fayl aniqlanganda faylning o'zini monitoring botga yuborish."""
        # Agar fayl diskda mavjud bo'lmasa, matnli xabar yuborish
        if not media_path or not os.path.exists(media_path):
            logger.warning(f"Media fayl topilmadi ({media_path}), matnli hisobot yuborilmoqda...")
            return await self.send_deleted_message_alert(
                sender_id=sender_id,
                sender_name=sender_name,
                sender_username=sender_username,
                text=f"[{media_type.upper()}] {text}",
                sent_at=sent_at,
                deleted_at=deleted_at
            )

        escaped_name = html.escape(sender_name or "Noma'lum")
        escaped_username = f"@{html.escape(sender_username)}" if sender_username else "<i>Mavjud emas</i>"
        escaped_caption = html.escape(text) if text and not text.startswith("[") else "<i>Izoh yo'q</i>"
        del_time = deleted_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        media_emojis = {
            "photo": "🖼 RASM",
            "video": "🎥 VIDEO",
            "voice": "🎤 OVOZLI XABAR",
            "video_note": "⭕️ KRUGLYASH",
            "document": "📁 HUJJAT",
            "audio": "🎵 AUDIO",
            "sticker": "🎭 STIKER"
        }
        media_title = media_emojis.get(media_type.lower(), f"📁 {media_type.upper()}")

        caption_html = (
            f"🗑 <b>O'CHIRILGAN {media_title} TUTILDI!</b>\n\n"
            f"👤 <b>Foydalanuvchi:</b>\n"
            f"• <b>Ism:</b> {escaped_name}\n"
            f"• <b>Username:</b> {escaped_username}\n"
            f"• <b>ID:</b> <code>{sender_id}</code>\n\n"
            f"💬 <b>Asl matni (Caption):</b>\n"
            f"<blockquote>{escaped_caption}</blockquote>\n\n"
            f"⏰ <b>Yozilgan vaqti:</b> <code>{sent_at}</code>\n"
            f"🚨 <b>O'chirilgan vaqti:</b> <code>{del_time}</code>"
        )

        return await self._upload_media(media_type, media_path, caption_html)

    async def send_ttl_media_alert(
        self,
        sender_id: int,
        sender_name: str,
        sender_username: Optional[str],
        text: str,
        media_type: str,
        media_path: str,
        ttl_seconds: int
    ) -> bool:
        """O'z-o'zini yo'q qiluvchi (TTL / 1 martalik / Vaqtli) rasm yoki video kelganda darhol botga saqlash."""
        if not media_path or not os.path.exists(media_path):
            return False

        escaped_name = html.escape(sender_name or "Noma'lum")
        escaped_username = f"@{html.escape(sender_username)}" if sender_username else "<i>Mavjud emas</i>"
        escaped_caption = html.escape(text) if text and not text.startswith("[") else "<i>Izoh yo'q</i>"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        caption_html = (
            f"🔥 <b>O'Z-O'ZINI O'CHIRUVCHI (TTL / VIEW-ONCE) MEDIA TUTILDI!</b>\n\n"
            f"👤 <b>Yuboruvchi:</b>\n"
            f"• <b>Ism:</b> {escaped_name}\n"
            f"• <b>Username:</b> {escaped_username}\n"
            f"• <b>ID:</b> <code>{sender_id}</code>\n\n"
            f"⏳ <b>Vaqt chegarasi (Taymer):</b> <code>{ttl_seconds} soniya</code>\n"
            f"💬 <b>Izoh (Caption):</b>\n"
            f"<blockquote>{escaped_caption}</blockquote>\n\n"
            f"⏰ <b>Kelgan vaqti:</b> <code>{now_str}</code>\n"
            f"🛡 <i>Ushbu fayl Telegramda ochilgach o'chib ketishi mumkin, lekin sizda to'liq saqlab qolindi!</i>"
        )

        return await self._upload_media(media_type, media_path, caption_html)

    async def _upload_media(self, media_type: str, media_path: str, caption_html: str) -> bool:
        """Telegram Bot API orqali faylni tegishli endpointga yuklash."""
        endpoint_map = {
            "photo": ("sendPhoto", "photo"),
            "video": ("sendVideo", "video"),
            "voice": ("sendVoice", "voice"),
            "video_note": ("sendVideo", "video"),  # sendVideo caption bilan xavfsiz ishlaydi
            "video_note": ("sendVideo", "video"),
            "audio": ("sendAudio", "audio"),
            "document": ("sendDocument", "document"),
            "sticker": ("sendDocument", "document")
        }

        endpoint, field_name = endpoint_map.get(media_type.lower(), ("sendDocument", "document"))
        api_url = f"{self.base_url}/{endpoint}"

        session = await self.get_session()
        try:
            with open(media_path, "rb") as f:
                file_bytes = f.read()

            filename = os.path.basename(media_path)
            data = aiohttp.FormData()
            data.add_field("chat_id", str(self.my_chat_id))
            data.add_field("caption", caption_html)
            data.add_field("parse_mode", "HTML")
            data.add_field(field_name, file_bytes, filename=filename)

            async with session.post(api_url, data=data) as resp:
                if resp.status == 200:
                    logger.info(f"✅ O'chirilgan media ({media_type}) monitoring botga muvaffaqiyatli jo'natildi!")
                    logger.info(f"✅ Media ({media_type}) monitoring botga muvaffaqiyatli jo'natildi!")
                    return True
                else:
                    err = await resp.text()
                    logger.error(f"❌ Media yuborishda xatolik [{resp.status}]: {err}")
                    # Fallback to text message
                    return await self.send_deleted_message_alert(
                        sender_id=sender_id,
                        sender_name=sender_name,
                        sender_username=sender_username,
                        text=f"[{media_type.upper()} fayli o'chirildi, lekin botga yuklashda xatolik bo'ldi] {text}",
                        sent_at=sent_at,
                        deleted_at=deleted_at
                    )
                    return False
        except Exception as e:
            logger.error(f"❌ Media faylni o'qish yoki yuborishda xatolik: {e}", exc_info=True)
            return await self.send_deleted_message_alert(
                sender_id=sender_id,
                sender_name=sender_name,
                sender_username=sender_username,
                text=f"[{media_type.upper()} fayl] {text}",
                sent_at=sent_at,
                deleted_at=deleted_at
            )
            logger.error(f"❌ Media faylni o'qish yoki yuklashda xatolik: {e}", exc_info=True)
            return False

    async def send_lead_alert(
        self,
        lead_chat_id: int,
        group_title: str,
        sender_id: int,
        sender_name: str,
        sender_username: Optional[str],
        service_type: str,
        task_summary: str,
        budget: Optional[str],
        urgency: str,
        original_text: str,
        msg_link: Optional[str] = None
    ) -> bool:
        """Topilgan yangi mijoz (lead) haqida guruhga va monitoring botga chiroyli xabar yuborish."""
        safe_name = html.escape(sender_name)
        safe_group = html.escape(group_title)
        safe_type = html.escape(service_type)
        safe_summary = html.escape(task_summary)
        safe_budget = html.escape(budget) if budget else "<i>Ko'rsatilmagan (Kelishilgan)</i>"
        safe_urgency = html.escape(urgency)

        username_str = f"@{sender_username}" if sender_username else "<i>Mavjud emas</i>"
        contact_link = f'<a href="tg://user?id={sender_id}">Lichkaga yozish</a>'
        msg_link_html = f'<a href="{msg_link}">Guruhdagi xabarni ochish</a>' if msg_link else "<i>Guruh xabari</i>"

        urgency_badge = "🔴 YUQORI" if urgency == "YUQORI" else ("🟡 O'RTA" if urgency == "O'RTA" else "🟢 ODDIY")

        alert_html = f"""
🎯 <b>YANGI MIJOZ / BUYURTMA TOPILDI!</b>

👥 <b>Guruh:</b> {safe_group}
👤 <b>Mijoz:</b> {safe_name} ({username_str}) [ID: <code>{sender_id}</code>]
📌 <b>Yo'nalish:</b> {safe_type}
📝 <b>Qisqacha talab:</b> {safe_summary}
💰 <b>Byudjet:</b> {safe_budget}
⚡️ <b>Shoshilinchlik:</b> {urgency_badge}

💬 <b>Aloqa:</b> {contact_link} | {username_str}
🔗 <b>Asl xabar:</b> {msg_link_html}

<blockquote>📄 <b>To'liq matn:</b>
{html.escape(original_text[:500])}</blockquote>
""".strip()

        # 1. Mijozlar guruhiga (masalan, -1003080764126) yuborish
        sent_lead = await self.send_message(alert_html, chat_id=lead_chat_id)

        # 2. Agar lead_chat_id monitoring chatdan farq qilsa, admin shaxsiy botiga ham yuborish
        if lead_chat_id != self.my_chat_id:
            await self.send_message(alert_html, chat_id=self.my_chat_id)

        return sent_lead

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("Notifier sessiyasi yopildi.")
