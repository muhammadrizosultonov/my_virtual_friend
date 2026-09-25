import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Telegram Userbot
    TELEGRAM_API_ID: int
    TELEGRAM_API_HASH: str
    TELEGRAM_PHONE: Optional[str] = None
    SESSION_NAME: str = "sessions/userbot"

    # Monitoring Bot
    BOT_TOKEN: str
    MY_CHAT_ID: int

    # Gemini AI
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-3.0-flash"

    # Database
    SQLITE_DB_PATH: str = "sessions/messages.db"

    # Lead Sniper & Broadcast
    LEAD_DESTINATION_CHAT_ID: int = -1003080764126
    SNIPER_TARGET_GROUPS: str = "Ortada turb berish,BIZNES PLUS,uzbekadmins,O'rtada turib berish"
    AD_TARGET_GROUPS: str = "Ortada turb berish,BIZNES PLUS,uzbekadmins,O'rtada turib berish"
    AD_BROADCAST_INTERVAL_HOURS: int = 1
    AD_BROADCAST_ENABLED: bool = True
    LEAD_SNIPER_ENABLED: bool = True

    # Settings
    LOG_LEVEL: str = "INFO"
    TIMEZONE: str = "Asia/Tashkent"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
