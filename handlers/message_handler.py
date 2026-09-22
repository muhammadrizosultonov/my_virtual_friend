import logging
from telethon import TelegramClient, events
from telethon.tl.types import User
from services.gemini_service import GeminiService
from services.notifier import MonitoringNotifier
from services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


def extract_content_summary(event) -> str:
    """Xabar matnini yoki media turini aniqlab matn ko'rinishida qaytaradi."""
    text = (event.raw_text or "").strip()
    if text:
        return text

    if event.photo:
        return "[Rasm]"
    elif event.voice:
        return "[Ovozli xabar (Voice)]"
    elif event.video_note:
        return "[Videoxabar (Kruglyash)]"
    elif event.video:
        return "[Video]"
    elif event.audio:
        return "[Audio/Musiqa]"
    elif event.sticker:
        return "[Stiker]"
    elif event.document:
        return "[Hujjat/Fayl]"
    elif event.contact:
        return "[Kontakt ma'lumoti]"
    elif event.geo:
        return "[Geolokatsiya]"
    return "[Matnsiz xabar]"


def register_handlers(
    client: TelegramClient,
    gemini_service: GeminiService,
    rate_limiter: RateLimiter,
    notifier: MonitoringNotifier
) -> None:
    """Telethon mijoziga kiruvchi xabarlar uchun event handlerlarni ro'yxatdan o'tkazish."""

    @client.on(events.NewMessage(incoming=True))
    async def incoming_private_message_handler(event: events.NewMessage.Event):
        try:
            # 1. Faqat shaxsiy chatlardan kelgan xabarlarni filtrlash
            if not event.is_private or event.out:
                return

            # 2. Yuboruvchini aniqlash
            sender = await event.get_sender()
            if not sender or not isinstance(sender, User):
                return

            # Botlar va o'zining xabarlarini inkor qilish
            if sender.bot or sender.is_self:
                return

            sender_id = sender.id
            sender_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip() or "Noma'lum"
            sender_username = sender.username

            message_text = extract_content_summary(event)
            logger.info(f"📩 Yangi xabar olindi: [{sender_name} (ID: {sender_id})]: {message_text[:60]}")

            # 3. Gemini AI orqali tahlil qilish
            analysis = await gemini_service.analyze_message(
                message_text=message_text,
                sender_name=sender_name
            )
            logger.info(
                f"🧠 AI Tahlili tayyor: Maqsad='{analysis.sender_intent}', "
                f"Muhimlik='{analysis.urgency}'"
            )

            # 4. Vaqt va kunlik limit nazorati
            can_reply, action_reason = await rate_limiter.should_auto_reply(sender_id)
            reply_sent = False

            if can_reply:
                # Avto-javob yuborish
                await event.reply(analysis.auto_reply_text)
                await rate_limiter.record_reply(sender_id)
                reply_sent = True
                action_status = "Avto-javob yuborildi ✅"
                logger.info(f"📤 Foydalanuvchiga avto-javob yuborildi: {sender_id}")
            else:
                action_status = action_reason
                logger.info(f"ℹ️ Avto-javob yuborilmadi: {action_reason}")

            # 5. Monitoring botga to'liq hisobot yuborish
            await notifier.send_report(
                sender_id=sender_id,
                sender_name=sender_name,
                sender_username=sender_username,
                original_text=message_text,
                analysis=analysis,
                action_status=action_status,
                reply_sent=reply_sent
            )

        except Exception as e:
            logger.error(f"❌ Xabarni qayta ishlashda xatolik: {e}", exc_info=True)

