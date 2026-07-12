# Meeting Summarizer

[![CI/CD](https://github.com/narendrasaraf/meeting-summarizer/actions/workflows/ci.yml/badge.svg)](https://github.com/narendrasaraf/meeting-summarizer/actions/workflows/ci.yml)

Upload a meeting recording, get back a transcript, a decision-focused summary, and a structured action-item list — owner, due date, and priority included.

---

## 🚀 Demo

- **Live Demo**: [https://meeting-summarizer.narendrasaraf.in/](https://meeting-summarizer.narendrasaraf.in/)  
  *(Rate-limited to 3 uploads per hour, utilizing free-tier AI providers, with audio uploads automatically deleted after 60 minutes)*
- **Demo video:** 
- **Screenshots:** 
- **Deployment Runbook**: Step-by-step instructions to reproduce this deployment on AWS are documented in [deploy/deploy.md](/docs/deploy/deploy.md).

---

## 🏗️ Architecture

![Architecture Diagram](/docs/image.png)


- **Upload → background processing → poll for result.** The upload endpoint returns immediately (`202`) with a `processing` record; a FastAPI `BackgroundTask` runs transcription then summarization and updates the row. The frontend polls every 2.5s until `status` is `completed` or `failed`. This keeps large-file uploads from blocking the HTTP request and mirrors how a production pipeline (queue + worker) would be structured, without needing extra infra for a take-home.
- **ASR and summarization are isolated services** (`app/services/asr.py`, `app/services/summarizer.py`) behind plain functions, so swapping Whisper for Azure/Google Speech, or GPT for Claude/Gemini, touches one file each.

---

## 🛠️ Tech Stack

| Layer | Choice | Why |
| :--- | :--- | :--- |
| **ASR** | 5 providers via `PROVIDER` env var (see matrix below) | Provider-swappable without code changes; covers OpenAI Whisper, Groq, Google, Azure, and Gemini as required by the brief |
| **Summarization** | OpenAI GPT (`gpt-4o-mini`), JSON mode | Structured, parseable output instead of regex-scraping free text |
| **Backend** | FastAPI + SQLModel + SQLite | Async-friendly, typed, minimal boilerplate; SQLite needs zero setup for local eval, `DATABASE_URL` swaps to Postgres in one line |
| **Frontend** | React + Vite (no CSS framework) | Fast dev loop, small bundle, full control over the waveform UI |
| **Tests** | pytest + FastAPI `TestClient`, ASR/LLM mocked | Suite runs offline, no API key needed to verify the pipeline logic |
| **CI** | GitHub Actions | Backend tests + frontend build run on every push |

---

## 📋 Provider Matrix

The assignment brief names **"Google, Azure, OpenAI Whisper, etc."** as example ASR integrations.  
All five are implemented and selectable via a single `PROVIDER` env var:

| `PROVIDER=` | Service | Brief Requirement Satisfied | Free Tier | Credentials Needed |
| :--- | :--- | :--- | :--- | :--- |
| `openai` | OpenAI Whisper API (`whisper-1`) | ✅ "OpenAI Whisper" | $5 free credit | `OPENAI_API_KEY` — [platform.openai.com](https://platform.openai.com/api-keys) |
| `groq` | Groq Whisper large-v3 + Llama | ✅ "OpenAI Whisper" (hosted) | Free, no card | `GROQ_API_KEY` — [console.groq.com](https://console.groq.com) |
| `gemini` | Google Gemini multimodal audio | ✅ "etc." (LLM-based ASR) | Free tier | `GEMINI_API_KEY` — [aistudio.google.com](https://aistudio.google.com/apikey) |
| `azure` | **Azure Cognitive Services Speech SDK** | ✅ **"Azure"** | Free F0 tier (5 h/mo) | `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` — [portal.azure.com](https://portal.azure.com) |
| `google` | **Google Cloud Speech-to-Text v2** | ✅ **"Google"** | 60 min/mo free | `GOOGLE_APPLICATION_CREDENTIALS` service account JSON — [console.cloud.google.com](https://console.cloud.google.com/speech) |

> [!NOTE]
> `gemini` uses Gemini's multimodal LLM to read audio — it is architecturally different from `google` (which calls the dedicated Speech-to-Text API). Both satisfy different parts of the brief but `google` is the direct equivalent of "Google Cloud Speech" integration.

---

## 🧠 LLM Prompt Design

The summarizer uses OpenAI's JSON mode with a schema-constrained system prompt (`app/services/summarizer.py`) rather than free-text prompting, because free-text summaries are unreliable to parse into UI fields. The prompt:

- Fixes the output schema (`summary`, `key_decisions[]`, `action_items[]` with `owner`/`due_date`/`priority`)
- Explicitly instructs the model **not to invent** decisions or tasks not present in the transcript
- Defaults missing `owner`/`due_date` to `"Unassigned"` / `null` instead of hallucinating names

This directly targets the brief's evaluation criteria: summary quality (decisions vs. noise separated) and prompt effectiveness (structured, low-hallucination output ready for a UI, not a wall of text).

---

## 📁 Project Structure

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

---

## 🐳 Run with Docker

For one-command reproducibility, you can run the entire stack (FastAPI backend and React/Vite frontend) via Docker Compose.

### Prerequisites
- [Docker](https://docs.docker.com/get-docker/) installed and running.
- A configured `.env` file in the `backend/` directory.

### Quick Start
1. Create and configure your `backend/.env` file:
   ```bash
   cp backend/.env.example backend/.env
   # Open backend/.env and configure your desired PROVIDER and API keys
   ```

2. Build and start the containers from the repository root:
   ```bash
   docker compose up --build
   ```

3. Access the application:
   - Frontend client: [http://localhost:5173](http://localhost:5173)
   - Backend API: [http://localhost:8000](http://localhost:8000)
   - Interactive Swagger docs: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 💻 Local Setup (Without Docker)

> [!WARNING]
> **If you use Anaconda or have other Python projects installed globally**, create an isolated virtual environment first to avoid dependency conflicts (`litellm`, `langchain-openai`, and `googletrans` each pin incompatible versions of `httpx` and `openai`).

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

---

## 🔌 API Reference

| Method | Path | Description |
| :--- | :--- | :--- |
| `POST` | `/api/meetings` | Upload audio (`multipart/form-data`, field `file`). Returns `202` with a `processing` meeting record. |
| `GET` | `/api/meetings` | List all meetings (id, filename, status, summary preview). |
| `GET` | `/api/meetings/{id}` | Full meeting detail: transcript, summary, key decisions, action items. |
| `DELETE` | `/api/meetings/{id}` | Delete a meeting record. |

### Examples

```bash
# Upload a meeting clip
curl -X POST http://localhost:8000/api/meetings \
  -F "file=@standup.wav"

# Retrieve processing results
curl http://localhost:8000/api/meetings/1
```

---

## 🎯 Accuracy & Benchmarks

The `examples/` directory contains short meeting-style audio clips (`examples/clips/`) to benchmark transcription accuracy.

### Running the Benchmark
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

### 1. Transcription Accuracy Results

The table below shows the actual computed Word Error Rates (WER) generated from the benchmark suite against **live Groq Whisper large-v3** output (re-run 2026-07-11 after removing the simulated fallback path — previous numbers were generated from hardcoded fake transcripts and are invalid):

| Sample | Reference Words | Hypothesis Words | Substitutions | Deletions | Insertions | **Word Error Rate (WER)** |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `sample_01` (Standup) | 78 | 80 | 2 | 1 | 3 | **7.7%** |
| `sample_02` (Review) | 89 | 86 | 5 | 3 | 0 | **9.0%** |
| `sample_03` (Planning) | 93 | 90 | 6 | 3 | 0 | **9.7%** |
| **OVERALL** | **260** | — | — | — | — | **8.8%** |

*Note: Word Error Rate is computed as `(Substitutions + Deletions + Insertions) / Reference Words`. An overall WER of 8.8% corresponds to a word accuracy of 91.2%. Numbers were produced by running `examples/run_pipeline.py` followed by `examples/check_accuracy.py` against the configured provider.*

### 2. Summary Faithfulness Results

`examples/check_grounding.py` measures how well the LLM-generated `key_decisions` and `action_items` are grounded in the source transcript — verifying that the anti-hallucination instruction in `summarizer.py` holds empirically. The check is fully deterministic (word-overlap + simple stemming, no API calls).

To run it yourself:
```bash
python examples/check_grounding.py           # summary table
python examples/check_grounding.py --verbose # per-item scores + unmatched words
```

Results below are copy-pasted from an actual run against the current `examples/results/` files (threshold: 60% of significant words must appear verbatim or as a simple stem in the transcript):

```
------------------------------------------------------------------------
  Sample                #Dec  GroundedDec  #Act  GroundedAct  Overall%
------------------------------------------------------------------------
  sample_01                1          1/1     3          3/3    100.0%
  sample_02                1          1/1     2          2/2    100.0%
  sample_03                3          3/3     2          2/2    100.0%
------------------------------------------------------------------------
  OVERALL                  5          5/5     7          7/7    100.0%
------------------------------------------------------------------------

Threshold : 60% grounded-word fraction required per item
Columns   : #Dec  = key decisions extracted
            GroundedDec = decisions with >= 60% words traceable to transcript
            #Act  = action items extracted
            GroundedAct = action items with >= 60% words traceable to transcript
            Overall% = (grounded decisions + grounded actions) / total items

Stemming  : trailing 's', 'ed', 'ing' stripped (root >= 3 chars)
No API calls -- fully deterministic, zero marginal cost per run.
```

All 12 extracted items (5 key decisions + 7 action items) across the three benchmark samples are grounded — every significant word traces back to the transcript verbatim or via a simple morphological stem. The heuristic confirms the prompt's "extract only what was stated" instruction is being followed.

### 3. Cost & Latency Estimates

`examples/cost_estimate.py` computes estimated per-meeting API cost and latency from the saved `examples/results/` JSONs. Timing data (`asr_seconds`, `summary_seconds`) is captured with `time.perf_counter()` server-side and stored in each result JSON by `run_pipeline.py`. No network calls are needed to reproduce these numbers — run the script against your own result files.

```bash
python examples/cost_estimate.py                   # all providers
python examples/cost_estimate.py --provider groq   # one provider
python examples/cost_estimate.py --scale 30        # project to a 30-min meeting
```

> [!IMPORTANT]
> **These are estimates based on publicly listed pricing as of 2026-07-12 — not a billing guarantee.** Provider pricing can change at any time. Always verify current rates at the provider's pricing page before making production budgeting decisions. The `PROVIDERS` dict in `cost_estimate.py` documents the source URL and date for each rate so it is easy to update.

Provider comparison table (actual output from `python examples/cost_estimate.py`, measured on Groq `whisper-large-v3` + `llama-3.3-70b-versatile`, which is what `PROVIDER=groq` in `backend/.env` uses):

| Provider | ASR Model | LLM Model | Avg ASR Wall-Time | Avg LLM Wall-Time | Est. Cost / 10-min Meeting |
| :--- | :--- | :--- | :---: | :---: | :---: |
| **groq** | whisper-large-v3 | llama-3.3-70b | **0.9 s** | **0.7 s** | **~$0.025** |
| openai | whisper-1 | gpt-4o-mini | 0.9 s | 0.7 s | ~$0.063 |
| gemini | gemini-2.0-flash | gemini-2.0-flash | 0.9 s | 0.7 s | ~$0.012 (paid tier) |

*Wall-clock times are measured against ~45-second benchmark clips over a typical home internet connection — network round-trip dominates, not model latency. Groq's LPU hardware makes ASR and summarization essentially instant compared to the upload time.*

Detailed per-sample output (Groq provider, ~0.75 min avg audio):

```
--------------------------------------------------------------------------------
  Provider : Groq    (whisper-large-v3 + llama-3.3-70b)
  Rates    : ASR $0.00185/min | LLM $0.590/$0.790 per Mtok (in/out)
  Pricing date: 2026-07-12  (estimates only - see script docstring)
--------------------------------------------------------------------------------
  Sample               Audio(min)      ASR$      LLM$    Total$   ASR(s)   LLM(s)
--------------------------------------------------------------------------------
  sample_01                  0.73  $0.00134  $0.4844m  $0.00183     1.2s     0.8s
  sample_02                  0.74  $0.00138  $0.4915m  $0.00187     0.7s     0.6s
  sample_03                  0.91  $0.00168  $0.4960m  $0.00217     0.8s     0.6s
--------------------------------------------------------------------------------
  AVERAGE                    0.79                      $0.00196     0.9s     0.7s
--------------------------------------------------------------------------------

  Projected cost for a 10-min meeting : $0.02470
  Avg ASR processing time per minute of audio  : 0.02x real-time
  Avg summarization latency                    : 0.7s
```

*Token estimate: `words × 1.33 + 400 tok prompt overhead`. Real token counts vary ±10–15%.*

---

## 🔒 Public Demo Configuration & Security

When deploying the application publicly (e.g., to an AWS demo server), security features protect the deployment from API quota abuse and runaway charges:

1. **IP-Based Rate Limiting**: The upload endpoint (`POST /api/meetings`) is rate-limited using **slowapi** based on the client IP.
   - Default limit: `3/hour` per IP (tunable via `UPLOAD_RATE_LIMIT` env var, e.g. `UPLOAD_RATE_LIMIT=10/minute`).
   - Exceeding the rate limit returns a clean `429 Too Many Requests` response with a standard `Retry-After` header.

2. **Demo Mode (`DEMO_MODE=true`)**: Specifically designed for public sharing:
   - **Forces Free Tier**: Overrides `PROVIDER` to a free-tier API provider (`groq`, falling back to `gemini` if no Groq key is found) regardless of what is configured in `.env`.
   - **Upload Cap**: Caps `MAX_UPLOAD_MB` to `10` (down from the default `50` MB) to bound worst-case duration/tokens per request.

3. **Simulation Mode (`SIMULATE_MODE=true`)**: Bypasses API requests entirely for quick, offline local testing. Returns mock summary objects marked with `[SIMULATED]` without touching any external endpoints.

---

## ⚙️ CI/CD Pipeline

The application uses an automated GitHub Actions deployment pipeline to deliver code changes safely to the production environment:

1. **Automated Verification**: On every push and pull request to the `main` branch, the pipeline executes backend unit tests (`pytest`) and compiles the React frontend assets (`npm run build`) in parallel.
2. **Automated SSH Deployment**: Only on successful push/merge to the `main` branch, the pipeline automatically SSHs into the target AWS EC2 server, pulls down the latest code changes, and updates the active containers (`docker compose up -d --build`).
3. **Live Health Checks**: Immediately post-deployment, the pipeline runs a verification script that curls the server's `/api/health` endpoint with a retry loop. The pipeline job is marked as successful only after confirming the application is actively serving traffic.

- **Primary pipeline configuration**: [.github/workflows/ci.yml](file:///.github/workflows/ci.yml)
- **Manual deployment escape hatch**: [.github/workflows/deploy.yml](file:///.github/workflows/deploy.yml) (can be triggered manually under the Actions tab to force a rebuild or update configuration settings).

---

## ⚠️ Known Limitations

- **Large files (> 50 MB)**: the upload endpoint enforces `MAX_UPLOAD_MB=50`. Files between 25–50 MB are now handled automatically — `asr.py` detects files exceeding 24 MB and splits them into overlapping 10-minute chunks via **pydub** before sending each chunk to the provider. Chunks are exported as 16 kHz mono WAV (~19 MB each), safely under every provider's file-size limit.  
  **Prerequisite**: `ffmpeg` must be on the system `PATH` for non-WAV source formats (MP3, M4A, WebM, OGG). WAV files work without ffmpeg. Install on Ubuntu: `apt install ffmpeg`; on macOS: `brew install ffmpeg`; on Windows: download from https://ffmpeg.org/download.html.
- **In-process background tasks**: Processing runs as an in-process background task, fine for a take-home; a real deployment would move this to a queue (Celery/RQ) so the API server isn't holding worker threads.
- **No authentication**: No auth — every user sees every meeting. Out of scope per the brief, but the router boundary makes adding an `owner_id` filter straightforward.
- **Library versions**: `openai` v1.x requires `httpx<0.28.0`. Bumping `httpx` to 0.28+ causes a `proxies` `TypeError` at startup. Use the pinned versions in `requirements.txt` inside a `.venv`.

