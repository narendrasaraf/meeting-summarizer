"""
Meeting Summarizer API — entrypoint.

Run with: uvicorn app.main:app --reload
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.models.db import init_db
from app.routers import meetings

app = FastAPI(
    title="Meeting Summarizer API",
    description="Transcribes meeting audio and generates action-oriented summaries.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/api/health", tags=["health"])
def health_check():
    return {"status": "ok"}


app.include_router(meetings.router)
