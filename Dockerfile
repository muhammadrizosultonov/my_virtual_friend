# Python 3.12 yengil (slim) rasmiy obrazi
FROM python:3.12-slim

# Muhit o'zgaruvchilari
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Tashkent

WORKDIR /app

# Tizim paketlarini yangilash (zarur bo'lsa)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Bog'liqliklarni o'rnatish
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Loyiha fayllarini ko'chirish
COPY . .

# Sessiyalar va ma'lumotlar papkasini yaratish
RUN mkdir -p /app/sessions

# Asosiy buyruq
CMD ["python", "main.py"]

