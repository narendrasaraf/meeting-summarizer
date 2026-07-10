"""
ASR (Automatic Speech Recognition) service.

Wraps the OpenAI Whisper transcription API. Kept as a thin, isolated
module so the ASR provider can be swapped (Azure Speech, Google STT,
local whisper.cpp, etc.) without touching the rest of the app.
"""
from openai import OpenAI

from app.core.config import settings

_client = OpenAI(api_key=settings.OPENAI_API_KEY)


class TranscriptionError(Exception):
    pass


def transcribe_audio(file_path: str) -> dict:
    """
    Transcribes an audio file using OpenAI's Whisper API.

    Returns:
        dict with keys: text (str), duration (float | None)
    """
    try:
        with open(file_path, "rb") as audio_file:
            result = _client.audio.transcriptions.create(
                model=settings.WHISPER_MODEL,
                file=audio_file,
                response_format="verbose_json",
            )
        return {
            "text": result.text,
            "duration": getattr(result, "duration", None),
        }
    except Exception as exc:  # noqa: BLE001
        raise TranscriptionError(f"Transcription failed: {exc}") from exc
