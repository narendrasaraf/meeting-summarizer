"""
Meeting endpoints: upload audio, poll status, list history.

Processing runs as a FastAPI BackgroundTask so the upload request
returns immediately with a meeting id; the frontend polls
GET /api/meetings/{id} until status flips to "completed" or "failed".

Each pipeline stage is logged at INFO level so that the full log trail
for a failed meeting identifies exactly which stage failed and why.
"""
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlmodel import Session, select
from redis import Redis
from rq import Queue

from app.core.config import settings
from app.core.auth import get_current_user
from app.limiter import limiter
from app.models.db import Meeting, User, get_session
from app.models.schemas import ActionItem, MeetingListItem, MeetingResponse, ErrorResponse
from app.workers.pipeline import process_meeting_task
from app.utils.audio import is_audio_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


def get_queue() -> Queue:
    redis_conn = Redis.from_url(settings.REDIS_URL)
    return Queue("default", connection=redis_conn, is_async=settings.RQ_ASYNC)


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
        segments=meeting.segments() if meeting.segments_json else None,
        asr_seconds=meeting.asr_seconds,
        summary_seconds=meeting.summary_seconds,
        error_message=meeting.error_message,
        created_at=meeting.created_at,
    )



@router.post(
    "",
    response_model=MeetingResponse,
    status_code=202,
    summary="Upload and process a new meeting",
    description=(
        "Uploads an audio file (e.g. WAV, MP3, M4A) for transcription and summarization. "
        "Performs file size and type validations, saves the audio file locally, enqueues the job "
        "into the Redis Queue (RQ), and returns the meeting record with status='processing'."
    ),
    responses={
        202: {
            "description": "Upload accepted. The transcription/summarization task is now enqueued.",
            "model": MeetingResponse,
        },
        400: {"description": "File validation failed (unsupported format or exceeds size limit).", "model": ErrorResponse},
        401: {"description": "Authentication required to upload a meeting.", "model": ErrorResponse},
        429: {"description": "Upload rate limit exceeded.", "model": ErrorResponse},
    },
)
@limiter.limit(settings.UPLOAD_RATE_LIMIT)
async def upload_meeting(
    request: Request,  # required by slowapi to extract the client IP
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: Optional[User] = Depends(get_current_user),
    queue: Queue = Depends(get_queue),
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

    if not is_audio_file(contents):
        raise HTTPException(400, "Invalid file content. Uploaded file does not contain valid audio data.")

    stored_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, stored_name)
    with open(file_path, "wb") as f:
        f.write(contents)

    meeting = Meeting(
        filename=file.filename or stored_name,
        status="processing",
        owner_id=current_user.id if current_user else None,
        file_path=file_path,
    )
    session.add(meeting)
    session.commit()
    session.refresh(meeting)

    logger.info(
        "upload.accepted meeting_id=%s filename=%s size_mb=%.2f",
        meeting.id,
        meeting.filename,
        size_mb,
    )
    
    # Enqueue task
    job = queue.enqueue(process_meeting_task, meeting.id, file_path)
    meeting.job_id = job.id
    session.add(meeting)
    session.commit()

    return _to_response(meeting)


@router.get(
    "",
    response_model=List[MeetingListItem],
    summary="List all meetings",
    description="Retrieves a list of all meetings owned by the currently authenticated user, ordered by creation date descending.",
    responses={
        200: {"description": "A list of meeting records with basic metadata and preview summaries."},
        401: {"description": "Authentication required.", "model": ErrorResponse},
    },
)
def list_meetings(
    session: Session = Depends(get_session),
    current_user: Optional[User] = Depends(get_current_user),
):
    owner_id = current_user.id if current_user else None
    meetings = session.exec(
        select(Meeting).where(Meeting.owner_id == owner_id).order_by(Meeting.created_at.desc())
    ).all()
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


@router.get(
    "/{meeting_id}",
    response_model=MeetingResponse,
    summary="Get detailed meeting results",
    description="Retrieves the detailed results of a single meeting (including full transcript, segmented timestamps, key decisions, and action items).",
    responses={
        200: {"description": "Detailed meeting results retrieved successfully.", "model": MeetingResponse},
        401: {"description": "Authentication required.", "model": ErrorResponse},
        404: {"description": "Meeting not found or belongs to another user.", "model": ErrorResponse},
    },
)
def get_meeting(
    meeting_id: int,
    session: Session = Depends(get_session),
    current_user: Optional[User] = Depends(get_current_user),
):
    meeting = session.get(Meeting, meeting_id)
    owner_id = current_user.id if current_user else None
    if not meeting or meeting.owner_id != owner_id:
        raise HTTPException(404, "Meeting not found")
    return _to_response(meeting)


@router.delete(
    "/{meeting_id}",
    status_code=204,
    summary="Delete a meeting",
    description="Deletes a meeting record and its associated data.",
    responses={
        204: {"description": "Meeting deleted successfully."},
        401: {"description": "Authentication required.", "model": ErrorResponse},
        404: {"description": "Meeting not found or belongs to another user.", "model": ErrorResponse},
    },
)
def delete_meeting(
    meeting_id: int,
    session: Session = Depends(get_session),
    current_user: Optional[User] = Depends(get_current_user),
):
    meeting = session.get(Meeting, meeting_id)
    owner_id = current_user.id if current_user else None
    if not meeting or meeting.owner_id != owner_id:
        raise HTTPException(404, "Meeting not found")
    session.delete(meeting)
    session.commit()
