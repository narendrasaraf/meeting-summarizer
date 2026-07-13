import os
import logging
from datetime import datetime, timezone, timedelta
from sqlmodel import Session, select
from redis import Redis
from rq import Queue

from app.core.config import settings
from app.models.db import engine, Meeting
from app.workers.pipeline import process_meeting_task

logger = logging.getLogger(__name__)


def recover_stuck_jobs() -> None:
    """Startup crash recovery routine for jobs lost on restart."""
    logger.info("Starting stuck jobs recovery routine...")
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.RECOVERY_TIMEOUT_MINUTES)

    try:
        redis_conn = Redis.from_url(settings.REDIS_URL)
        queue = Queue("default", connection=redis_conn)
    except Exception as exc:
        logger.error("Could not connect to Redis for startup recovery: %s", exc)
        return

    with Session(engine) as session:
        stuck_meetings = session.exec(
            select(Meeting)
            .where(Meeting.status == "processing")
            .where(Meeting.created_at < cutoff)
        ).all()

        if not stuck_meetings:
            logger.info("No stuck jobs found to recover.")
            return

        logger.warning("Found %d stuck meeting(s) to recover. Processing...", len(stuck_meetings))

        for meeting in stuck_meetings:
            if meeting.file_path and os.path.exists(meeting.file_path):
                logger.info("Requeuing stuck meeting_id=%d file=%s", meeting.id, meeting.file_path)
                try:
                    job = queue.enqueue(process_meeting_task, meeting.id, meeting.file_path)
                    meeting.job_id = job.id
                    session.add(meeting)
                except Exception as exc:
                    logger.error("Failed to enqueue recovery job for meeting_id=%d: %s", meeting.id, exc)
            else:
                logger.error(
                    "Cannot recover stuck meeting_id=%d: audio file not found at %s. Marking as failed.",
                    meeting.id,
                    meeting.file_path,
                )
                meeting.status = "failed"
                meeting.error_message = "File not found during crash recovery"
                session.add(meeting)

        session.commit()
        logger.info("Recovery routine completed.")
