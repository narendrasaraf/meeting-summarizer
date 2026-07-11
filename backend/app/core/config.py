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
                          (not the same as Google Cloud Speech-to-Text).
Set PROVIDER=azure    — Azure Cognitive Services Speech-to-Text (SDK).
                        Requires AZURE_SPEECH_KEY + AZURE_SPEECH_REGION.
                        Summarisation falls back to OPENAI_API_KEY + SUMMARY_MODEL.
Set PROVIDER=google   — Google Cloud Speech-to-Text v2 (dedicated speech API).
                        Uses Application Default Credentials (service account JSON).
                        Summarisation falls back to OPENAI_API_KEY + SUMMARY_MODEL.

Brief mapping
-------------
The assignment brief names "Google, Azure, OpenAI Whisper, etc." as example integrations:
  • OpenAI Whisper  → PROVIDER=openai  (hosted Whisper-1 via OpenAI API)
  • Groq Whisper    → PROVIDER=groq    (Whisper large-v3 on Groq free tier)
  • Google          → PROVIDER=google  (Google Cloud Speech-to-Text v2)
  • Azure           → PROVIDER=azure   (Azure Cognitive Services Speech SDK)
  • Gemini          → PROVIDER=gemini  (Gemini multimodal audio transcription)

Free API keys
-------------
  Groq   → https://console.groq.com
  Gemini → https://aistudio.google.com/apikey
  Azure  → https://portal.azure.com → Cognitive Services → Speech
  Google → https://console.cloud.google.com/speech (service account + Speech API enabled)
"""
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = {"openai", "groq", "gemini", "azure", "google"}

# Key name used in the missing-key warning message.
# google uses Application Default Credentials (ADC) — no key string required.
_KEY_ENV_VAR = {
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "azure": "AZURE_SPEECH_KEY",
    "google": "(ADC — set GOOGLE_APPLICATION_CREDENTIALS to your service account JSON path)",
}

# Providers that bypass the OpenAI SDK for ASR (no OpenAI client needed for transcription).
_NON_OPENAI_ASR_PROVIDERS = {"gemini", "azure", "google"}

# Providers that use Application Default Credentials instead of an explicit key string.
_ADC_PROVIDERS = {"google"}


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

    # ── Azure Cognitive Services Speech ───────────────────────────────────────
    # ASR uses the azure-cognitiveservices-speech SDK.
    # Summarisation uses the OpenAI-compatible path (OPENAI_API_KEY + SUMMARY_MODEL).
    AZURE_SPEECH_KEY: str = os.getenv("AZURE_SPEECH_KEY", "")
    AZURE_SPEECH_REGION: str = os.getenv("AZURE_SPEECH_REGION", "eastus")

    # ── Google Cloud Speech-to-Text v2 ────────────────────────────────────────
    # ASR uses the google-cloud-speech library with Application Default Credentials.
    # Set GOOGLE_APPLICATION_CREDENTIALS to the path of your service account JSON.
    # Summarisation uses the OpenAI-compatible path (OPENAI_API_KEY + SUMMARY_MODEL).
    GOOGLE_APPLICATION_CREDENTIALS: str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    GOOGLE_STT_LOCATION: str = os.getenv("GOOGLE_STT_LOCATION", "global")

    # ── Shared ────────────────────────────────────────────────────────────────
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./meetings.db")
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "uploads")
    MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "50"))
    ALLOWED_EXTENSIONS: set = {".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg", ".flac"}
    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")

    # ── Computed helpers (read by services) ───────────────────────────────────

    @property
    def active_whisper_model(self) -> str:
        """
        ASR model/engine identifier for the active provider.
        For azure/google this is a human-readable label logged at startup
        (not passed to the SDK directly — the SDK uses its own model selection).
        """
        if self.PROVIDER == "groq":
            return self.GROQ_WHISPER_MODEL
        if self.PROVIDER == "gemini":
            return self.GEMINI_ASR_MODEL
        if self.PROVIDER == "azure":
            return "azure-cognitive-services-speech"
        if self.PROVIDER == "google":
            return "google-cloud-speech-v2-latest_long"
        return self.WHISPER_MODEL

    @property
    def active_summary_model(self) -> str:
        """Chat/completion model for the active provider."""
        if self.PROVIDER == "groq":
            return self.GROQ_SUMMARY_MODEL
        if self.PROVIDER == "gemini":
            return self.GEMINI_SUMMARY_MODEL
        # azure/google fall through to the OpenAI summariser
        return self.SUMMARY_MODEL

    @property
    def active_api_key(self) -> str:
        """
        API key for the active provider (used by the OpenAI SDK and the startup
        key-presence check). Returns "" for google (uses ADC, not a key string).
        """
        if self.PROVIDER == "groq":
            return self.GROQ_API_KEY
        if self.PROVIDER == "gemini":
            return self.GEMINI_API_KEY
        if self.PROVIDER == "azure":
            return self.AZURE_SPEECH_KEY
        if self.PROVIDER == "google":
            return ""  # ADC — no key string; GOOGLE_APPLICATION_CREDENTIALS is the credential
        return self.OPENAI_API_KEY

    @property
    def active_base_url(self) -> str | None:
        """
        OpenAI-SDK base_url override for the active provider.
        None means "use OpenAI default".
        azure/google bypass the OpenAI SDK for ASR entirely; this property is only
        consulted by the summariser (which uses the OpenAI SDK for all providers).
        """
        if self.PROVIDER == "groq":
            return self.GROQ_BASE_URL
        if self.PROVIDER == "gemini":
            return self.GEMINI_BASE_URL
        # azure/google: summariser uses OpenAI default (no base_url override)
        return None


settings = Settings()

# ── Startup validation ────────────────────────────────────────────────────────
if settings.PROVIDER not in _SUPPORTED_PROVIDERS:
    raise ValueError(
        f"PROVIDER={settings.PROVIDER!r} is not supported. "
        f"Choose one of: {sorted(_SUPPORTED_PROVIDERS)}"
    )

# Skip key-presence check for ADC providers (google) — they use a credentials file, not a key.
if settings.PROVIDER not in _ADC_PROVIDERS and not settings.active_api_key:
    logger.warning(
        "No API key found for PROVIDER=%s. Set %s in your .env file.",
        settings.PROVIDER,
        _KEY_ENV_VAR.get(settings.PROVIDER, "API_KEY"),
    )

# For google, warn if GOOGLE_APPLICATION_CREDENTIALS is not set (though ADC may still work
# via instance metadata, a gcloud login, or Workload Identity in GKE).
if settings.PROVIDER == "google" and not settings.GOOGLE_APPLICATION_CREDENTIALS:
    logger.warning(
        "PROVIDER=google but GOOGLE_APPLICATION_CREDENTIALS is not set. "
        "The library will fall back to gcloud ADC / instance metadata. "
        "Set GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json to be explicit."
    )

logger.info(
    "AI provider: %s | ASR model: %s | Summary model: %s",
    settings.PROVIDER,
    settings.active_whisper_model,
    settings.active_summary_model,
)

if not os.path.isdir(settings.UPLOAD_DIR):
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
