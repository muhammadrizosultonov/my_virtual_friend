import asyncio
import logging
import os
from telethon import TelegramClient, events
from telethon.tl.types import User
from database.db import Database
from services.gemini_service import GeminiService
from services.notifier import MonitoringNotifier
from services.rate_limiter import RateLimiter
from services.downloader_service import (
    save_content_to_saved_messages,
    resolve_message_from_link
)

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


def detect_media_type(event: events.NewMessage.Event) -> tuple[str | None, str, int | None]:
    """
    Xabarning media turini, matnini va TTL (o'z-o'zini o'chirish taymeri) ni aniqlaydi.
    Qaytaradi: (media_turi: str | None, text_summary: str, ttl_seconds: int | None)
    """
    caption = (event.raw_text or "").strip()
    ttl_seconds = None

    if event.media:
        ttl_seconds = getattr(event.media, "ttl_seconds", None)
    if not ttl_seconds and hasattr(event.message, "ttl_period") and event.message.ttl_period:
        ttl_seconds = event.message.ttl_period

    if event.photo:
        return "photo", caption or "[Rasm]", ttl_seconds
    elif event.voice:
        return "voice", caption or "[Ovozli xabar (Voice)]", ttl_seconds
    elif event.video_note:
        return "video_note", caption or "[Videoxabar (Kruglyash)]", ttl_seconds
    elif event.video:
        return "video", caption or "[Video]", ttl_seconds
    elif event.audio:
        return "audio", caption or "[Audio/Musiqa]", ttl_seconds
    elif event.sticker:
        return "sticker", caption or "[Stiker]", ttl_seconds
    elif event.document:
        return "document", caption or "[Hujjat/Fayl]", ttl_seconds
    elif event.contact:
        return None, "[Kontakt ma'lumoti]", ttl_seconds
    elif event.geo:
        return None, "[Geolokatsiya]", ttl_seconds

    return None, caption or "[Matnsiz xabar]", ttl_seconds


def extract_content_summary(event: events.NewMessage.Event) -> str:
    """Xabar matnini yoki media turini aniqlab matn ko'rinishida qaytaradi."""
    _, text, _ = detect_media_type(event)
    return text


def register_handlers(
    client: TelegramClient,
    db: Database,
    gemini_service: GeminiService,
    rate_limiter: RateLimiter,
    notifier: MonitoringNotifier
) -> None:
    """Xabarlarni yig'ish, media/TTL yuklash, avto-javob, /malumot, Media Anti-Delete va Saved Messages Downloader handleri."""

    # 1. Yangi xabarlarni tutish
    @client.on(events.NewMessage)
    async def handle_private_message(event: events.NewMessage.Event):
        try:
            # Faqat shaxsiy chatlarni tekshirish
            if not event.is_private:
                return

            chat = await event.get_chat()
            if not isinstance(chat, User) or chat.bot:
                return

            chat_id = event.chat_id
            is_outgoing = bool(event.out)
            message_id = event.id

            media_type, text, ttl_seconds = detect_media_type(event)
            media_path = None

            # Agar kiruvchi xabarda media bo'lsa, uni xavfsiz keshga yuklab olish (Anti-Delete va TTL uchun)
            if not is_outgoing and media_type:
                try:
                    os.makedirs("sessions/media", exist_ok=True)
                    # 50MB dan kichik mediani yuklab olish
                    file_size = 0
                    if hasattr(event, "file") and event.file and hasattr(event.file, "size") and isinstance(event.file.size, int):
                        file_size = event.file.size

                    if file_size < 50 * 1024 * 1024:
                        dest_prefix = f"sessions/media/msg_{message_id}_{chat_id}"
                        media_path = await event.download_media(file=dest_prefix)
                        if media_path:
                            logger.info(f"📥 Media saqlandi [{media_type}]: {media_path}")
                except Exception as e:
                    logger.warning(f"⚠️ Mediani yuklab olishda xatolik: {e}")

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
                text=text,
                media_type=media_type,
                media_path=media_path
            )

            direction = "📤 Chiquvchi" if is_outgoing else "📥 Kiruvchi"
            logger.info(f"{direction} xabar saqlandi: [{contact_name}]: {text[:50]}")

            # Agar bu kiruvchi xabar bo'lsa
            if not is_outgoing:
                sender = await event.get_sender()
                if not sender or not isinstance(sender, User) or sender.bot or sender.is_self:
                    return

                # 🔥 1. Agar xabar O'Z-O'ZINI O'CHIRUVCHI (TTL / View-Once) bo'lsa -> Darhol botga jo'natish!
                if ttl_seconds and media_path:
                    logger.warning(
                        f"🔥 TTL (View-Once) media aniqlandi! Foydalanuvchi: {contact_name} "
                        f"(TTL: {ttl_seconds}s)"
                    )
                    await notifier.send_ttl_media_alert(
                        sender_id=sender.id,
                        sender_name=contact_name,
                        sender_username=username,
                        text=text,
                        media_type=media_type or "photo",
                        media_path=media_path,
                        ttl_seconds=ttl_seconds
                    )

                # 2. /malumot yoki /info komandasi tekshiruvi
                clean_text = text.strip().lower()
                if clean_text in ["/malumot", "malumot", "/info", "info"]:
                    logger.info(f"ℹ️ [{contact_name}] uchun /malumot jadvali yuborilmoqda...")
                    await event.reply(WORK_SCHEDULE_MESSAGE, parse_mode="html")
                    return

                # 3. Kunlik 1 marta avto-javob tekshiruvi (Albom va poyga holatidan himoyalangan)
                grouped_id = getattr(event, "grouped_id", None) or getattr(event.message, "grouped_id", None)
                can_reply, action_reason = await rate_limiter.should_auto_reply(sender.id, grouped_id=grouped_id)
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
                else:
                    logger.debug(f"Avto-javob yuborilmadi: {action_reason}")

        except Exception as e:
            logger.error(f"❌ Xabarni qayta ishlashda xatolik: {e}", exc_info=True)

    # 2. O'chirilgan xabarlarni tutish (Media & Text Anti-Delete)
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
                if not msg["is_outgoing"]:
                    logger.warning(
                        f"🚨 O'chirilgan xabar aniqlandi! Foydalanuvchi: {msg['sender_name']} "
                        f"(ID: {msg['chat_id']}): {msg['text'][:50]}"
                    )

                    # Agar media bo'lsa -> Rasm/video faylini o'zini jo'natish
                    if msg.get("media_type") and msg.get("media_path"):
                        await notifier.send_deleted_media_alert(
                            sender_id=msg["chat_id"],
                            sender_name=msg["sender_name"],
                            sender_username=msg["username"],
                            text=msg["text"],
                            media_type=msg["media_type"],
                            media_path=msg["media_path"],
                            sent_at=msg["created_at"]
                        )
                    else:
                        # Matnli xabar bo'lsa -> Matnli ogohlantirish
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

    # 3. Yopiq va himoyalangan xabarlarni 'Saved Messages' (Saqlangan xabarlar) ga yuklab olish handleri
    @client.on(events.NewMessage(outgoing=True))
    async def handle_save_command(event: events.NewMessage.Event):
        try:
            raw_text = (event.raw_text or "").strip()
            if not raw_text:
                return

            parts = raw_text.split()
            cmd = parts[0].lower()

            # Trigger komandalari: .save, .dl, .yukla, .get, !save, !dl
            if cmd not in [".save", ".dl", ".yukla", ".get", "!save", "!dl"]:
                return

            reply_msg = await event.get_reply_message()

            # A) Agar biror xabarga reply qilingan bo'lsa
            if reply_msg:
                success = await save_content_to_saved_messages(client, reply_msg)
                if success:
                    try:
                        await event.delete()
                    except Exception:
                        pass
                return

            # B) Agar komanda bilan birga havola (link) yozilgan bo'lsa: .save https://t.me/...
            if len(parts) > 1:
                link = parts[1]
                target_msg = await resolve_message_from_link(client, link)
                if target_msg:
                    success = await save_content_to_saved_messages(client, target_msg)
                    if success:
                        try:
                            await event.delete()
                        except Exception:
                            pass
                    return

            # C) Agar reply ham, link ham bo'lmasa -> Qisqa ogohlantirish
            try:
                await event.edit(
                    "⚠️ <i>Saqlash uchun xabarga reply qiling:</i> <code>.save</code> "
                    "<i>yoki havola kiriting:</i> <code>.save https://t.me/...</code>",
                    parse_mode="html"
                )
                await asyncio.sleep(3)
                await event.delete()
            except Exception:
                pass

        except Exception as e:
            logger.error(f"❌ .save komandasini qayta ishlashda xatolik: {e}", exc_info=True)
