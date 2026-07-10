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
  - Gemini ASR path (_call_gemini_transcribe dispatch)
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
    """
    mock_success = MagicMock()
    mock_success.text = "Retry transcript"
    mock_success.duration = 7.5

    with (
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
    """
    from app.services.asr import TranscriptionError

    with (
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


def test_gemini_asr_path_dispatches_correctly():
    """
    When PROVIDER=gemini, transcribe_audio() must call _call_gemini_transcribe()
    instead of the OpenAI SDK path.
    We patch _call_gemini_transcribe so no real HTTP requests are made.
    """
    import app.services.asr as asr_module
    from app.services.asr import transcribe_audio

    with (
        patch.object(asr_module, "_call_gemini_transcribe", return_value="Gemini transcript") as mock_gemini,
        patch("app.services.asr.settings") as mock_settings,
    ):
        mock_settings.PROVIDER = "gemini"

        result = transcribe_audio("fake_audio.wav")

    assert result["text"] == "Gemini transcript"
    assert result["duration"] is None
    mock_gemini.assert_called_once_with("fake_audio.wav")
