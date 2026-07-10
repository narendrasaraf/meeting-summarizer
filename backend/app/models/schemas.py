"""
Pydantic schemas for API request/response bodies.
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ActionItem(BaseModel):
    task: str
    owner: Optional[str] = "Unassigned"
    due_date: Optional[str] = None
    priority: Optional[str] = "medium"


class SummaryPayload(BaseModel):
    summary: str
    key_decisions: List[str]
    action_items: List[ActionItem]


class MeetingResponse(BaseModel):
    id: int
    filename: str
    status: str
    duration_seconds: Optional[float]
    transcript: Optional[str]
    summary: Optional[str]
    key_decisions: List[str] = []
    action_items: List[ActionItem] = []
    error_message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MeetingListItem(BaseModel):
    id: int
    filename: str
    status: str
    created_at: datetime
    summary_preview: Optional[str] = None
