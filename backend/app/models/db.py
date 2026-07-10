"""
Database layer. Uses SQLModel (SQLAlchemy + Pydantic) on top of SQLite
for zero-config local persistence. Swap DATABASE_URL for Postgres/MySQL
in production without changing this file.
"""
from datetime import datetime, timezone
from typing import Optional, List

from sqlmodel import SQLModel, Field, create_engine, Session
import json

from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
)


class Meeting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str
    duration_seconds: Optional[float] = None
    status: str = Field(default="processing")  # processing | completed | failed
    transcript: Optional[str] = None
    summary: Optional[str] = None
    key_decisions_json: Optional[str] = None  # JSON-encoded list[str]
    action_items_json: Optional[str] = None   # JSON-encoded list[dict]
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def key_decisions(self) -> List[str]:
        return json.loads(self.key_decisions_json) if self.key_decisions_json else []

    def action_items(self) -> List[dict]:
        return json.loads(self.action_items_json) if self.action_items_json else []


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
