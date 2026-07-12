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
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from sqlmodel import Session, select

from app.core.config import settings
from app.limiter import limiter
from app.models.db import Meeting, get_session
from app.models.schemas import ActionItem, MeetingListItem, MeetingResponse
from app.services.asr import TranscriptionError, transcribe_audio
from app.services.summarizer import SummarizationError, summarize_transcript

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

# limiter is the shared instance from app.limiter (same object used in app.main).
# Using the same instance ensures .reset() in tests clears all counters and that
# the exception handler registered in main.py covers limits applied here.


def _process_meeting(meeting_id: int, file_path: str) -> None:
    """Background job: transcribe then summarize, updating the DB row."""
    from sqlmodel import Session as _Session
    from app.models.db import engine

    logger.info(
        "pipeline.start meeting_id=%s file=%s",
        meeting_id,
        os.path.basename(file_path),
    )

    # ── SIMULATE MODE: skip all real API calls ──────────────────────────────────
    if settings.SIMULATE_MODE:
        logger.warning(
            "SIMULATE_MODE is enabled — meeting_id=%s will receive SIMULATED content. "
            "Set SIMULATE_MODE=false and supply a real API key for live transcription.",
            meeting_id,
        )
        import json as _json
        with _Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            if not meeting:
                return
            meeting.transcript = (
                "[SIMULATED — no live transcription] "
                "This is placeholder text generated in SIMULATE_MODE. "
                "No audio was sent to any speech-recognition API."
            )
            meeting.duration_seconds = None
            meeting.summary = (
                "[SIMULATED — no live transcription] "
                "Demo summary: the meeting covered placeholder topics. "
                "Enable a real provider (OPENAI_API_KEY / GROQ_API_KEY / GEMINI_API_KEY) "
                "and set SIMULATE_MODE=false to get a real summary."
            )
            meeting.key_decisions_json = _json.dumps(
                ["[SIMULATED] No real decisions — SIMULATE_MODE is active"]
            )
            meeting.action_items_json = _json.dumps(
                [{"task": "[SIMULATED] Disable SIMULATE_MODE and add a real API key",
                  "owner": "Unassigned", "due_date": None, "priority": "high"}]
            )
            meeting.status = "completed"
            meeting.updated_at = datetime.now(timezone.utc)
            session.add(meeting)
            session.commit()
            if os.path.exists(file_path):
                os.remove(file_path)
            logger.info("pipeline.simulate.finish meeting_id=%s", meeting_id)
        return
    # ─────────────────────────────────────────────────────────────────────────────

    with _Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        if not meeting:
            logger.error("pipeline.abort meeting_id=%s reason=record_not_found", meeting_id)
            return
        try:
            # ── Stage 1: ASR ──────────────────────────────────────────────────
            stage = "asr"
            logger.info("pipeline.asr.start meeting_id=%s", meeting_id)
            _asr_t0 = time.perf_counter()
            asr_result = transcribe_audio(file_path, original_filename=meeting.filename)
            meeting.asr_seconds = round(time.perf_counter() - _asr_t0, 3)
            meeting.transcript = asr_result["text"]
            meeting.duration_seconds = asr_result.get("duration")
            if asr_result.get("segments"):
                meeting.segments_json = json.dumps(asr_result["segments"])
            session.add(meeting)
            session.commit()
            logger.info(
                "pipeline.asr.done meeting_id=%s duration_s=%.1f chars=%d asr_wall_s=%.2f",
                meeting_id,
                meeting.duration_seconds or 0.0,
                len(meeting.transcript),
                meeting.asr_seconds,
            )


            # ── Stage 2: Summarisation ────────────────────────────────────────
            stage = "summarization"
            logger.info(
                "pipeline.summarize.start meeting_id=%s transcript_chars=%d",
                meeting_id,
                len(meeting.transcript),
            )
            _sum_t0 = time.perf_counter()
            summary_result = summarize_transcript(meeting.transcript)
            meeting.summary_seconds = round(time.perf_counter() - _sum_t0, 3)
            meeting.summary = summary_result["summary"]
            meeting.key_decisions_json = json.dumps(summary_result["key_decisions"])
            meeting.action_items_json = json.dumps(summary_result["action_items"])
            meeting.status = "completed"
            logger.info(
                "pipeline.summarize.done meeting_id=%s decisions=%d actions=%d summary_wall_s=%.2f",
                meeting_id,
                len(summary_result["key_decisions"]),
                len(summary_result["action_items"]),
                meeting.summary_seconds,
            )

        except (TranscriptionError, SummarizationError) as exc:
            meeting.status = "failed"
            meeting.error_message = str(exc)
            logger.error(
                "pipeline.failed meeting_id=%s stage=%s error=%s",
                meeting_id,
                stage,
                exc,
                exc_info=True,
            )
        except Exception as exc:  # noqa: BLE001
            meeting.status = "failed"
            meeting.error_message = f"Unexpected error: {exc}"
            logger.exception(
                "pipeline.unexpected_error meeting_id=%s stage=%s",
                meeting_id,
                stage,
            )
        finally:
            meeting.updated_at = datetime.now(timezone.utc)
            session.add(meeting)
            session.commit()
            if os.path.exists(file_path):
                os.remove(file_path)
            logger.info(
                "pipeline.finish meeting_id=%s status=%s",
                meeting_id,
                meeting.status,
            )


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



@router.post("", response_model=MeetingResponse, status_code=202)
@limiter.limit(settings.UPLOAD_RATE_LIMIT)
async def upload_meeting(
    request: Request,  # required by slowapi to extract the client IP
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

    logger.info(
        "upload.accepted meeting_id=%s filename=%s size_mb=%.2f",
        meeting.id,
        meeting.filename,
        size_mb,
    )
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
