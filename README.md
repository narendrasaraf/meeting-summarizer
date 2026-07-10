# Meeting Summarizer

Upload a meeting recording, get back a transcript, a decision-focused summary, and a structured action-item list — owner, due date, and priority included.

Built for the "Meeting Summarizer" assessment brief: ASR integration + LLM summarization + a backend to store/process results + an optional frontend.

## Demo

- **Demo video:** _[add link here after recording — see `docs/demo-checklist.md`]_
- **Screenshots:** _[optional — drop into `docs/` and link here]_

## Architecture

```
┌─────────────┐      multipart/form-data       ┌──────────────────────┐
│   React     │ ───────────────────────────────▶│   FastAPI backend    │
│  frontend   │                                  │                      │
│ (Vite, JS)  │◀──── poll GET /api/meetings/:id ─│  1. save audio file  │
└─────────────┘                                  │  2. Whisper ASR      │
                                                  │  3. GPT summarizer   │
                                                  │  4. persist (SQLite) │
                                                  └──────────┬───────────┘
                                                             │
                                                     OpenAI Whisper + GPT
```

- **Upload → background processing → poll for result.** The upload endpoint returns immediately (`202`) with a `processing` record; a FastAPI `BackgroundTask` runs transcription then summarization and updates the row. The frontend polls every 2.5s until `status` is `completed` or `failed`. This keeps large-file uploads from blocking the HTTP request and mirrors how a production pipeline (queue + worker) would be structured, without needing extra infra for a take-home.
- **ASR and summarization are isolated services** (`app/services/asr.py`, `app/services/summarizer.py`) behind plain functions, so swapping Whisper for Azure/Google Speech, or GPT for Claude/Gemini, touches one file each.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| ASR | OpenAI Whisper API (`whisper-1`) | Best accuracy/cost tradeoff for a hosted API, no local model weights to manage |
| Summarization | OpenAI GPT (`gpt-4o-mini`), JSON mode | Structured, parseable output instead of regex-scraping free text |
| Backend | FastAPI + SQLModel + SQLite | Async-friendly, typed, minimal boilerplate; SQLite needs zero setup for local eval, `DATABASE_URL` swaps to Postgres in one line |
| Frontend | React + Vite (no CSS framework) | Fast dev loop, small bundle, full control over the waveform UI |
| Tests | pytest + FastAPI `TestClient`, ASR/LLM mocked | Suite runs offline, no API key needed to verify the pipeline logic |
| CI | GitHub Actions | Backend tests + frontend build run on every push |

## LLM prompt design

The summarizer uses OpenAI's JSON mode with a schema-constrained system prompt (`app/services/summarizer.py`) rather than free-text prompting, because free-text summaries are unreliable to parse into UI fields. The prompt:

- Fixes the output schema (`summary`, `key_decisions[]`, `action_items[]` with `owner`/`due_date`/`priority`)
- Explicitly instructs the model **not to invent** decisions or tasks not present in the transcript
- Defaults missing `owner`/`due_date` to `"Unassigned"` / `null` instead of hallucinating names

This directly targets the brief's evaluation criteria: summary quality (decisions vs. noise separated) and prompt effectiveness (structured, low-hallucination output ready for a UI, not a wall of text).

## Project structure

```
meeting-summarizer/
├── backend/
│   ├── app/
│   │   ├── core/config.py        # env-driven settings
│   │   ├── models/db.py          # SQLModel schema + session
│   │   ├── models/schemas.py     # API response models
│   │   ├── services/asr.py       # Whisper transcription
│   │   ├── services/summarizer.py# GPT structured summarization
│   │   ├── routers/meetings.py   # upload / poll / list / delete
│   │   └── main.py               # FastAPI app
│   ├── tests/test_api.py
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/UploadDropzone.jsx
│   │   ├── components/MeetingResult.jsx
│   │   ├── components/HistoryList.jsx
│   │   ├── App.jsx
│   │   └── api.js
│   └── package.json
└── .github/workflows/ci.yml
```

## Setup

### Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # then add your OPENAI_API_KEY
uvicorn app.main:app --reload
```

Backend runs at `http://localhost:8000`. Interactive API docs at `http://localhost:8000/docs`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend runs at `http://localhost:5173` and proxies `/api` calls to the backend.

### Tests

```bash
cd backend
pytest -v
```

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/meetings` | Upload audio (`multipart/form-data`, field `file`). Returns `202` with a `processing` meeting record. |
| `GET` | `/api/meetings` | List all meetings (id, filename, status, summary preview). |
| `GET` | `/api/meetings/{id}` | Full meeting detail: transcript, summary, key decisions, action items. |
| `DELETE` | `/api/meetings/{id}` | Delete a meeting record. |

Example:

```bash
curl -X POST http://localhost:8000/api/meetings \
  -F "file=@standup.wav"

curl http://localhost:8000/api/meetings/1
```

## Known limitations

- Whisper API caps uploads at 25MB per OpenAI's limits — large recordings should be chunked or compressed first (not implemented here to keep scope tight).
- Processing runs as an in-process background task, fine for a take-home; a real deployment would move this to a queue (Celery/RQ) so the API server isn't holding worker threads.
- No auth — every user sees every meeting. Out of scope per the brief, but the router boundary makes adding an `owner_id` filter straightforward.
# meeting-summarizer
