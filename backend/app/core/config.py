"""
Application configuration, loaded from environment variables.

Provider selection
------------------
Set PROVIDER=openai   (default) — OpenAI Whisper + GPT.
Set PROVIDER=groq     — Groq free-tier Whisper + Llama (OpenAI-compatible).
Set PROVIDER=gemini   — Google Gemini (free tier via Google AI Studio).
                        • Summarisation: OpenAI-compatible endpoint at
                          https://generativelanguage.googleapis.com/v1beta/openai/
                        • ASR: Gemini File API + multimodal transcription
                          (no Whisper-equivalent; Gemini reads the audio directly).

Free API keys
-------------
  Groq   → https://console.groq.com
  Gemini → https://aistudio.google.com/apikey
"""
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = {"openai", "groq", "gemini"}

# Key name used in the missing-key warning message
_KEY_ENV_VAR = {
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


class Settings:
    # ── Provider ──────────────────────────────────────────────────────────────
    PROVIDER: str = os.getenv("PROVIDER", "openai").lower()

    # ── OpenAI ────────────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "whisper-1")
    SUMMARY_MODEL: str = os.getenv("SUMMARY_MODEL", "gpt-4o-mini")

    # ── Groq (OpenAI-SDK-compatible, free tier) ────────────────────────────────
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_WHISPER_MODEL: str = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")
    GROQ_SUMMARY_MODEL: str = os.getenv("GROQ_SUMMARY_MODEL", "llama-3.3-70b-versatile")

    # ── Gemini (Google AI Studio, free tier) ───────────────────────────────────
    # Summarisation uses the OpenAI-compatible endpoint; ASR uses the File API.
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    GEMINI_ASR_MODEL: str = os.getenv("GEMINI_ASR_MODEL", "gemini-2.0-flash")
    GEMINI_SUMMARY_MODEL: str = os.getenv("GEMINI_SUMMARY_MODEL", "gemini-2.0-flash")

    # ── Shared ────────────────────────────────────────────────────────────────
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./meetings.db")
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "uploads")
    MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "50"))
    ALLOWED_EXTENSIONS: set = {".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg", ".flac"}
    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")

    # ── Computed helpers (read by services) ───────────────────────────────────

    @property
    def active_whisper_model(self) -> str:
        """ASR model name for the active provider (Gemini uses its own model for audio)."""
        if self.PROVIDER == "groq":
            return self.GROQ_WHISPER_MODEL
        if self.PROVIDER == "gemini":
            return self.GEMINI_ASR_MODEL  # used by the Gemini File API path
        return self.WHISPER_MODEL

    @property
    def active_summary_model(self) -> str:
        """Chat/completion model for the active provider."""
        if self.PROVIDER == "groq":
            return self.GROQ_SUMMARY_MODEL
        if self.PROVIDER == "gemini":
            return self.GEMINI_SUMMARY_MODEL
        return self.SUMMARY_MODEL

    @property
    def active_api_key(self) -> str:
        """API key for the active provider."""
        if self.PROVIDER == "groq":
            return self.GROQ_API_KEY
        if self.PROVIDER == "gemini":
            return self.GEMINI_API_KEY
        return self.OPENAI_API_KEY

    @property
    def active_base_url(self) -> str | None:
        """
        OpenAI-SDK base_url override for the active provider.
        None means "use OpenAI default".
        Note: Gemini ASR bypasses the SDK entirely (File API); this is only
        used for chat completions (summariser).
        """
        if self.PROVIDER == "groq":
            return self.GROQ_BASE_URL
        if self.PROVIDER == "gemini":
            return self.GEMINI_BASE_URL
        return None


settings = Settings()

# ── Startup validation ────────────────────────────────────────────────────────
if settings.PROVIDER not in _SUPPORTED_PROVIDERS:
    raise ValueError(
        f"PROVIDER={settings.PROVIDER!r} is not supported. "
        f"Choose one of: {sorted(_SUPPORTED_PROVIDERS)}"
    )

if not settings.active_api_key:
    logger.warning(
        "No API key found for PROVIDER=%s. Set %s in your .env file.",
        settings.PROVIDER,
        _KEY_ENV_VAR.get(settings.PROVIDER, "API_KEY"),
    )

logger.info(
    "AI provider: %s | ASR model: %s | Summary model: %s",
    settings.PROVIDER,
    settings.active_whisper_model,
    settings.active_summary_model,
)

if not os.path.isdir(settings.UPLOAD_DIR):
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
