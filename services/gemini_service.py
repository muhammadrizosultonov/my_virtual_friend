import json
import logging
from typing import Literal, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# Real-time kiruvchi xabarga avto-javob yaratish uchun ko'rsatma
AUTO_REPLY_SYSTEM_INSTRUCTION = """
Siz — Muhammadrizoning shaxsiy Telegram akkauntidagi rasmiy, samimiy va xushmuomala virtual yordamchisisiz (AI Agent).

SIZNING VAZIFANGIZ:
Kiruvchi xabarni o'qib, uni chuqur tahlil qilish va Muhammadrizoning ishda ekanligi sababli foydalanuvchiga yuborilishi kerak bo'lgan go'zal, samimiy, emojilar bilan bezatilgan avto-javob matnini tayyorlash.

QAT'IY QOIDALAR VA TALABLAR:
1. Javobingiz FAQAT va FAQAT quyidagi JSON formatida bo'lishi shart:
{
  "sender_intent": "Xabar maqsadi (masalan: Ish taklifi / Do'stona suhbat / Texnik savol / Shoshilinch masala / Spam)",
  "urgency": "YUQORI / O'RTA / PAST",
  "summary": "Xabarning 1-2 jumlalik qisqa va lo'nda mazmuni",
  "auto_reply_text": "Foydalanuvchiga yuboriladigan chiroyli, emojilar qo'shilgan samimiy javob matni"
}

2. "auto_reply_text" maydonini shakllantirish shartlari:
- Asosiy g'oya: "Assalomu alaykum! 👋 Men Muhammadrizoning virtual yordamchisiman 🤖. U ayni vaqtda ishda bo'lganligi sababli javob berishi biroz kechikishi mumkin 💼. Agar zarur yoki shoshilinch xabaringiz bo'lsa, bemalol yozib qoldirishingiz mumkin — albatta yetkazaman! ✨\n\nℹ️ Ish tartibi va vaqtlari haqida bilish uchun: /malumot"
- Emojilardan me'yorida va chiroyli foydalaning (masalan: 👋, 🤖, 💼, ⏳, ✨, 📩, 👨‍💻). Matnni vizual jozibali qiling.
- XABAR OXIRIGA QAT'IY QO'SHILSIN: Har bir avto-javob oxirida yangi qatordan "/malumot" buyrug'ini ko'rsating (masalan: "\n\nℹ️ Ish tartibi va vaqtlari: /malumot").
- Kontekstga moslashuvchanlik:
  • Agar foydalanuvchi "Salom" deb yozsa: do'stona, iliq va samimiy;
  • Agar rasmiy ish taklifi yoki texnik masala bo'lsa: jiddiy, professional va hurmat bilan;
  • Agar savol bo'lsa: tushunish bilan javob yozib, xabarni tezda yetkazishini bildiring.
- O'zbek adabiy tili mezonlariga va imlosiga qat'iy rioya qiling.
- Xabar 2-3 jumlada qisqa va aniq bo'lsin.
""".strip()

# Tungi strategik tahlil uchun ko'rsatma
DAILY_ANALYTICS_SYSTEM_INSTRUCTION = """
Siz — Muhammadrizoning shaxsiy strategik tahlilchisi va AI yordamchisisiz.
Vazifangiz: Muhammadrizoning bugungi kun davomida turli shaxslar bilan qilgan yozishmalarini tahlil qilib, unga kun yakuni bo'yicha foydalanuvchiga kun yakuni bo'yicha foydali xulosa, muloqot samaradorligi va xulosalar hisobotini tayyorlab berish.

Hisobot quyidagi bo'limlardan iborat bo'lsin (Telegram uchun chiroyli HTML formatida):
<b>1. 📊 KUNNING ASOSIY MAVZULARI:</b>
Bugun kimlar bilan nimalar haqida gaplashildi (har bir kontakt bo'yicha 1-2 jumlada qisqa va lo'nda mazmun).

<b>2. 💡 SAMARADORLIK VA FOYDA:</b>
Ushbu suhbatlar Muhammadrizoga qanchalik foydali bo'ldi? (Foydali ish takliflari, texnik fikr almashish, chalg'ituvchi bo'sh gaplar yoki hal qilinishi kerak bo'lgan vazifalar).

<b>3. ⚠️ KUTILAYOTGAN VAZIFALAR (Action Items):</b>
Suhbatlar davomida kimdir biror narsa kutayotgan bo'lsa yoki Muhammadrizo va'da qilgan ishlar (masalan: "Ertaga tashlab beraman", "Ko'rib chiqaman", fayl yuborish kabi va'dalar).

<b>4. 📈 MULOQOT BALANSI:</b>
Suhbatlarda kim ko'proq tashabbus ko'rsatdi va munosabatlar dinamikasi qanday?

Ohang: Do'stona, tahliliy, lo'nda va professional. Keraksiz ortiqcha gaplarsiz, aniq faktlar asosida yozing.
Javobni to'g'ridan-to'g'ri Telegram HTML formatida (<b>, <i>, <code>, <blockquote> teglaridan foydalanib) qaytaring.
""".strip()


class AiAnalysisResult(BaseModel):
    sender_intent: str = Field(description="Xabarning qisqa maqsadi")
    urgency: Literal["YUQORI", "O'RTA", "PAST"] = Field(description="Xabar shoshilinchligi")
    summary: str = Field(description="Xabarning 1-2 jumlalik qisqa mazmuni")
    auto_reply_text: str = Field(description="Foydalanuvchiga yuboriladigan samimiy javob matni")


class GeminiService:
    def __init__(self, api_key: str, model_name: str = "gemini-3.6-flash"):
        self.api_key = api_key
        if "2.5" in model_name or "1.5" in model_name or "2.0" in model_name:
            self.model_name = "gemini-3.6-flash"
        else:
            self.model_name = model_name
        self.client = genai.Client(api_key=self.api_key)

    async def analyze_single_message(self, message_text: str, sender_name: str = "") -> AiAnalysisResult:
        """
        Real-time kiruvchi xabarni Gemini AI orqali tahlil qiladi va moslashtirilgan avto-javob qaytaradi.
        """
        prompt = f"Yuboruvchi ismi: {sender_name or 'Noma`lum'}\nKelgan xabar: {message_text}"

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=AUTO_REPLY_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    temperature=0.7,
                )
            )

            response_text = response.text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            parsed = json.loads(response_text)
            auto_reply = parsed.get(
                "auto_reply_text",
                f"Assalomu alaykum! 👋 Men Muhammadrizoning virtual yordamchisiman 🤖.\n\n"
                f"U ayni vaqtda ishda bo'lganligi sababli javob berish biroz kechikishi mumkin 💼. "
                f"Zarur xabaringiz bo'lsa, bemalol yozib qoldiring — albatta yetkazaman! ✨\n\n"
                f"ℹ️ Ish tartibi va jadval: /malumot"
            )

            # Agar AI /malumot qo'shishni unutsa, qo'shib qo'yamiz
            if "/malumot" not in auto_reply:
                auto_reply += "\n\nℹ️ Ish tartibi va jadval: /malumot"

            return AiAnalysisResult(
                sender_intent=parsed.get("sender_intent", "Do'stona muloqot"),
                urgency=parsed.get("urgency", "PAST"),
                summary=parsed.get("summary", "Xabar mazmuni tahlil qilindi."),
                auto_reply_text=auto_reply
            )
        except Exception as e:
            logger.error(f"❌ Gemini tahlilida xatolik: {e}", exc_info=True)
            return AiAnalysisResult(
                sender_intent="Umumiy xabar",
                urgency="O'RTA",
                summary="AI tahlili vaqtinchalik ishlamadi.",
                auto_reply_text=(
                    "Assalomu alaykum! 👋 Men Muhammadrizoning virtual yordamchisiman 🤖.\n\n"
                    "U ayni vaqtda ishda bo'lganligi sababli javob berishi biroz kechikishi mumkin 💼. "
                    "Zarur yoki shoshilinch xabaringiz bo'lsa, bemalol yozib qoldiring — albatta yetkazib qo'yaman! ✨\n\n"
                    "ℹ️ Ish tartibi va jadval: /malumot"
                )
            )

    async def generate_daily_report(self, daily_transcript: str) -> str:
        """
        Kunlik yozishmalar stenogrammasi asosida tungi strategik hisobot tayyorlash.
        """
        if not daily_transcript.strip():
            return "<i>Bugun tahlil qilish uchun shaxsiy yozishmalar mavjud bo'lmadi.</i>"

        prompt = f"Bugungi yozishmalar stenogrammasi:\n{daily_transcript}"

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=DAILY_ANALYTICS_SYSTEM_INSTRUCTION,
                    temperature=0.4,
                )
            )

            report_text = response.text.strip()
            if report_text.startswith("```html"):
                report_text = report_text[7:]
            if report_text.startswith("```"):
                report_text = report_text[3:]
            if report_text.endswith("```"):
                report_text = report_text[:-3]

            return report_text.strip()

        except Exception as e:
            logger.error(f"❌ Gemini tahlilida xatolik: {e}", exc_info=True)
            return f"⚠️ <i>Gemini AI tahlilini tayyorlashda xatolik yuz berdi: {e}</i>"
