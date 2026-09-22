import asyncio
import os
import sys
import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from config import settings


def display_qr(url: str):
    """Terminalda qulay va aniq QR kodni chop etish."""
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(invert=True)


async def login_via_qr(client: TelegramClient):
    print("\n" + "=" * 60)
    print("📲 1-USUL: QR KOD ORQALI KIRISH (Tavsiya etiladi - Kod kutish shart emas!)")
    print("=" * 60)
    print("\n⏳ Telegram serveridan QR kod olinmoqda...\n")

    qr_login = await client.qr_login()
    
    print("👇 QUYIDAGI QR KODNI TELEGRAM BILAN SKANERLANG:\n")
    display_qr(qr_login.url)

    print("\n📌 QANDAY QILINADI:")
    print("1. Telefoningizda Telegram ilovasini oching.")
    print("2. Sozlamalar (Settings) -> Qurilmalar (Devices) bo'limiga kiring.")
    print("3. 'Qurilmani ulash' (Link Desktop Device / Scan QR) tugmasini bosing.")
    print("4. Yuqoridagi QR kodni kamerangiz orqali skanerlang!")
    print("\n⏳ Skanerlashingiz kutilmoqda (2 daqiqa)...")

    try:
        user = await qr_login.wait(timeout=120)
        return user
    except SessionPasswordNeededError:
        print("\n🔐 Akkauntingizda 2FA (Ikki bosqichli parol) yoqilgan.")
        password = input("🔑 Ikki bosqichli parolingizni (2FA Password) kiriting: ")
        return await client.sign_in(password=password)
    except asyncio.TimeoutError:
        print("\n⚠️ QR kod muddati tugadi.")
        return None


async def login_via_phone(client: TelegramClient):
    print("\n" + "=" * 60)
    print("📞 2-USUL: TELEFON RAQAM VA TASDIQLASH KODI ORQALI KIRISH")
    print("=" * 60)

    phone = settings.TELEGRAM_PHONE
    if not phone:
        phone = input("\n📱 Telefon raqamingizni xalqaro formatda kiriting (+998...): ").strip()

    print(f"\n📡 Telegram serveriga kod so'rovi yuborilmoqda: {phone} ...")
    try:
        await client.send_code_request(phone)
        print("\n📨 DIQQAT: Kod Telegram rasmiy ilovangiz ichidagi chatga yuborildi.")
        code = input("🔢 5 xonali tasdiqlash kodini kiriting: ").strip()

        try:
            return await client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            print("\n🔐 Akkauntingizda 2FA (Ikki bosqichli parol) yoqilgan.")
            password = input("🔑 Ikki bosqichli parolingizni kiriting: ")
            return await client.sign_in(password=password)
    except Exception as e:
        print(f"\n❌ Telefon orqali kirishda xatolik: {e}")
        return None


async def main():
    print("=" * 60)
    print("🚀 TELEGRAM AI USERBOT - AVTORIZATSIYA DASTURI")
    print("=" * 60)

    os.makedirs("sessions", exist_ok=True)

    client = TelegramClient(
        session=settings.SESSION_NAME,
        api_id=settings.TELEGRAM_API_ID,
        api_hash=settings.TELEGRAM_API_HASH,
        device_model="Desktop Linux",
        system_version="Linux 6.x",
        app_version="4.16.8",
        lang_code="en",
        system_lang_code="en"
    )

    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"\n✅ Siz allaqachon muvaffaqiyatli avtorizatsiyadan o'tgansiz!")
        print(f"👤 Akkaunt: {me.first_name} (@{me.username or 'NoUsername'}) [ID: {me.id}]")
        print("\nEndi botni bemalol ishga tushirishingiz mumkin:")
        print("👉 Python: python main.py")
        print("👉 Docker: docker compose up -d\n")
        await client.disconnect()
        return

    print("\nQaysi usulda kirmoqchisiz?")
    print("1️⃣  QR Kod orqali (TAVSIYA ETILADI - tezkor va kodsiz)")
    print("2️⃣  Telefon raqam va kod orqali")

    choice = input("\nTanlovingizni kiriting (1 yoki 2, default: 1): ").strip()
    if choice == "2":
        user = await login_via_phone(client)
    else:
        user = await login_via_qr(client)

    if user:
        me = await client.get_me()
        print("\n" + "=" * 60)
        print("🎉 TABRIKLAYMIZ! Akkauntingiz muvaffaqiyatli ulandi!")
        print(f"👤 Ism: {me.first_name} {me.last_name or ''}")
        print(f"🔗 Username: @{me.username or 'Mavjud emas'}")
        print(f"🆔 Telegram ID: {me.id}")
        print("💾 Sessiya saqlandi.")
        print("=" * 60)
        print("\nEndi botni to'liq ishga tushirishingiz mumkin:")
        print("👉 Python orqali: ./venv/bin/python main.py")
        print("👉 Docker orqali: docker compose up -d\n")
    else:
        print("\n❌ Kirish muvaffaqiyatsiz bo'ldi. Qaytadan urinib ko'ring: ./venv/bin/python login.py")

    await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nTo'xtatildi.")
