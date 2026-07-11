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
• azure          → Azure Cognitive Services Speech SDK
                   (azure-cognitiveservices-speech).
                   Uses continuous recognition so arbitrarily long meeting
                   audio is supported. Duration is not exposed by the SDK
                   response; returns None for duration.
• google         → Google Cloud Speech-to-Text v2
                   (google-cloud-speech).
                   Uses Application Default Credentials (service account JSON).
                   Auto-selects synchronous recognize() for files < 9 MB or
                   long_running_recognize() for larger files. Word-level timing
                   is requested so duration is computed from the last word's
                   end_time.

Brief mapping
-------------
  OpenAI Whisper  → PROVIDER=openai  (satisfies "OpenAI Whisper" in the brief)
  Groq Whisper    → PROVIDER=groq    (Whisper large-v3, free tier)
  Google          → PROVIDER=google  (satisfies "Google" in the brief)
  Azure           → PROVIDER=azure   (satisfies "Azure" in the brief)
  Gemini          → PROVIDER=gemini  (Gemini multimodal audio)

Why tenacity over a hand-rolled loop?  See the original comment in this
module: composable retry predicates, structured pre-sleep logging, and
no off-by-one risk. For Gemini's HTTP errors (urllib.error.HTTPError)
we add a matching predicate that checks the status code.
"""
import json
import logging
import shutil
import tempfile
import threading
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


# Module-level client (re-created if provider changes on restart).
# Providers that bypass the OpenAI SDK for ASR get None here.
_client: OpenAI | None = (
    None if settings.PROVIDER in {"gemini", "azure", "google"}
    else _make_openai_client()
)


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


# ── Azure Cognitive Services Speech path ──────────────────────────────────────

def _call_azure_transcribe(file_path: str) -> str:
    """
    Transcribe audio using the Azure Cognitive Services Speech SDK.

    Uses continuous recognition so arbitrarily long meeting recordings are
    supported (not just the 15-second limit of recognize_once).

    Requires:
        AZURE_SPEECH_KEY   — Cognitive Services resource key
        AZURE_SPEECH_REGION — Azure region, e.g. "eastus"

    Natively supports WAV (PCM). For MP3/M4A/WebM/OGG, Azure requires the
    GStreamer plugin to be installed separately on the host.

    Returns:
        Concatenated transcript string (duration is not available from the SDK).
    """
    try:
        import azure.cognitiveservices.speech as speechsdk  # noqa: PLC0415
    except ImportError as exc:
        raise TranscriptionError(
            "azure-cognitiveservices-speech is not installed. "
            "Run: pip install azure-cognitiveservices-speech"
        ) from exc

    speech_config = speechsdk.SpeechConfig(
        subscription=settings.AZURE_SPEECH_KEY,
        region=settings.AZURE_SPEECH_REGION,
    )
    speech_config.speech_recognition_language = "en-US"

    audio_config = speechsdk.audio.AudioConfig(filename=file_path)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    fragments: list[str] = []
    errors: list[str] = []
    done = threading.Event()

    def _on_recognized(evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            fragments.append(evt.result.text)

    def _on_canceled(evt: speechsdk.SpeechRecognitionCanceledEventArgs) -> None:
        details = evt.result.cancellation_details
        if details.reason == speechsdk.CancellationReason.Error:
            errors.append(
                f"Azure Speech error {details.error_code}: {details.error_details}"
            )
        done.set()

    def _on_stopped(evt) -> None:  # noqa: ANN001
        done.set()

    recognizer.recognized.connect(_on_recognized)
    recognizer.canceled.connect(_on_canceled)
    recognizer.session_stopped.connect(_on_stopped)

    recognizer.start_continuous_recognition()
    timed_out = not done.wait(timeout=600)  # 10-minute hard cap per file
    recognizer.stop_continuous_recognition()

    if timed_out:
        raise TranscriptionError(
            "Azure Speech recognition timed out after 600 s. "
            "Consider splitting the audio into shorter chunks."
        )
    if errors:
        raise TranscriptionError(errors[0])

    return " ".join(fragments)


# ── Google Cloud Speech-to-Text v2 path ───────────────────────────────────────

# File size threshold above which long_running_recognize() is used instead of
# the synchronous recognize() (which has a 10 MB / ~60 s limit).
_GOOGLE_SYNC_MAX_BYTES = 9 * 1024 * 1024  # 9 MB

# Map file extensions to the Google STT AudioEncoding enum values.
# Populated lazily to avoid importing google.cloud.speech at module load.
_GOOGLE_ENCODING_MAP: dict[str, str] = {
    ".wav": "LINEAR16",
    ".mp3": "MP3",
    ".flac": "FLAC",
    ".ogg": "OGG_OPUS",
    ".webm": "WEBM_OPUS",
}


def _call_google_stt_transcribe(file_path: str) -> dict:
    """
    Transcribe audio using Google Cloud Speech-to-Text v2.

    Authentication is handled by the google-cloud-speech library via
    Application Default Credentials (ADC):
      1. If GOOGLE_APPLICATION_CREDENTIALS is set, the library reads that
         service account JSON automatically.
      2. Otherwise falls back to gcloud ADC / instance metadata.

    Auto-selects synchronous recognize() for files < 9 MB or
    long_running_recognize() for larger files.

    Returns:
        dict with keys: text (str), duration (float | None)
        duration is derived from the last word's end_time if word timing is
        available, otherwise None.
    """
    try:
        from google.cloud import speech  # noqa: PLC0415
    except ImportError as exc:
        raise TranscriptionError(
            "google-cloud-speech is not installed. "
            "Run: pip install google-cloud-speech"
        ) from exc

    path = Path(file_path)
    audio_bytes = path.read_bytes()

    # Map extension → encoding name → enum value
    encoding_name = _GOOGLE_ENCODING_MAP.get(path.suffix.lower(), "LINEAR16")
    encoding = getattr(speech.RecognitionConfig.AudioEncoding, encoding_name,
                       speech.RecognitionConfig.AudioEncoding.LINEAR16)

    client = speech.SpeechClient()
    audio = speech.RecognitionAudio(content=audio_bytes)
    config = speech.RecognitionConfig(
        encoding=encoding,
        language_code="en-US",
        model="latest_long",
        enable_word_time_offsets=True,
    )

    logger.info(
        "google-stt: file=%s size_bytes=%d encoding=%s",
        path.name,
        len(audio_bytes),
        encoding_name,
    )

    if len(audio_bytes) < _GOOGLE_SYNC_MAX_BYTES:
        response = client.recognize(config=config, audio=audio)
    else:
        logger.info("google-stt: file > 9 MB, using long_running_recognize()")
        operation = client.long_running_recognize(config=config, audio=audio)
        response = operation.result(timeout=600)

    text_parts: list[str] = []
    duration: float | None = None

    for result in response.results:
        if not result.alternatives:
            continue
        alt = result.alternatives[0]
        text_parts.append(alt.transcript)
        # Track the latest word end time across all results
        for word_info in alt.words:
            end_s = word_info.end_time.total_seconds()
            if duration is None or end_s > duration:
                duration = end_s

    return {"text": " ".join(text_parts), "duration": duration}


# ── Audio chunking ────────────────────────────────────────────────────────────

# Files that exceed this limit are split before being sent to any provider.
# 24 MB leaves a safe margin under OpenAI / Groq Whisper's hard 25 MB cap.
_CHUNK_THRESHOLD_BYTES: int = 24 * 1024 * 1024

# Target duration for each chunk (non-overlapping portion).
_CHUNK_TARGET_MS: int = 10 * 60 * 1000  # 10 minutes

# Overlap appended to each chunk so the boundary transcript can be deduplicated.
_CHUNK_OVERLAP_MS: int = 5 * 1000  # 5 seconds

# Sample rate used when exporting chunks.  At 16 kHz mono 16-bit, a 10-min
# chunk is exactly 19.2 MB — predictably under the 25 MB cap regardless of
# the source format/bitrate.  This is also the Whisper-optimal sample rate.
_CHUNK_SAMPLE_RATE: int = 16_000

# Maximum words inspected when hunting for a boundary overlap.
_DEDUP_MAX_WORDS: int = 15


def _split_audio(file_path: str) -> tuple[list[Path], bool]:
    """
    Split a large audio file into overlapping 10-minute WAV chunks when it
    exceeds _CHUNK_THRESHOLD_BYTES.

    Each chunk is exported as 16 kHz mono 16-bit WAV so that no chunk can
    exceed ~19.2 MB (10 min × 16 000 Hz × 2 bytes × 1 channel), which is
    safely under every provider's file-size limit.

    Requires pydub to be installed; raises TranscriptionError with an
    installation hint if it is missing.

    Returns:
        (chunk_paths, was_split)
        If was_split is False, chunk_paths == [Path(file_path)] (original, unchanged).
        If was_split is True, chunk_paths are WAV files inside a new temp directory;
        the caller is responsible for deleting that directory after transcription.
    """
    src = Path(file_path)
    if src.stat().st_size <= _CHUNK_THRESHOLD_BYTES:
        return [src], False

    try:
        from pydub import AudioSegment  # noqa: PLC0415
    except ImportError as exc:
        raise TranscriptionError(
            "pydub is not installed — required to split audio files > 24 MB. "
            "Run: pip install pydub   "
            "(also ensure ffmpeg is on PATH for non-WAV/non-PCM formats)"
        ) from exc

    logger.info(
        "audio_chunk: file=%s size_mb=%.1f exceeds %d MB — splitting into ≤10-min chunks",
        src.name,
        src.stat().st_size / (1024 * 1024),
        _CHUNK_THRESHOLD_BYTES // (1024 * 1024),
    )

    audio = AudioSegment.from_file(str(src))
    # Normalise to 16 kHz mono to keep chunk sizes predictable and Whisper-friendly.
    audio = audio.set_frame_rate(_CHUNK_SAMPLE_RATE).set_channels(1).set_sample_width(2)
    total_ms = len(audio)

    tmp_dir = Path(tempfile.mkdtemp(prefix="asr_chunks_"))
    chunk_paths: list[Path] = []
    chunk_idx = 0
    start_ms = 0

    while start_ms < total_ms:
        end_ms = min(start_ms + _CHUNK_TARGET_MS + _CHUNK_OVERLAP_MS, total_ms)
        chunk = audio[start_ms:end_ms]

        chunk_path = tmp_dir / f"chunk_{chunk_idx:04d}.wav"
        chunk.export(str(chunk_path), format="wav")

        logger.debug(
            "audio_chunk: chunk %d  [%.1fs – %.1fs]  size_mb=%.1f",
            chunk_idx,
            start_ms / 1000,
            end_ms / 1000,
            chunk_path.stat().st_size / (1024 * 1024),
        )
        chunk_paths.append(chunk_path)

        chunk_idx += 1
        start_ms += _CHUNK_TARGET_MS  # advance by non-overlapping portion only

    logger.info(
        "audio_chunk: produced %d chunk(s) in %s", len(chunk_paths), tmp_dir
    )
    return chunk_paths, True


def _dedup_boundary(text_a: str, text_b: str, max_words: int = _DEDUP_MAX_WORDS) -> tuple[str, int]:
    """
    Remove the duplicated overlap region from the start of text_b.

    Because consecutive chunks share a _CHUNK_OVERLAP_MS overlap, the ASR for
    chunk i+1 typically begins with words already present at the end of chunk i.
    This function finds the longest suffix of text_a that fuzzy-matches a prefix
    of text_b (≥70% word-level hit rate) and strips that prefix from text_b.

    The algorithm iterates from the largest candidate overlap down to 1 word so
    it always removes the most specific match first, minimising false positives.

    Args:
        text_a:    transcript of chunk i
        text_b:    transcript of chunk i+1
        max_words: maximum prefix length to test (caps false-positive risk)

    Returns:
        Tuple of (Cleaned text_b, number of words dropped)
    """
    if not text_a or not text_b:
        return text_b, 0

    def _norm(word: str) -> str:
        """Lowercase and strip punctuation for position-insensitive comparison."""
        return word.lower().strip(".,!?;:'\"\u2014-\u2026")

    words_a = text_a.split()
    words_b = text_b.split()
    n_test = min(max_words, len(words_a), len(words_b))

    for n in range(n_test, 0, -1):
        tail = [_norm(w) for w in words_a[-n:]]
        head = [_norm(w) for w in words_b[:n]]
        hits = sum(a == b for a, b in zip(tail, head))
        if hits / n >= 0.70:
            logger.debug(
                "audio_chunk: dedup removed %d overlapping word(s) at chunk boundary", n
            )
            return " ".join(words_b[n:]), n

    return text_b, 0


def _stitch_chunks(results: list[dict]) -> dict:
    """
    Merge per-chunk transcription results into a single result dict.

    - Text: deduplicate chunk boundaries then join with a single space,
      skipping any chunks that produced empty text.
    - Duration: sum of per-chunk durations, or None if any chunk returned None
      (e.g. gemini / azure, which don't expose duration).
    - Segments: align start and end times of segments from consecutive chunks
      after discarding overlapping words.
    """
    if not results:
        return {"text": "", "duration": None, "segments": None}
    if len(results) == 1:
        # Normalize segments if present
        segments = results[0].get("segments")
        if segments:
            normalized = []
            for seg in segments:
                start = seg.get("start") if isinstance(seg, dict) else getattr(seg, "start", None)
                end = seg.get("end") if isinstance(seg, dict) else getattr(seg, "end", None)
                text = seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", None)
                if start is not None and end is not None:
                    normalized.append({"start": start, "end": end, "text": text})
            results[0]["segments"] = normalized
        return results[0]

    texts: list[str] = [results[0]["text"]]
    has_any_segments = any(r.get("segments") is not None for r in results)
    stitched_segments = []

    # Copy normalized first chunk segments
    first_segs = results[0].get("segments")
    if first_segs:
        for seg in first_segs:
            start = seg.get("start") if isinstance(seg, dict) else getattr(seg, "start", None)
            end = seg.get("end") if isinstance(seg, dict) else getattr(seg, "end", None)
            text = seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", None)
            if start is not None and end is not None:
                stitched_segments.append({"start": start, "end": end, "text": text})

    current_time_offset = 0.0
    for i in range(len(results) - 1):
        prev = results[i]
        curr = results[i + 1]
        
        # Non-overlapping chunk offset
        current_time_offset += _CHUNK_TARGET_MS / 1000.0

        cleaned, n_dropped = _dedup_boundary(prev["text"], curr["text"])
        if cleaned:
            texts.append(cleaned)

        curr_segs = curr.get("segments")
        if curr_segs:
            words_skipped = 0
            for seg in curr_segs:
                start = seg.get("start") if isinstance(seg, dict) else getattr(seg, "start", None)
                end = seg.get("end") if isinstance(seg, dict) else getattr(seg, "end", None)
                text = seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", None)
                if start is not None and end is not None and text:
                    seg_words = text.split()
                    seg_word_count = len(seg_words)
                    if words_skipped + seg_word_count <= n_dropped:
                        words_skipped += seg_word_count
                        continue
                    elif words_skipped < n_dropped:
                        # n_dropped falls inside this segment
                        num_to_strip = n_dropped - words_skipped
                        remaining_text = " ".join(seg_words[num_to_strip:])
                        words_skipped = n_dropped
                        if remaining_text:
                            # Proportional adjustment of start time
                            ratio = len(remaining_text) / len(text)
                            adjusted_start = start + (end - start) * (1 - ratio)
                            stitched_segments.append({
                                "start": adjusted_start + current_time_offset,
                                "end": end + current_time_offset,
                                "text": remaining_text
                            })
                    else:
                        stitched_segments.append({
                            "start": start + current_time_offset,
                            "end": end + current_time_offset,
                            "text": text
                        })

    durations = [r.get("duration") for r in results]
    total_duration: float | None = (
        sum(d for d in durations)  # type: ignore[misc]
        if all(d is not None for d in durations)
        else None
    )

    return {
        "text": " ".join(t for t in texts if t),
        "duration": total_duration,
        "segments": stitched_segments if has_any_segments else None,
    }


# ── Public API ────────────────────────────────────────────────────────────────


def _transcribe_single_file(file_path: str) -> dict:
    """
    Dispatch one file to the active provider without any chunking logic.
    Called by transcribe_audio for each chunk (or directly for small files).

    Returns:
        dict with keys: text (str), duration (float | None), segments (list | None)
    """
    if settings.PROVIDER == "gemini":
        text = _call_gemini_transcribe(file_path)
        return {"text": text, "duration": None, "segments": None}

    if settings.PROVIDER == "azure":
        text = _call_azure_transcribe(file_path)
        return {"text": text, "duration": None, "segments": None}

    if settings.PROVIDER == "google":
        res = _call_google_stt_transcribe(file_path)
        return {"text": res["text"], "duration": res["duration"], "segments": None}

    # openai / groq — via OpenAI SDK
    with open(file_path, "rb") as audio_file:
        result = _call_whisper_api(audio_file)

    segments = None
    if hasattr(result, "segments") and result.segments is not None:
        segments = []
        for seg in result.segments:
            start = seg.get("start") if isinstance(seg, dict) else getattr(seg, "start", None)
            end = seg.get("end") if isinstance(seg, dict) else getattr(seg, "end", None)
            text = seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", None)
            if start is not None and end is not None:
                segments.append({"start": start, "end": end, "text": text})

    return {
        "text": result.text,
        "duration": getattr(result, "duration", None),
        "segments": segments,
    }


def transcribe_audio(file_path: str, original_filename: str | None = None) -> dict:
    """
    Transcribes an audio file using the configured provider.

    Large-file handling
    -------------------
    Files that exceed _CHUNK_THRESHOLD_BYTES (24 MB) are automatically split
    into overlapping 10-minute chunks via pydub, transcribed chunk-by-chunk
    through the active provider, then stitched back together:

      1. _split_audio()          — splits and exports normalised 16 kHz WAV chunks
      2. _transcribe_single_file() — dispatches each chunk to the provider
      3. _stitch_chunks()        — deduplicates chunk boundaries and sums durations

    Temporary chunk files are always deleted in a try/finally block even on
    provider failure, so no disk leakage occurs.

    This resolves the hard 25 MB limit of OpenAI / Groq Whisper without any
    change to the public API or the router.

    Retry policy
    ------------
    openai/groq: up to 3 attempts via tenacity (429, timeouts, 5xx).
    azure/google: retry handled inside their respective _call_* functions.

    On any non-transient failure the underlying exception is re-raised as
    TranscriptionError so the router marks the meeting as "failed".

    Returns:
        dict with keys: text (str), duration (float | None)
        duration is None for gemini and azure (not exposed by those APIs).
    """
    try:
        chunk_paths, was_split = _split_audio(file_path)
        tmp_dir = chunk_paths[0].parent if was_split else None

        try:
            results = [_transcribe_single_file(str(p)) for p in chunk_paths]
        finally:
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                logger.debug("audio_chunk: removed temp dir %s", tmp_dir)

        return _stitch_chunks(results)

    except TranscriptionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise TranscriptionError(f"Transcription failed: {exc}") from exc
