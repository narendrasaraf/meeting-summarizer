"""
Meeting Summarizer API — entrypoint.

Run with: uvicorn app.main:app --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.limiter import limiter
from app.models.db import init_db
from app.routers import meetings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# Rate limiter — keyed by client IP, shared across all routers via app.state.
# Defined in app.limiter to avoid circular imports with the meetings router.
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Replaces deprecated @app.on_event('startup')."""
    init_db()
    yield


app = FastAPI(
    title="Meeting Summarizer API",
    description="Transcribes meeting audio and generates action-oriented summaries.",
    version="1.0.0",
    lifespan=lifespan,
)

# Attach the limiter to app.state so slowapi can find it.
app.state.limiter = limiter
# Register the standard 429 handler (returns JSON with Retry-After header).
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["health"])
def health_check():
    return {"status": "ok"}


app.include_router(meetings.router)
