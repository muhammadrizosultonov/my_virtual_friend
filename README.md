# 📊 Telegram AI Daily Digest & Communication Analytics (Muhammadrizo)

Ushbu tizim — shaxsiy Telegram akkauntingizdagi barcha shaxsiy yozishmalarni (`is_private=True`) kunduzi fonda jimjit bazaga yig'ib boruvchi va har kuni kechasi soat **00:00 da** (yoki buyruq orqali istalgan vaqtda) **Google Gemini 2.5 Flash** orqali "Kunlik Samaradorlik va Muloqot Tahlili" hisobotini tayyorlab, monitoring botingizga jo'natuvchi to'liq asinxron tizimdir.

---

## ⚡ Asosiy Imkoniyatlar

1. **Kunduzi Xabarlarni Yig'ish (Collector):**
   - Faqat shaxsiy chatlardan (`is_private = True`) kelgan va ketgan xabarlarni tutib, SQLite bazasiga yozadi.
   - Guruhlar, kanallar va botlar inkor qilinadi.
   - **Kunduzi 0 token sarfi:** Gemini API kunduzi umuman chaqirilmaydi (resurs tejaladi).

2. **Tungi Matematik Statistika:**
   - Bugun gaplashilgan kontaktlar soni.
   - Jami xabarlar soni (kiruvchi va chiquvchi balansi).
   - Bugungi TOP suhbatdoshlar.
   - Oxirgi 7 kunlik va 30 kunlik (oylik) eng faol kontaktlar dinamikasi.

3. **Google Gemini 2.5 Flash Strategik Tahlili:**
   - 📊 **KUNNING ASOSIY MAVZULARI:** Bugun kimlar bilan nimalar haqida gaplashildi (har bir kontakt bo'yicha 1-2 jumlada qisqa mazmun).
   - 💡 **SAMARADORLIK VA FOYDA:** Ushbu suhbatlar Muhammadrizoga qanchalik foydali bo'ldi? (Foydali takliflar, texnik fikr almashish, chalg'ituvchi bo'sh gaplar yoki vazifalar).
   - ⚠️ **KUTILAYOTGAN VAZIFALAR (Action Items):** Suhbatlar davomida kimdir biror narsa kutayotgan bo'lsa yoki Muhammadrizo va'da qilgan ishlar ("Ertaga tashlab beraman", "Ko'rib chiqaman").
   - 📈 **MULOQOT BALANSI:** Suhbatlarda kim ko'proq tashabbus ko'rsatdi va munosabatlar dinamikasi.

4. **Avtomatik Scheduler (APScheduler):**
   - Har kuni soat `00:00` da (`Asia/Tashkent` vaqti bilan) kechagi kunning to'liq tahlilini avtomatik monitoring botga yuboradi.

---

## 📁 Loyiha Strukturasi

```
my_virtual_friend/
├── config.py                 # Konfiguratsiya (Pydantic Settings)
├── database/
│   ├── __init__.py
│   └── db.py                 # SQLite asinxron bazasi va statistik so'rovlar
├── services/
│   ├── __init__.py
│   ├── gemini_service.py     # Gemini 2.5 Flash strategik tahlil xizmati
│   ├── stats_service.py      # Matematik statistika va stenogramma shakllantirish
│   ├── notifier.py           # Monitoring botga xabar yuborish (avto-chunking)
│   └── scheduler_service.py  # APScheduler (00:00 trigger) va tahlil boshqaruvchisi
├── handlers/
│   ├── __init__.py
│   └── collector_handler.py  # Xabarlarni fonda tutib bazaga yig'uvchi handler
├── main.py                   # Asosiy ishga tushirish fayli
├── login.py                  # QR Kod orqali tezkor avtorizatsiya
├── tests/
│   └── test_analytics.py     # To'liq avtomatlashtirilgan testlar
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

---

## 🐳 Docker orqali Tezkor Ishga Tushirish (Tavsiya etiladi)

### 1-qadam: `.env` faylini sozlang
```bash
cp .env.example .env
nano .env   # Kalitlaringizni kiriting
```

### 2-qadam: Telegram hisobga ulanish (Birinchi marta)
```bash
docker compose run --rm userbot python login.py
```
> Ekranda chiqqan QR kodni telefoningizdagi Telegram orqali skanerlang (*Sozlamalar -> Qurilmalar -> Qurilmani ulash*).

### 3-qadam: Doimiy ishga tushirish (Background)
```bash
docker compose up -d
```

---

## 🛠️ Lokal Muhitda Ishga Tushirish (Docker-siz)

```bash
# 1. Kutubxonalarni o'rnatish
./venv/bin/pip install -r requirements.txt

# 2. QR Kod orqali kirish (birinchi marta)
./venv/bin/python login.py

# 3. Userbotni ishga tushirish (kunduzi yig'adi, kechasi 00:00 da tahlil qiladi)
./venv/bin/python main.py
```

---

## ⚡ Tahlilni darhol (test uchun) ishga tushirish

Kechasi 00:00 bo'lishini kutmasdan, hozirgi to'plangan yozishmalar bo'yicha darhol tahlil tayyorlab botingizga yuborish uchun `--now` buyrug'idan foydalaning:

```bash
# Lokal muhitda:
./venv/bin/python main.py --now

# Docker orqali:
docker compose run --rm userbot python main.py --now
```
