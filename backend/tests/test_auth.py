import io
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ["DATABASE_URL"] = "sqlite:///./test_auth.db"
os.environ["UPLOAD_DIR"] = "test_uploads"

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.models.db import init_db, Meeting, User
from app.core.auth import get_password_hash
from app.limiter import limiter
from sqlmodel import Session, create_engine, select

client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def setup_db():
    init_db()
    yield
    from app.models.db import engine as _engine
    _engine.dispose()
    for f in ("test_auth.db",):
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass


@pytest.fixture(autouse=True)
def clean_tables():
    from app.models.db import engine
    with Session(engine) as session:
        session.execute(User.__table__.delete())
        session.execute(Meeting.__table__.delete())
        session.commit()


def test_register_and_login():
    # 1. Register a user
    resp = client.post(
        "/api/auth/register",
        json={"email": "user@example.com", "password": "securepassword"},
    )
    assert resp.status_code == 201
    assert "access_token" in resp.json()

    # 2. Register same email fails
    resp = client.post(
        "/api/auth/register",
        json={"email": "user@example.com", "password": "anotherpassword"},
    )
    assert resp.status_code == 400
    assert "Email already registered" in resp.json()["detail"]

    # 3. Login successful
    resp = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "securepassword"},
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()

    # 4. Login wrong password fails
    resp = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "wrongpassword"},
    )
    assert resp.status_code == 400
    assert "Incorrect email or password" in resp.json()["detail"]


def test_auth_required_gating():
    with patch.object(settings, "AUTH_REQUIRED", True):
        # 1. List without token -> 401
        resp = client.get("/api/meetings")
        assert resp.status_code == 401

        # 2. Upload without token -> 401
        resp = client.post(
            "/api/meetings",
            files={"file": ("standup.wav", io.BytesIO(b"RIFF\x00\x00\x00\x00WAVEfake-audio-bytes"), "audio/wav")},
        )
        assert resp.status_code == 401


def test_cross_user_isolation():
    # Register two users
    resp_a = client.post(
        "/api/auth/register",
        json={"email": "usera@example.com", "password": "password"},
    )
    token_a = resp_a.json()["access_token"]

    resp_b = client.post(
        "/api/auth/register",
        json={"email": "userb@example.com", "password": "password"},
    )
    token_b = resp_b.json()["access_token"]

    # User A uploads a meeting
    with patch("app.workers.pipeline.transcribe_audio") as mock_transcribe, \
         patch("app.workers.pipeline.summarize_transcript") as mock_summarize:
        mock_transcribe.return_value = {"text": "User A meeting content", "duration": 5.0}
        mock_summarize.return_value = {"summary": "A's Summary", "key_decisions": [], "action_items": []}

        resp = client.post(
            "/api/meetings",
            headers={"Authorization": f"Bearer {token_a}"},
            files={"file": ("meeting_a.wav", io.BytesIO(b"RIFF\x00\x00\x00\x00WAVEmock-audio-data"), "audio/wav")},
        )
        assert resp.status_code == 202
        meeting_id = resp.json()["id"]

    # User B lists meetings -> should NOT see User A's meeting
    resp_list = client.get(
        "/api/meetings",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_list.status_code == 200
    meetings = resp_list.json()
    assert len(meetings) == 0

    # User B tries to fetch User A's meeting -> 404
    resp_get = client.get(
        f"/api/meetings/{meeting_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_get.status_code == 404

    # User B tries to delete User A's meeting -> 404
    resp_del = client.delete(
        f"/api/meetings/{meeting_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_del.status_code == 404

    # User A can fetch own meeting
    resp_get_a = client.get(
        f"/api/meetings/{meeting_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_get_a.status_code == 200
    assert resp_get_a.json()["filename"] == "meeting_a.wav"

    # User A can delete own meeting
    resp_del_a = client.delete(
        f"/api/meetings/{meeting_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_del_a.status_code == 204


def test_rate_limiter_keys_by_user_id():
    # Register a user
    resp = client.post(
        "/api/auth/register",
        json={"email": "rate_user@example.com", "password": "password"},
    )
    token = resp.json()["access_token"]

    # Mock a request to test get_rate_limit_key directly
    from fastapi import Request
    from app.limiter import get_rate_limit_key
    from app.models.db import engine

    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == "rate_user@example.com")).first()
        assert user is not None

        # Simulate Request state with user
        class MockRequest:
            def __init__(self):
                class State:
                    user = None
                self.state = State()
                self.headers = {}
                self.client = type('Client', (object,), {'host': '127.0.0.1'})()

        req = MockRequest()
        # Anonymous request
        assert get_rate_limit_key(req) == "127.0.0.1"

        # Authenticated request
        req.state.user = user
        assert get_rate_limit_key(req) == f"user:{user.id}"
