"""
Meeting endpoints: upload audio, poll status, list history.

Processing runs as a FastAPI BackgroundTask so the upload request
returns immediately with a meeting id; the frontend polls
GET /api/meetings/{id} until status flips to "completed" or "failed".
"""
import json
import os
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from app.core.config import settings
from app.models.db import Meeting, get_session
from app.models.schemas import ActionItem, MeetingListItem, MeetingResponse
from app.services.asr import TranscriptionError, transcribe_audio
from app.services.summarizer import SummarizationError, summarize_transcript

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


def _process_meeting(meeting_id: int, file_path: str) -> None:
    """Background job: transcribe then summarize, updating the DB row."""
    from sqlmodel import Session as _Session
    from app.models.db import engine

    with _Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        if not meeting:
            return
        try:
            asr_result = transcribe_audio(file_path)
            meeting.transcript = asr_result["text"]
            meeting.duration_seconds = asr_result.get("duration")
            session.add(meeting)
            session.commit()

            summary_result = summarize_transcript(meeting.transcript)
            meeting.summary = summary_result["summary"]
            meeting.key_decisions_json = json.dumps(summary_result["key_decisions"])
            meeting.action_items_json = json.dumps(summary_result["action_items"])
            meeting.status = "completed"
        except (TranscriptionError, SummarizationError) as exc:
            meeting.status = "failed"
            meeting.error_message = str(exc)
        except Exception as exc:  # noqa: BLE001
            meeting.status = "failed"
            meeting.error_message = f"Unexpected error: {exc}"
        finally:
            meeting.updated_at = datetime.utcnow()
            session.add(meeting)
            session.commit()
            if os.path.exists(file_path):
                os.remove(file_path)


def _to_response(meeting: Meeting) -> MeetingResponse:
    return MeetingResponse(
        id=meeting.id,
        filename=meeting.filename,
        status=meeting.status,
        duration_seconds=meeting.duration_seconds,
        transcript=meeting.transcript,
        summary=meeting.summary,
        key_decisions=meeting.key_decisions(),
        action_items=[ActionItem(**a) for a in meeting.action_items()],
        error_message=meeting.error_message,
        created_at=meeting.created_at,
    )


@router.post("", response_model=MeetingResponse, status_code=202)
async def upload_meeting(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(settings.ALLOWED_EXTENSIONS))}",
        )

    contents = await file.read()
    size_mb = len(contents) / (1024 * 1024)
    if size_mb > settings.MAX_UPLOAD_MB:
        raise HTTPException(400, f"File too large ({size_mb:.1f}MB). Max is {settings.MAX_UPLOAD_MB}MB.")

    stored_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, stored_name)
    with open(file_path, "wb") as f:
        f.write(contents)

    meeting = Meeting(filename=file.filename or stored_name, status="processing")
    session.add(meeting)
    session.commit()
    session.refresh(meeting)

    background_tasks.add_task(_process_meeting, meeting.id, file_path)

    return _to_response(meeting)


@router.get("", response_model=List[MeetingListItem])
def list_meetings(session: Session = Depends(get_session)):
    meetings = session.exec(select(Meeting).order_by(Meeting.created_at.desc())).all()
    return [
        MeetingListItem(
            id=m.id,
            filename=m.filename,
            status=m.status,
            created_at=m.created_at,
            summary_preview=(m.summary[:140] + "...") if m.summary and len(m.summary) > 140 else m.summary,
        )
        for m in meetings
    ]


@router.get("/{meeting_id}", response_model=MeetingResponse)
def get_meeting(meeting_id: int, session: Session = Depends(get_session)):
    meeting = session.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    return _to_response(meeting)


@router.delete("/{meeting_id}", status_code=204)
def delete_meeting(meeting_id: int, session: Session = Depends(get_session)):
    meeting = session.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    session.delete(meeting)
    session.commit()
