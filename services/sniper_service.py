import asyncio
import logging
import re
from typing import List, Optional, Set
from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, User

from database.db import Database
from services.gemini_service import GeminiService, LeadAnalysisResult
from services.notifier import MonitoringNotifier

logger = logging.getLogger(__name__)

def normalize_text_for_matching(text: str) -> str:
    """Belgilar va apostroflarni tozalab solishtirish uchun qulay ko'rinishga keltirish."""
    if not text:
        return ""
    # Har xil apostroflarni (o', o‘, o’, o`) bitta qilib tozalash
    t = text.lower()
    t = re.sub(r"['`‘’ʻʼ]", "", t)
    return re.sub(r"\s+", " ", t).strip()


# 1-BOSQICH: Tezkor xotiradagi kalit so'zlar (Regex)
POSITIVE_LEAD_PATTERNS = [
    r"(bot\w*|sayt\w*|veb[- ]?sayt\w*|website\w*|dasturchi\w*|programmer\w*|developer\w*|backend\w*|frontend\w*|fullstack\w*|python\w*|django\w*|fastapi\w*|aiogram\w*|telethon\w*|vps\w*|server\w*|crm\w*|skript\w*|script\w*).*(kerak\w*|qidiryap\w*|qidirmoqda\w*|bormi|yozib ber\w*|yasab ber\w*|qilib ber\w*|tuzib ber\w*|qiladigan|tuzadigan|yaratadigan|loyiha\w*|proyekt\w*|zakaz\w*|buyurtma\w*|ish taklif\w*|ish bor\w*|nujen|nujna|trebuetsya)",
    r"(kerak\w*|qidiryap\w*|qidirmoqda\w*|zakaz bor\w*|buyurtma bor\w*|ish bor\w*|loyiha bor\w*|proyekt bor\w*|nujen|nujna|trebuetsya).*(bot\w*|sayt\w*|veb[- ]?sayt\w*|website\w*|dasturchi\w*|programmer\w*|developer\w*|backend\w*|frontend\w*|python\w*|django\w*|fastapi\w*|aiogram\w*|vps\w*|server\w*|crm\w*)",
    r"(bot yasatmoqchiman|sayt ochtirmoqchiman|bot qildirmoqchiman|sayt qildirmoqchiman|server sozlash kerak|dasturchi qidiryapman|dasturchi kerak|bot kerak|sayt kerak|zakaz beraman)"
]

# 1-BOSQICH SALBIY FILTR: O'zi ish qidirayotganlar yoki keraksiz spamlar
NEGATIVE_PATTERNS = [
    r"\b(rezyume|rezume|cv\b|ish qidiryapman|ish qidirmoqdaman|ish qidirayapman|vakansiya qidiryapman|ish kerak menga|tajribam bor ish|ishga kirmoqchiman|junior|intern)\b",
    r"@sultonov_mr",  # O'zimizning reklama postlarimiz
    r"\b(kripto|signal|pul ishlash|investitsiya|stavka|1xbet|kazino|vaucher|top-up|stars narxlari|gift savdosi)\b"
]

COMPILED_POSITIVE = [re.compile(p, re.IGNORECASE | re.UNICODE | re.DOTALL) for p in POSITIVE_LEAD_PATTERNS]
COMPILED_NEGATIVE = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in NEGATIVE_PATTERNS]


def quick_is_potential_lead(text: str) -> bool:
    """
    0 CPU va $0 xarajat bilan 99% keraksiz xabarlarni elab tashlash.
    Faqat haqiqatan ham buyurtma/dasturchi qidirayotgan matnlarni o'tkazadi.
    """
    if not text or len(text.strip()) < 15:
        return False

    clean_text = text.strip()

    # Salbiy filtr (Rezyume, o'zi ish qidirayotganlar, spam)
    for neg_regex in COMPILED_NEGATIVE:
        if neg_regex.search(clean_text):
            return False

    # Ijobiy kalit so'zlar
    for pos_regex in COMPILED_POSITIVE:
        if pos_regex.search(clean_text):
            return True

    return False


class LeadSniperService:
    """
    Yirik Telegram guruhlardan 24/7 buyurtmachilar va mijozlarni
    3 bosqichli filtr orqali ushlab beruvchi xizmat.
    """

    def __init__(
        self,
        client: TelegramClient,
        db: Database,
        gemini_service: GeminiService,
        notifier: MonitoringNotifier,
        lead_chat_id: int,
        target_group_names: List[str]
    ):
        self.client = client
        self.db = db
        self.gemini_service = gemini_service
        self.notifier = notifier
        self.lead_chat_id = lead_chat_id
        self.target_group_names = [normalize_text_for_matching(name) for name in target_group_names if name.strip()]
        self._target_chat_ids: Set[int] = set()

    def is_target_chat(self, chat_id: int, title: Optional[str], username: Optional[str]) -> bool:
        """Xabar biz kuzatayotgan nishon guruhlardan biridan kelganligini tekshirish."""
        if chat_id in self._target_chat_ids:
            return True

        norm_title = normalize_text_for_matching(title or "")
        norm_username = normalize_text_for_matching(username or "")

        for target in self.target_group_names:
            if not target:
                continue
            # Har qanday umumiy bo'lak (substring yoki to'liq moslik)
            if target in norm_title or target in norm_username or norm_title in target:
                self._target_chat_ids.add(chat_id)
                return True
            # So'zma-so'z tekshirish
            target_words = target.split()
            if len(target_words) > 1 and any(w in norm_title for w in target_words if len(w) > 3):
                self._target_chat_ids.add(chat_id)
                return True

        return False

    async def process_incoming_group_message(self, event: events.NewMessage.Event) -> None:
        """Guruhdagi har bir kiruvchi xabarni tahlil qilish."""
        try:
            # Faqat guruh va superguruh xabarlarini tekshirish
            if event.is_private:
                return

            # O'zimizning xabarlarimiz yoki botlarni tekshirmaslik
            if event.out:
                return

            chat = await event.get_chat()
            chat_title = getattr(chat, "title", None) or ""
            chat_username = getattr(chat, "username", None) or ""

            if not self.is_target_chat(event.chat_id, chat_title, chat_username):
                return

            # Tizimda Lead Sniper yoqilganligini tekshirish
            setting_enabled = await self.db.get_setting("sniper_enabled", "1")
            if setting_enabled == "0":
                return

            text = (event.raw_text or "").strip()
            if not text:
                return

            # 1-BOSQICH: Tezkor Regex Elak
            if not quick_is_potential_lead(text):
                return

            # Xabar avval ko'rib chiqilganmi?
            if await self.db.is_lead_saved(event.chat_id, event.id):
                return

            sender = await event.get_sender()
            if not sender or getattr(sender, "bot", False) or getattr(sender, "is_self", False):
                return

            sender_name = f"{getattr(sender, 'first_name', '') or ''} {getattr(sender, 'last_name', '') or ''}".strip() or "Foydalanuvchi"
            sender_username = getattr(sender, "username", None)
            sender_id = sender.id

            logger.info(f"🎯 Snayper nomzod topdi [{chat_title} | {sender_name}]: {text[:60]}...")

            # 2-BOSQICH: Gemini AI Chuqur Tahlili
            analysis = await self.gemini_service.analyze_lead_message(
                message_text=text,
                group_title=chat_title,
                sender_name=sender_name
            )

            if not analysis or not analysis.is_lead:
                logger.info(f"ℹ️ Gemini tahlili: Bu haqiqiy buyurtmachi emas (is_lead=False).")
                return

            logger.info(f"🔥 YUQORI QIYMATLI MIJOZ TASDIQLANDI! Soha: {analysis.service_type} | Mijoz: {sender_name}")

            # Xabarga havola yasash
            msg_link = None
            if chat_username:
                msg_link = f"https://t.me/{chat_username}/{event.id}"
            else:
                clean_chat_id = str(event.chat_id).replace("-100", "").replace("-", "")
                msg_link = f"https://t.me/c/{clean_chat_id}/{event.id}"

            # 3-BOSQICH: Bazaga saqlash
            await self.db.save_lead(
                chat_id=event.chat_id,
                chat_title=chat_title,
                sender_id=sender_id,
                sender_name=sender_name,
                username=sender_username,
                message_id=event.id,
                original_text=text,
                service_type=analysis.service_type,
                task_summary=analysis.task_summary,
                budget=analysis.budget,
                urgency=analysis.urgency
            )

            # Dinamik guruh ID sini olish
            dest_chat_id_str = await self.db.get_setting("lead_chat_id", str(self.lead_chat_id))
            try:
                dest_chat_id = int(dest_chat_id_str)
            except Exception:
                dest_chat_id = self.lead_chat_id

            # 4-BOSQICH: Yangi mijozlar guruhiga jo'natish
            await self.notifier.send_lead_alert(
                lead_chat_id=dest_chat_id,
                group_title=chat_title,
                sender_id=sender_id,
                sender_name=sender_name,
                sender_username=sender_username,
                service_type=analysis.service_type,
                task_summary=analysis.task_summary,
                budget=analysis.budget,
                urgency=analysis.urgency,
                original_text=text,
                msg_link=msg_link
            )

        except Exception as e:
            logger.error(f"❌ Lead Sniper ishlashida xatolik: {e}", exc_info=True)
