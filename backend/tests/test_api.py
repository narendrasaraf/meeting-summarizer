"""
Basic API tests. ASR and summarization calls are mocked so the suite
runs offline without any real API key.

Tests cover:
  - Health check
  - File-type validation
  - Full upload → processing → completed pipeline (mocked AI)
  - 404 for unknown meeting
  - Meeting list returns array
  - Transient failure (APITimeoutError) followed by a successful retry
    in both asr.transcribe_audio and summarizer.summarize_transcript
  - ASR failure propagates to status=failed in the DB (regression guard)
  - Gemini ASR path (_call_gemini_transcribe dispatch)
  - Azure ASR path (_call_azure_transcribe dispatch)
  - Google Cloud STT path (_call_google_stt_transcribe dispatch)
  - Multi-chunk stitching: order preserved, boundary dedup fires, durations summed
  - _dedup_boundary unit test: overlap stripped correctly
  - _dedup_boundary unit test: no overlap → text_b unchanged
"""
import io
import os
import sys
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ["DATABASE_URL"] = "sqlite:///./test_meetings.db"
os.environ["UPLOAD_DIR"] = "test_uploads"
os.environ["PROVIDER"] = "openai"

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.db import init_db
from app.services.asr import transcribe_audio
from app.services.summarizer import summarize_transcript

client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def setup_db():
    init_db()
    yield
    # Dispose the engine to release the SQLite file lock before deletion.
    # On Windows, SQLite holds an OS-level lock until all pool connections
    # are explicitly closed; engine.dispose() flushes the connection pool.
    from app.models.db import engine as _engine
    _engine.dispose()
    for f in ("test_meetings.db",):
        if os.path.exists(f):
            os.remove(f)


# ── Existing tests (unchanged) ────────────────────────────────────────────────


def test_health_check():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_upload_rejects_bad_extension():
    resp = client.post(
        "/api/meetings",
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]


@patch("app.routers.meetings.transcribe_audio")
@patch("app.routers.meetings.summarize_transcript")
def test_upload_processes_and_completes(mock_summarize, mock_transcribe):
    mock_transcribe.return_value = {"text": "We decided to ship on Friday.", "duration": 12.3}
    mock_summarize.return_value = {
        "summary": "Team agreed on Friday ship date.",
        "key_decisions": ["Ship on Friday"],
        "action_items": [{"task": "Prepare release notes", "owner": "Alex", "due_date": "Friday", "priority": "high"}],
    }

    resp = client.post(
        "/api/meetings",
        files={"file": ("standup.wav", io.BytesIO(b"fake-audio-bytes"), "audio/wav")},
    )
    assert resp.status_code == 202
    meeting_id = resp.json()["id"]

    # Background task runs synchronously under TestClient
    detail = client.get(f"/api/meetings/{meeting_id}").json()
    assert detail["status"] == "completed"
    assert detail["summary"] == "Team agreed on Friday ship date."
    assert detail["key_decisions"] == ["Ship on Friday"]
    assert detail["action_items"][0]["task"] == "Prepare release notes"


def test_get_missing_meeting_404():
    resp = client.get("/api/meetings/99999")
    assert resp.status_code == 404


def test_list_meetings_returns_array():
    resp = client.get("/api/meetings")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ── Retry tests ───────────────────────────────────────────────────────────────

def _make_timeout_error() -> openai.APITimeoutError:
    """Build an openai.APITimeoutError without hitting the network."""
    return openai.APITimeoutError(
        request=httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions")
    )


def test_asr_retries_on_transient_error():
    """
    Simulate one APITimeoutError followed by a successful Whisper response.
    transcribe_audio must:
      - call _client.audio.transcriptions.create exactly twice
      - return the transcript from the second (successful) call
    time.sleep is patched so the test doesn't wait for backoff delays.
    _split_audio is patched to bypass the file.stat() call on the fake path.
    """
    import app.services.asr as asr_module
    from pathlib import Path as _Path

    mock_success = MagicMock()
    mock_success.text = "Retry transcript"
    mock_success.duration = 7.5

    with (
        patch.object(
            asr_module, "_split_audio",
            return_value=([_Path("fake_audio.wav")], False),
        ),
        patch("app.services.asr._client") as mock_client,
        patch("builtins.open", MagicMock(return_value=MagicMock(
            __enter__=lambda s, *a: MagicMock(),
            __exit__=lambda s, *a: False,
        ))),
        patch("time.sleep"),  # suppress tenacity back-off delays in tests
    ):
        mock_client.audio.transcriptions.create.side_effect = [
            _make_timeout_error(),  # attempt 1 → transient failure
            mock_success,           # attempt 2 → success
        ]

        result = transcribe_audio("fake_audio.wav")

    assert result["text"] == "Retry transcript"
    assert result["duration"] == 7.5
    assert mock_client.audio.transcriptions.create.call_count == 2


def test_summarizer_retries_on_transient_error():
    """
    Simulate one APITimeoutError followed by a successful GPT response.
    summarize_transcript must call _client.chat.completions.create twice
    and return the parsed result from the second call.
    """
    import json as _json

    good_payload = _json.dumps({
        "summary": "Retry summary.",
        "key_decisions": ["Decision A"],
        "action_items": [{"task": "Task B", "owner": "Alice", "due_date": None, "priority": "low"}],
    })

    mock_success = MagicMock()
    mock_success.choices = [MagicMock(message=MagicMock(content=good_payload))]

    with (
        patch("app.services.summarizer._client") as mock_client,
        patch("time.sleep"),
    ):
        mock_client.chat.completions.create.side_effect = [
            _make_timeout_error(),  # attempt 1 → transient failure
            mock_success,           # attempt 2 → success
        ]

        result = summarize_transcript("Some meeting transcript text.")

    assert result["summary"] == "Retry summary."
    assert result["key_decisions"] == ["Decision A"]
    assert result["action_items"][0]["task"] == "Task B"
    assert mock_client.chat.completions.create.call_count == 2


def test_asr_raises_after_max_retries():
    """
    All 3 attempts fail → TranscriptionError must be raised (tenacity reraises).
    _split_audio is patched to bypass the file.stat() call on the fake path.
    """
    import app.services.asr as asr_module
    from app.services.asr import TranscriptionError
    from pathlib import Path as _Path

    with (
        patch.object(
            asr_module, "_split_audio",
            return_value=([_Path("fake_audio.wav")], False),
        ),
        patch("app.services.asr._client") as mock_client,
        patch("builtins.open", MagicMock(return_value=MagicMock(
            __enter__=lambda s, *a: MagicMock(),
            __exit__=lambda s, *a: False,
        ))),
        patch("time.sleep"),
    ):
        mock_client.audio.transcriptions.create.side_effect = [
            _make_timeout_error(),
            _make_timeout_error(),
            _make_timeout_error(),
        ]

        with pytest.raises(TranscriptionError):
            transcribe_audio("fake_audio.wav")

    assert mock_client.audio.transcriptions.create.call_count == 3


@patch("app.routers.meetings.transcribe_audio")
def test_asr_failure_sets_meeting_status_failed(mock_transcribe):
    """
    When transcribe_audio raises TranscriptionError the router must:
      - store status = "failed" (not "completed") in the DB
      - populate error_message with the exception string
      - not store any transcript or summary content
    This is an end-to-end regression guard: the old silent-fallback code would
    have returned status="completed" with fabricated content instead.
    """
    from app.services.asr import TranscriptionError

    mock_transcribe.side_effect = TranscriptionError("mocked ASR failure — no API key")

    resp = client.post(
        "/api/meetings",
        files={"file": ("failure_test.wav", io.BytesIO(b"fake-audio-bytes"), "audio/wav")},
    )
    assert resp.status_code == 202
    meeting_id = resp.json()["id"]

    # Background task runs synchronously under TestClient
    detail = client.get(f"/api/meetings/{meeting_id}").json()

    assert detail["status"] == "failed", (
        f"Expected status='failed' but got '{detail['status']}'. "
        "The silent-fallback code path may still be active."
    )
    assert detail["error_message"], "error_message must be non-empty on ASR failure"
    assert "mocked ASR failure" in detail["error_message"]
    # No fabricated content must leak through
    assert not detail.get("transcript"), "transcript must be empty/null on failure"
    assert not detail.get("summary"), "summary must be empty/null on failure"


def test_gemini_asr_path_dispatches_correctly():
    """
    When PROVIDER=gemini, transcribe_audio() must call _call_gemini_transcribe()
    instead of the OpenAI SDK path.
    We patch _call_gemini_transcribe so no real HTTP requests are made.
    _split_audio is patched to bypass the file.stat() call on the fake path.
    """
    import app.services.asr as asr_module
    from app.services.asr import transcribe_audio
    from pathlib import Path as _Path

    with (
        patch.object(
            asr_module, "_split_audio",
            return_value=([_Path("fake_audio.wav")], False),
        ),
        patch.object(asr_module, "_call_gemini_transcribe", return_value="Gemini transcript") as mock_gemini,
        patch("app.services.asr.settings") as mock_settings,
    ):
        mock_settings.PROVIDER = "gemini"

        result = transcribe_audio("fake_audio.wav")

    assert result["text"] == "Gemini transcript"
    assert result["duration"] is None
    mock_gemini.assert_called_once_with("fake_audio.wav")


def test_azure_asr_path_dispatches_correctly():
    """
    When PROVIDER=azure, transcribe_audio() must call _call_azure_transcribe()
    and wrap the returned string in {"text": ..., "duration": None}.
    No real Azure SDK calls are made — _call_azure_transcribe is patched.
    _split_audio is patched to bypass the file.stat() call on the fake path.
    """
    import app.services.asr as asr_module
    from app.services.asr import transcribe_audio
    from pathlib import Path as _Path

    with (
        patch.object(
            asr_module, "_split_audio",
            return_value=([_Path("fake_audio.wav")], False),
        ),
        patch.object(
            asr_module, "_call_azure_transcribe", return_value="Azure transcript"
        ) as mock_azure,
        patch("app.services.asr.settings") as mock_settings,
    ):
        mock_settings.PROVIDER = "azure"

        result = transcribe_audio("fake_audio.wav")

    assert result["text"] == "Azure transcript"
    assert result["duration"] is None
    mock_azure.assert_called_once_with("fake_audio.wav")


def test_google_stt_asr_path_dispatches_correctly():
    """
    When PROVIDER=google, transcribe_audio() must call _call_google_stt_transcribe()
    and pass its full {text, duration} dict through unchanged.
    No real Google Cloud API calls are made — _call_google_stt_transcribe is patched.
    _split_audio is patched to bypass the file.stat() call on the fake path.
    """
    import app.services.asr as asr_module
    from app.services.asr import transcribe_audio
    from pathlib import Path as _Path

    with (
        patch.object(
            asr_module, "_split_audio",
            return_value=([_Path("fake_audio.wav")], False),
        ),
        patch.object(
            asr_module,
            "_call_google_stt_transcribe",
            return_value={"text": "Google transcript", "duration": 42.0},
        ) as mock_google,
        patch("app.services.asr.settings") as mock_settings,
    ):
        mock_settings.PROVIDER = "google"

        result = transcribe_audio("fake_audio.wav")

    assert result["text"] == "Google transcript"
    assert result["duration"] == 42.0
    mock_google.assert_called_once_with("fake_audio.wav")


# ── Chunking tests ────────────────────────────────────────────────────────────

def test_chunked_transcription_stitches_in_order():
    """
    A file larger than 24 MB should be split into chunks, transcribed in order,
    and the results stitched so that:
      - all unique content from every chunk is preserved in order
      - overlapping boundary text appears exactly once in the final output
      - per-chunk durations are summed into the final duration

    Both _split_audio and _transcribe_single_file are mocked so no real audio
    file, no pydub, and no provider API calls are needed.
    """
    from pathlib import Path as _Path

    import app.services.asr as asr_module
    from app.services.asr import transcribe_audio as _transcribe_audio

    fake_chunks = [
        _Path("/fake/tmp/chunk_0000.wav"),
        _Path("/fake/tmp/chunk_0001.wav"),
        _Path("/fake/tmp/chunk_0002.wav"),
    ]
    # Consecutive chunks deliberately share boundary words to exercise dedup.
    chunk_results = [
        {"text": "We kicked off the sprint planning meeting today.", "duration": 120.0},
        {"text": "planning meeting today. Sarah will own the API task.", "duration": 118.5},
        {"text": "own the API task. Launch is scheduled for Friday.", "duration": 60.0},
    ]

    with (
        patch.object(asr_module, "_split_audio", return_value=(fake_chunks, True)),
        patch.object(asr_module, "_transcribe_single_file", side_effect=chunk_results),
        patch("shutil.rmtree"),  # suppress temp-dir cleanup on fake paths
    ):
        result = _transcribe_audio("/fake/large_meeting.wav")

    # All unique content must survive in order
    assert "kicked off the sprint" in result["text"]
    assert "Sarah will own the API task" in result["text"]
    assert "scheduled for Friday" in result["text"]

    # Boundary text must appear exactly once (dedup fired)
    assert result["text"].count("planning meeting today") == 1
    assert result["text"].count("own the API task") == 1

    # Duration is the sum of all per-chunk durations
    assert result["duration"] == pytest.approx(120.0 + 118.5 + 60.0)


def test_dedup_boundary_strips_overlapping_prefix():
    """
    _dedup_boundary must strip the longest matching suffix of text_a from the
    start of text_b when the positional word overlap meets the 70% threshold.
    """
    from app.services.asr import _dedup_boundary

    # The last 6 words of text_a are identical to the first 6 of text_b.
    text_a = "The quick brown fox jumps over the lazy dog."
    text_b = "fox jumps over the lazy dog. Then it ran away quickly."

    result_text, n_dropped = _dedup_boundary(text_a, text_b)

    # The overlapping prefix ("fox jumps over the lazy dog.") must be gone
    assert result_text.startswith("Then it ran")
    assert "quickly" in result_text
    # The overlap fragment must not be doubled
    assert result_text.count("fox jumps") == 0
    assert n_dropped == 6


def test_dedup_boundary_no_overlap_unchanged():
    """
    _dedup_boundary must return text_b unchanged when there is no positional
    word overlap between the suffix of text_a and the prefix of text_b.
    """
    from app.services.asr import _dedup_boundary

    text_a = "First chunk ends right here."
    text_b = "Completely different words begin now without any overlap at all."

    result_text, n_dropped = _dedup_boundary(text_a, text_b)

    assert result_text == text_b
    assert n_dropped == 0


# ── Rate limiting tests ───────────────────────────────────────────────────────

@patch("app.routers.meetings.transcribe_audio")
@patch("app.routers.meetings.summarize_transcript")
def test_upload_rate_limit_blocks_4th_request(mock_summarize, mock_transcribe):
    """
    POST /api/meetings is limited to UPLOAD_RATE_LIMIT (default: 3/hour) per IP.

    The 4th request from the same client IP within the rate window must return
    HTTP 429.  We reset the limiter's in-memory storage before and after the
    test to ensure isolation from prior/subsequent test uploads.

    slowapi attaches the canonical limiter to app.state.limiter in app.main;
    that is the instance whose .reset() clears the counter store.
    """
    import app.main as _main_module

    # Reset the canonical limiter so prior test uploads don't count.
    _main_module.limiter.reset()

    mock_transcribe.return_value = {"text": "Rate limit test transcript.", "duration": 5.0}
    mock_summarize.return_value = {
        "summary": "Rate limit test summary.",
        "key_decisions": [],
        "action_items": [],
    }

    audio_payload = {"file": ("rl_test.wav", io.BytesIO(b"fake-audio-bytes"), "audio/wav")}

    # First 3 requests must succeed (202).
    for i in range(3):
        resp = client.post("/api/meetings", files=audio_payload)
        assert resp.status_code == 202, (
            f"Request {i + 1} expected 202, got {resp.status_code}: {resp.text}"
        )

    # 4th request must be blocked with 429.
    resp = client.post("/api/meetings", files=audio_payload)
    assert resp.status_code == 429, (
        f"Expected 429 on 4th request, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    # slowapi returns {"error": "Rate limit exceeded: ..."} by default.
    assert "error" in body or "detail" in body, (
        f"429 response should carry an error/detail message, got: {body}"
    )

    # Clean up: reset so the rate window doesn't bleed into other tests.
    _main_module.limiter.reset()


