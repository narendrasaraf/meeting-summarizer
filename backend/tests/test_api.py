"""
Basic API tests. ASR and summarization calls are mocked so the suite
runs offline without an OpenAI API key.
"""
import io
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ["DATABASE_URL"] = "sqlite:///./test_meetings.db"
os.environ["UPLOAD_DIR"] = "test_uploads"

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.db import init_db

client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def setup_db():
    init_db()
    yield
    for f in ("test_meetings.db",):
        if os.path.exists(f):
            os.remove(f)


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
