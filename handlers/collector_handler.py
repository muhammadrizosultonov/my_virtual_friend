import logging
from telethon import TelegramClient, events
from telethon.tl.types import User
from database.db import Database
from services.gemini_service import GeminiService
from services.notifier import MonitoringNotifier
from services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Standart ish tartibi va jadvali haqida chiroyli xabar
WORK_SCHEDULE_MESSAGE = """
🏢 <b>Muhammadrizoning ish tartibi va jadvali:</b>

📅 <b>Ish kunlari:</b> Dushanba — Juma 💼
🏖 <b>Dam olish kunlari:</b> Shanba va Yakshanba ✨
⏰ <b>Ish vaqti:</b> 09:00 dan 18:00 gacha ⏳
🍽 <b>Tushlik tanaffusi:</b> 12:00 dan 13:00 gacha ☕️

📩 <i>Xabaringizni yozib qoldirsangiz, ish vaqtida yoki bo'shashi bilanoq sizga javob beradi!</i>
""".strip()


def extract_content_summary(event: events.NewMessage.Event) -> str:
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
    db: Database,
    gemini_service: GeminiService,
    rate_limiter: RateLimiter,
    notifier: MonitoringNotifier
) -> None:
    """Xabarlarni yig'ish, avto-javob, /malumot komandasi va Anti-Delete handleri."""

    # 1. Yangi xabarlarni tutish
    @client.on(events.NewMessage)
    async def handle_private_message(event: events.NewMessage.Event):
        try:
            # Faqat shaxsiy chatlarni tekshirish (guruh va kanallar inkor qilinadi)
            if not event.is_private:
                return

            chat = await event.get_chat()
            if not isinstance(chat, User) or chat.bot:
                return

            chat_id = event.chat_id
            is_outgoing = bool(event.out)
            message_id = event.id
            text = extract_content_summary(event)

            # Kontakt ma'lumotlari
            contact_name = f"{chat.first_name or ''} {chat.last_name or ''}".strip() or "Noma'lum"
            username = chat.username

            # Xabarni bazaga saqlash
            await db.save_message(
                message_id=message_id,
                chat_id=chat_id,
                sender_name=contact_name,
                username=username,
                is_outgoing=is_outgoing,
                text=text
            )

            direction = "📤 Chiquvchi" if is_outgoing else "📥 Kiruvchi"
            logger.info(f"{direction} xabar saqlandi: [{contact_name}]: {text[:50]}")

            # Agar bu kiruvchi xabar bo'lsa
            if not is_outgoing:
                sender = await event.get_sender()
                if not sender or not isinstance(sender, User) or sender.bot or sender.is_self:
                    return

                # A) /malumot yoki /info komandasi tekshiruvi
                clean_text = text.strip().lower()
                if clean_text in ["/malumot", "malumot", "/info", "info"]:
                    logger.info(f"ℹ️ [{contact_name}] uchun /malumot jadvali yuborilmoqda...")
                    await event.reply(WORK_SCHEDULE_MESSAGE, parse_mode="html")
                    return

                # B) Kunlik 1 marta avto-javob tekshiruvi
                can_reply, action_reason = await rate_limiter.should_auto_reply(sender.id)
                if can_reply:
                    logger.info(f"🧠 Gemini AI orqali [{contact_name}] uchun mos avto-javob tayyorlanmoqda...")
                    analysis = await gemini_service.analyze_single_message(
                        message_text=text,
                        sender_name=contact_name
                    )

                    # Chiroyli javobni foydalanuvchiga yuborish
                    await event.reply(analysis.auto_reply_text)
                    await rate_limiter.record_reply(sender.id)
                    logger.info(f"✨ Foydalanuvchiga avto-javob yuborildi ({sender.id})")

                    # Monitoring botga real-time hisobot yuborish
                    await notifier.send_instant_alert(
                        sender_id=sender.id,
                        sender_name=contact_name,
                        sender_username=username,
                        original_text=text,
                        analysis=analysis,
                        action_status="Avto-javob yuborildi ✅",
                        reply_sent=True
                    )
                else:
                    logger.debug(f"Avto-javob yuborilmadi: {action_reason}")

        except Exception as e:
            logger.error(f"❌ Xabarni qayta ishlashda xatolik: {e}", exc_info=True)

    # 2. O'chirilgan xabarlarni tutish (Anti-Delete)
    @client.on(events.MessageDeleted)
    async def handle_deleted_message(event: events.MessageDeleted.Event):
        try:
            deleted_ids = event.deleted_ids
            if not deleted_ids:
                return

            # Bazadan o'chirilgan xabarlarni qidirish
            deleted_messages = await db.get_messages_by_ids(deleted_ids)
            if not deleted_messages:
                return

            for msg in deleted_messages:
                # Faqat kiruvchi xabarlar o'chirilganda monitoring botga xabar berish
                if not msg["is_outgoing"]:
                    logger.warning(
                        f"🚨 O'chirilgan xabar aniqlandi! Foydalanuvchi: {msg['sender_name']} "
                        f"(ID: {msg['chat_id']}): {msg['text'][:50]}"
                    )
                    await notifier.send_deleted_message_alert(
                        sender_id=msg["chat_id"],
                        sender_name=msg["sender_name"],
                        sender_username=msg["username"],
                        text=msg["text"],
                        sent_at=msg["created_at"]
                    )

            # Bazada o'chirilgan deb belgilash
            await db.mark_messages_deleted(deleted_ids)

        except Exception as e:
            logger.error(f"❌ O'chirilgan xabarlarni qayta ishlashda xatolik: {e}", exc_info=True)
