#!/usr/bin/env python3
"""
run_pipeline.py
───────────────
Sends every audio clip in examples/clips/ through the running Meeting
Summarizer backend, polls until each job completes, and saves the full
API response JSON + raw transcript text to examples/results/.

Each saved <stem>.json now includes:
  asr_seconds     — wall-clock time for the transcription stage
  summary_seconds — wall-clock time for the summarization stage

These fields are captured server-side with time.perf_counter() and
included in the API response. They are used by cost_estimate.py.

Prerequisites
-------------
  1. Backend running:
         cd backend && uvicorn app.main:app --reload
  2. A valid OPENAI_API_KEY (or GROQ_API_KEY + PROVIDER=groq) in backend/.env
  3. Audio clips in examples/clips/  (run generate_samples.py first, or add
     your own .wav / .mp3 / .m4a files)

Usage
-----
    python examples/run_pipeline.py
    python examples/run_pipeline.py --base-url http://localhost:8000
    python examples/run_pipeline.py --poll-interval 3 --timeout 300

Next steps after running:
    python examples/check_accuracy.py    # Word Error Rate
    python examples/check_grounding.py   # Summary Faithfulness
    python examples/cost_estimate.py     # Cost & Latency estimates
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
CLIPS_DIR = HERE / "clips"
RESULTS_DIR = HERE / "results"
RESULTS_DIR.mkdir(exist_ok=True)

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg", ".flac"}


# ---------------------------------------------------------------------------
# Minimal HTTP helpers (stdlib only — no httpx/requests needed)
# ---------------------------------------------------------------------------

def _post_multipart(url: str, file_path: Path) -> dict:
    """Upload an audio file as multipart/form-data using urllib."""
    boundary = "----MeetingSummarizerBoundary7829"
    file_bytes = file_path.read_bytes()
    mime = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".webm": "audio/webm",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }.get(file_path.suffix.lower(), "application/octet-stream")

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def process_clip(
    clip: Path,
    base_url: str,
    poll_interval: float,
    timeout: float,
) -> dict | None:
    """Upload one clip, poll until done, return the final meeting dict."""
    upload_url = f"{base_url.rstrip('/')}/api/meetings"

    print(f"\n  Uploading {clip.name} ...", end=" ", flush=True)
    try:
        meeting = _post_multipart(upload_url, clip)
    except urllib.error.URLError as exc:
        print(f"\n  [FAIL]  Upload failed -- is the backend running? ({exc})")
        return None

    meeting_id = meeting["id"]
    print(f"queued as meeting #{meeting_id}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        meeting = _get_json(f"{upload_url}/{meeting_id}")
        status = meeting.get("status", "unknown")
        print(f"  ... {status}", end="\r", flush=True)
        if status in ("completed", "failed"):
            break

    print(f"  -> {status}          ")
    return meeting


def save_results(clip: Path, meeting: dict) -> None:
    stem = clip.stem
    # Full JSON response
    json_path = RESULTS_DIR / f"{stem}.json"
    json_path.write_text(json.dumps(meeting, indent=2, default=str), encoding="utf-8")
    print(f"  Saved: {json_path.relative_to(HERE.parent)}")

    # Bare transcript (for WER comparison)
    transcript = meeting.get("transcript") or ""
    txt_path = RESULTS_DIR / f"{stem}_transcript.txt"
    txt_path.write_text(transcript, encoding="utf-8")
    print(f"  Saved: {txt_path.relative_to(HERE.parent)}")

    if meeting["status"] == "failed":
        print(f"  [FAIL]  Pipeline error: {meeting.get('error_message')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run example clips through the Meeting Summarizer API.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--poll-interval", type=float, default=2.5)
    parser.add_argument("--timeout", type=float, default=300,
                        help="Max seconds to wait per clip (default 300)")
    args = parser.parse_args()

    clips = sorted(p for p in CLIPS_DIR.iterdir() if p.suffix.lower() in AUDIO_EXTS)
    if not clips:
        print(f"No audio clips found in {CLIPS_DIR}.")
        print("Run:  python examples/generate_samples.py")
        sys.exit(1)

    # Sanity-check server
    try:
        _get_json(f"{args.base_url}/api/health")
    except urllib.error.URLError:
        print(f"Cannot reach backend at {args.base_url}.")
        print("Start it with:  cd backend && uvicorn app.main:app --reload")
        sys.exit(1)

    print(f"Backend OK -- processing {len(clips)} clip(s) ...")
    for clip in clips:
        meeting = process_clip(clip, args.base_url, args.poll_interval, args.timeout)
        if meeting:
            save_results(clip, meeting)

    print("\nDone. Next steps:")
    print("  python examples/check_accuracy.py    # WER benchmark")
    print("  python examples/check_grounding.py   # summary faithfulness")
    print("  python examples/cost_estimate.py     # cost & latency estimates")


if __name__ == "__main__":
    main()
