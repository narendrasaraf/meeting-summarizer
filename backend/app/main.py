"""
Meeting Summarizer API — entrypoint.

Run with: uvicorn app.main:app --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from sqlmodel import Session
from app.models.schemas import ErrorResponse, ErrorDetail

from app.core.config import settings
from app.limiter import limiter
from app.models.db import init_db, get_session
from app.routers import meetings, auth

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


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    code = "UNAUTHORIZED" if exc.status_code == 401 else \
           "FORBIDDEN" if exc.status_code == 403 else \
           "NOT_FOUND" if exc.status_code == 404 else \
           "RATE_LIMIT_EXCEEDED" if exc.status_code == 429 else \
           "BAD_REQUEST"
    message = exc.detail
    if isinstance(message, dict):
        return JSONResponse(status_code=exc.status_code, content=message)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": str(message),
            "error": {
                "code": code,
                "message": str(message),
                "field": None
            }
        }
    )


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc: RateLimitExceeded):
    message = "Rate limit exceeded. Please try again later."
    return JSONResponse(
        status_code=429,
        content={
            "detail": message,
            "error": {
                "code": "RATE_LIMIT_EXCEEDED",
                "message": message,
                "field": None
            }
        }
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    errors = exc.errors()
    msg = "Validation failed"
    field = None
    if errors:
        first = errors[0]
        field_path = ".".join(str(loc) for loc in first.get("loc", []))
        msg = f"Field '{field_path}': {first.get('msg')}"
        field = field_path

    return JSONResponse(
        status_code=422,
        content={
            "detail": msg,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": msg,
                "field": field
            }
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc: Exception):
    logger = logging.getLogger("app.main")
    logger.exception("Unhandled server error: %s", exc)
    message = "An unexpected error occurred on the server."
    return JSONResponse(
        status_code=500,
        content={
            "detail": message,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": message,
                "field": None
            }
        }
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["health"])
def health_check(session: Session = Depends(get_session)):
    import os
    from sqlmodel import text

    # 1. Verify DB connectivity
    db_status = "ok"
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error: {exc}"

    # 2. Verify configured ASR/LLM provider API key
    provider_status = "ok"
    if not settings.SIMULATE_MODE:
        prov = settings.PROVIDER.lower()
        if prov == "openai" and not settings.OPENAI_API_KEY:
            provider_status = "missing_api_key"
        elif prov == "groq" and not settings.GROQ_API_KEY:
            provider_status = "missing_api_key"
        elif prov == "gemini" and not settings.GEMINI_API_KEY:
            provider_status = "missing_api_key"
        elif prov == "google" and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            provider_status = "missing_credentials"
        elif prov == "azure" and (not settings.AZURE_SPEECH_KEY or not settings.AZURE_SPEECH_REGION):
            provider_status = "missing_credentials"

    is_healthy = db_status == "ok" and provider_status == "ok"
    status_code = 200 if is_healthy else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if is_healthy else "error",
            "database": db_status,
            "provider": provider_status,
            "provider_name": settings.PROVIDER,
            "simulate_mode": settings.SIMULATE_MODE,
            "auth_required": settings.AUTH_REQUIRED,
        }
    )


app.include_router(meetings.router)
app.include_router(auth.router)
