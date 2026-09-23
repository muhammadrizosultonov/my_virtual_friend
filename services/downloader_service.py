import asyncio
import logging
import os
import re
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.types import Message

logger = logging.getLogger(__name__)

TELEGRAM_LINK_PATTERN = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:c/)?([a-zA-Z0-9_]+)/([0-9]+)(?:/([0-9]+))?"
)


async def resolve_message_from_link(client: TelegramClient, link: str) -> Message | None:
    """Telegram post havolasidan (ochiq yoki yopiq) xabarni MTProto orqali oladi."""
    match = TELEGRAM_LINK_PATTERN.search(link)
    if not match:
        return None

    part1, part2, part3 = match.groups()
    try:
        if part1.isdigit():
            # Yopiq kanal/guruh havolasi: t.me/c/1234567890/456 yoki topic: t.me/c/1234567890/10/456
            channel_id = int(f"-100{part1}")
            msg_id = int(part3) if part3 else int(part2)
            entity = await client.get_input_entity(channel_id)
            return await client.get_messages(entity, ids=msg_id)
        else:
            # Ochiq kanal havolasi: t.me/username/123
            channel_username = part1
            msg_id = int(part2)
            entity = await client.get_input_entity(channel_username)
            return await client.get_messages(entity, ids=msg_id)
    except Exception as e:
        logger.error(f"❌ Havoladan xabarni olishda xatolik [{link}]: {e}")
        return None


async def save_content_to_saved_messages(
    client: TelegramClient,
    target_msg: Message,
    temp_dir: str = "sessions/temp_downloads"
) -> bool:
    """
    Har qanday himoyalangan (restricted / noforwards) xabar yoki mediani
    Saqlangan xabarlar (Saved Messages / 'me') ga to'liq formatda yuboradi.
    """
    try:
        caption = target_msg.raw_text or ""
        entities = target_msg.entities

        # Agar xabarda media (rasm, video, audio, voice, hujjat va h.k.) bo'lsa
        if target_msg.media:
            os.makedirs(temp_dir, exist_ok=True)
            temp_file_prefix = os.path.join(
                temp_dir,
                f"dl_{target_msg.id}_{int(datetime.now().timestamp())}"
            )

            # MTProto orqali cheklovlarni chetlab o'tib yuklab olish
            downloaded_path = await client.download_media(target_msg, file=temp_file_prefix)

            if downloaded_path and os.path.exists(downloaded_path):
                is_voice = bool(getattr(target_msg, "voice", False))
                is_video_note = bool(getattr(target_msg, "video_note", False))
                is_video = bool(getattr(target_msg, "video", False))

                # Saqlangan xabarlarga yuborish
                await client.send_file(
                    'me',
                    file=downloaded_path,
                    caption=caption,
                    formatting_entities=entities,
                    voice_note=is_voice,
                    video_note=is_video_note,
                    supports_streaming=is_video
                )

                # Vaqtinchalik faylni o'chirish
                try:
                    os.remove(downloaded_path)
                except Exception:
                    pass

                logger.info(f"✅ Media muvaffaqiyatli 'Saved Messages'ga saqlandi! (ID: {target_msg.id})")
                return True
            else:
                logger.warning(f"⚠️ Mediani yuklab bo'lmadi: (ID: {target_msg.id})")
                return False

        # Agar faqat matnli xabar bo'lsa
        elif caption:
            await client.send_message(
                'me',
                message=caption,
                formatting_entities=entities
            )
            logger.info(f"✅ Matnli xabar 'Saved Messages'ga saqlandi! (ID: {target_msg.id})")
            return True

        return False

    except Exception as e:
        logger.error(f"❌ Xabarni 'Saved Messages'ga saqlashda xatolik: {e}", exc_info=True)
        return False
