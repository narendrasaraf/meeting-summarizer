import os
import sys
import logging

# Ensure root of the backend is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from redis import Redis
from rq import Worker, Queue, Connection

from app.core.config import settings
from app.models.db import init_db
from app.workers.recovery import recover_stuck_jobs
from app.workers.cleanup import run_cleanup_loop
import threading

# Configure worker logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("app.worker")

if __name__ == "__main__":
    logger.info("Starting RQ worker container...")
    
    # Initialize DB (creates tables / schema migration if needed)
    init_db()
    
    # Run startup recovery routine for stuck jobs
    recover_stuck_jobs()
    
    # Start cleanup daemon thread (runs every 10 minutes)
    logger.info("Starting background meeting cleanup daemon thread...")
    cleanup_thread = threading.Thread(target=run_cleanup_loop, args=(600,), daemon=True)
    cleanup_thread.start()
    
    # Run the worker process
    logger.info("Connecting to Redis at %s and starting worker listening on default queue...", settings.REDIS_URL)
    try:
        redis_conn = Redis.from_url(settings.REDIS_URL)
        with Connection(redis_conn):
            worker = Worker(Queue("default"))
            worker.work()
    except Exception as exc:
        logger.critical("Fatal error running RQ worker: %s", exc, exc_info=True)
        sys.exit(1)
