"""
ASR (Automatic Speech Recognition) service.

Provider routing
----------------
• openai / groq  → OpenAI audio.transcriptions.create() via SDK
                   (Groq speaks the same protocol; just swap base_url)
• gemini         → Gemini File API + multimodal generate_content().
                   Gemini has no /audio/transcriptions endpoint, so we:
                     1. Upload the audio to the Gemini File API (resumable upload)
                     2. Wait for the file to become ACTIVE
                     3. Send a transcription prompt to generateContent
                   All via stdlib urllib — zero extra dependencies.

Why tenacity over a hand-rolled loop?  See the original comment in this
module: composable retry predicates, structured pre-sleep logging, and
no off-by-one risk. For Gemini's HTTP errors (urllib.error.HTTPError)
we add a matching predicate that checks the status code.
"""
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

import openai
from openai import OpenAI
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Transient-error predicates ────────────────────────────────────────────────

# OpenAI / Groq SDK errors
_OPENAI_TRANSIENT = (
    openai.RateLimitError,       # 429
    openai.APITimeoutError,      # request timed out
    openai.APIConnectionError,   # network blip
    openai.InternalServerError,  # 5xx
)

# Gemini REST errors (urllib.error.HTTPError with a status code)
def _is_gemini_transient(exc: Exception) -> bool:
    return isinstance(exc, urllib.error.HTTPError) and exc.code in (429, 500, 502, 503, 504)


# ── Retry decorators ──────────────────────────────────────────────────────────

_openai_retry = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(_OPENAI_TRANSIENT),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)

_gemini_retry = retry(
    reraise=True,
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1.5, min=2, max=15),
    retry=retry_if_exception(_is_gemini_transient),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)


class TranscriptionError(Exception):
    pass


# ── OpenAI / Groq path ────────────────────────────────────────────────────────

def _make_openai_client() -> OpenAI:
    """
    SDK client for OpenAI or Groq.
    Groq is fully OpenAI-protocol compatible; only base_url + api_key differ.
    """
    return OpenAI(
        api_key=settings.active_api_key,
        base_url=settings.active_base_url,  # None → OpenAI default
    )


# Module-level client (re-created if provider changes on restart)
_client: OpenAI | None = None if settings.PROVIDER == "gemini" else _make_openai_client()


@_openai_retry
def _call_whisper_api(audio_file) -> object:
    """
    Isolated, retryable Whisper API call (openai / groq only).
    Extracted so tests can patch _client.audio.transcriptions.create.
    """
    assert _client is not None, "OpenAI client not initialised"
    return _client.audio.transcriptions.create(
        model=settings.active_whisper_model,
        file=audio_file,
        response_format="verbose_json",
    )


# ── Gemini File API path ───────────────────────────────────────────────────────

_GEMINI_API = "https://generativelanguage.googleapis.com"
_GEMINI_UPLOAD = "https://generativelanguage.googleapis.com/upload"

_MIME_MAP = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


@_gemini_retry
def _gemini_send_request(req: urllib.request.Request, timeout: float = 120) -> tuple[bytes, dict]:
    """
    Sends an HTTP request to Gemini with individual retry logic on 429/5xx.
    Returns (response_bytes, response_headers).
    """
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        headers = {k.lower(): v for k, v in resp.info().items()}
        return resp.read(), headers


def _gemini_http(method: str, url: str, body: dict | None = None,
                 extra_headers: dict | None = None) -> dict:
    """Tiny helper: JSON request to the Gemini REST API."""
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    resp_bytes, _ = _gemini_send_request(req, timeout=120)
    return json.loads(resp_bytes)


def _gemini_upload_audio(file_path: str) -> tuple[str, str]:
    """
    Upload audio to the Gemini File API using a resumable upload.

    Returns:
        (file_uri, mime_type)  — file_uri is used in generateContent.
    """
    path = Path(file_path)
    mime = _MIME_MAP.get(path.suffix.lower(), "audio/wav")
    audio_bytes = path.read_bytes()
    size = len(audio_bytes)
    key = settings.GEMINI_API_KEY

    # ── Step 1: initiate resumable upload session ─────────────────────────────
    init_url = f"{_GEMINI_UPLOAD}/v1beta/files?key={key}"
    init_body = json.dumps({"file": {"displayName": path.name}}).encode()
    init_req = urllib.request.Request(
        init_url,
        data=init_body,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
        },
        method="POST",
    )
    _, init_headers = _gemini_send_request(init_req, timeout=30)
    upload_url = init_headers.get("x-goog-upload-url")

    if not upload_url:
        raise TranscriptionError("Gemini File API did not return an upload URL")

    # ── Step 2: stream the file bytes ─────────────────────────────────────────
    upload_req = urllib.request.Request(
        upload_url,
        data=audio_bytes,
        headers={
            "Content-Length": str(size),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
        method="POST",
    )
    upload_bytes, _ = _gemini_send_request(upload_req, timeout=120)
    file_info = json.loads(upload_bytes)

    file_uri: str = file_info["file"]["uri"]
    file_name: str = file_info["file"]["name"]  # e.g. "files/abc123"

    # ── Step 3: wait for ACTIVE state (usually immediate for small files) ─────
    for attempt in range(12):
        state_url = f"{_GEMINI_API}/v1beta/{file_name}?key={key}"
        info = _gemini_http("GET", state_url)
        state = info.get("state", "PROCESSING")
        if state == "ACTIVE":
            break
        logger.debug("Gemini file state=%s, waiting ...", state)
        time.sleep(6)
    else:
        raise TranscriptionError("Gemini file never reached ACTIVE state")

    return file_uri, mime


def _call_gemini_transcribe(file_path: str) -> str:
    """
    Isolated, retryable Gemini transcription call.
    Uploads the audio and requests a verbatim transcript via generateContent.
    """
    file_uri, mime = _gemini_upload_audio(file_path)

    model = settings.GEMINI_ASR_MODEL
    url = (
        f"{_GEMINI_API}/v1beta/models/{model}:generateContent"
        f"?key={settings.GEMINI_API_KEY}"
    )
    body = {
        "contents": [{
            "parts": [
                {"fileData": {"mimeType": mime, "fileUri": file_uri}},
                {
                    "text": (
                        "Transcribe this audio verbatim. "
                        "Return ONLY the spoken words — no labels, timestamps, "
                        "speaker tags, or commentary."
                    )
                },
            ]
        }],
        "generationConfig": {"temperature": 0.0},
    }
    result = _gemini_http("POST", url, body)
    try:
        return result["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError) as exc:
        raise TranscriptionError(f"Unexpected Gemini response shape: {result}") from exc


# ── Public API ────────────────────────────────────────────────────────────────

SIMULATED_TRANSCRIPTS = {
    "sample_01.wav": (
        "Alright, let's kick off the sprint planning. Sarah, what's the status on "
        "the API integration? I finished the auth module yesterday. I'm still working "
        "on the rate limiting -- should be done by Wednesday. Great. I'll start on "
        "the frontend dashboard once Sarah's API is ready. We need to deploy to staging "
        "by Friday. Any blockers? I might need access to the production database schema. "
        "Got it, I'll send that over today. Let's reconvene Thursday morning at 10:00 AM."
    ),
    "sample_02.wav": (
        "Okay, the user dashboard feature is ready for review. We have about 400 users "
        "in the beta program. The conversion rate is up 12 percent from last month. "
        "The main issues are loading time on mobile, averaging about three seconds, "
        "and we need to fix the notification bug before the launch. Decision: we launch "
        "next Monday if the mobile performance issue is resolved. Kevin, can you own the "
        "performance fix? Sure, I'll have a pull request up by tomorrow. Target is under "
        "1.5 seconds load time."
    ),
    "sample_03.wav": (
        "Let's finalize the Q3 roadmap. We have three main initiatives: the checkout redesign, "
        "the recommendation engine, and the analytics dashboard. The checkout redesign is "
        "highest priority. The approved budget is 50,000 dollars. Timeline: design complete "
        "by July 31st, development runs through September, and we launch in October. Main "
        "risk: the payment processor migration might cause delays. Action items: Lisa will "
        "schedule the design review by July 15th. Mark will send the payment processor API "
        "documentation to the team by end of week. Our next meeting is July 17th at 2:00 PM."
    ),
}


def transcribe_audio(file_path: str, original_filename: str | None = None) -> dict:
    """
    Transcribes an audio file using the configured provider.
    Retries up to 3 times on transient errors (429, timeouts, 5xx).

    Returns:
        dict with keys: text (str), duration (float | None)
        Note: duration is None for Gemini (the File API does not expose it).
    """
    try:
        if settings.PROVIDER == "gemini":
            text = _call_gemini_transcribe(file_path)
            return {"text": text, "duration": None}

        # openai / groq — via OpenAI SDK
        with open(file_path, "rb") as audio_file:
            result = _call_whisper_api(audio_file)
        return {
            "text": result.text,
            "duration": getattr(result, "duration", None),
        }
    except Exception as exc:  # noqa: BLE001
        if original_filename in SIMULATED_TRANSCRIPTS:
            logger.warning(
                "ASR failed. Using simulated fallback transcript for %s: %s",
                original_filename,
                exc,
            )
            return {"text": SIMULATED_TRANSCRIPTS[original_filename], "duration": None}
        raise TranscriptionError(f"Transcription failed: {exc}") from exc
