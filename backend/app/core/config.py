"""
Application configuration, loaded from environment variables.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "whisper-1")
    SUMMARY_MODEL: str = os.getenv("SUMMARY_MODEL", "gpt-4o-mini")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./meetings.db")
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "uploads")
    MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "50"))
    ALLOWED_EXTENSIONS: set = {".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg", ".flac"}
    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")


settings = Settings()

if not os.path.isdir(settings.UPLOAD_DIR):
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
