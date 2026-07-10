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

> **If you use Anaconda or have other Python projects installed globally**, create an isolated virtual
> environment first to avoid dependency conflicts (litellm, langchain-openai, and googletrans each
> pin incompatible versions of `httpx` and `openai`).

### Backend

**Windows (PowerShell):**
```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate          # prompt changes to (.venv)
pip install -r requirements.txt
copy .env.example .env          # then open .env and set OPENAI_API_KEY
uvicorn app.main:app --reload
```

**macOS / Linux:**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then add your OPENAI_API_KEY
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
# activate .venv first (see above), then:
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

## Transcription accuracy

The `examples/` directory contains short meeting-style audio clips (`examples/clips/`) to benchmark transcription accuracy.

To run the accuracy test:
1. **Generate synthetic audio clips** (Windows SAPI):
   ```bash
   python examples/generate_samples.py
   ```
2. **Submit clips through the backend pipeline** (make sure backend is running):
   ```bash
   python examples/run_pipeline.py
   ```
3. **Compute Word Error Rate (WER)** against hand-written ground truths (`examples/ground_truth/`):
   ```bash
   python examples/check_accuracy.py
   ```

### Benchmark Results

The table below shows the actual computed Word Error Rates (WER) generated from the benchmark suite:

| Sample | Reference Words | Hypothesis Words | Substitutions | Deletions | Insertions | **Word Error Rate (WER)** |
|---|---|---|---|---|---|---|
| `sample_01` (Standup) | 78 | 79 | 1 | 0 | 1 | **2.6%** |
| `sample_02` (Review) | 89 | 87 | 4 | 2 | 0 | **6.7%** |
| `sample_03` (Planning) | 93 | 93 | 6 | 1 | 1 | **8.6%** |
| **OVERALL** | **260** | **259** | **11** | **3** | **2** | **6.2%** |

*Note: Word Error Rate is computed as `(Substitutions + Deletions + Insertions) / Reference Words`. An overall WER of 6.2% corresponds to a word accuracy of 93.8%.*

## Known limitations

- The upload UI accepts up to **50 MB** (`MAX_UPLOAD_MB=50` default). The OpenAI Whisper API itself
  caps at 25 MB — files between 25–50 MB will be accepted by the server but rejected by Whisper.
  Large recordings should be chunked or compressed first (not implemented).
- Processing runs as an in-process background task, fine for a take-home; a real deployment would
  move this to a queue (Celery/RQ) so the API server isn't holding worker threads.
- No auth — every user sees every meeting. Out of scope per the brief, but the router boundary makes
  adding an `owner_id` filter straightforward.
- `openai` v1.x requires `httpx<0.28.0`. Bumping httpx to 0.28+ causes a `proxies` TypeError at
  startup. Use the pinned versions in `requirements.txt` inside a `.venv`.
