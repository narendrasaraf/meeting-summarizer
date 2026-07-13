import os
import time
import logging
from datetime import datetime, timezone, timedelta
from sqlmodel import Session, select

from app.models.db import engine, Meeting

logger = logging.getLogger("app.cleanup")


def cleanup_old_meetings() -> None:
    """Deletes meetings and their audio files older than 60 minutes."""
    logger.info("Starting periodic cleanup job for old meetings...")
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=60)
    
    try:
        with Session(engine) as session:
            old_meetings = session.exec(
                select(Meeting).where(Meeting.created_at < cutoff)
            ).all()
            
            if not old_meetings:
                logger.info("No expired meetings found to clean up.")
                return
                
            logger.warning("Found %d meeting(s) older than 60 minutes. Deleting...", len(old_meetings))
            for meeting in old_meetings:
                # Delete audio file from disk if it exists
                if meeting.file_path and os.path.exists(meeting.file_path):
                    try:
                        os.remove(meeting.file_path)
                        logger.info("Deleted audio file: %s", meeting.file_path)
                    except Exception as exc:
                        logger.error("Failed to delete audio file at %s: %s", meeting.file_path, exc)
                
                # Delete database record
                session.delete(meeting)
                logger.info("Deleted meeting record id=%d", meeting.id)
                
            session.commit()
            logger.info("Cleanup job completed successfully.")
    except Exception as exc:
        logger.error("Error during old meetings cleanup: %s", exc, exc_info=True)


def run_cleanup_loop(interval_seconds: int = 600) -> None:
    """Infinite loop for the cleanup daemon thread."""
    while True:
        try:
            cleanup_old_meetings()
        except Exception:
            pass
        time.sleep(interval_seconds)
