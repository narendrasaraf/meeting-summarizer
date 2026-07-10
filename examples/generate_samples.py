#!/usr/bin/env python3
"""
generate_samples.py
───────────────────
Creates 3 short meeting-style WAV clips using the Windows built-in
Text-to-Speech engine (SAPI / System.Speech) — no pip dependencies needed.

Also writes the corresponding hand-crafted ground-truth transcripts to
examples/ground_truth/ so check_accuracy.py can compute WER.

Usage
-----
    python examples/generate_samples.py

Output
------
    examples/clips/sample_01.wav  (Sprint standup,  ~38 s)
    examples/clips/sample_02.wav  (Product review,  ~50 s)
    examples/clips/sample_03.wav  (Q3 planning,     ~70 s)
    examples/ground_truth/sample_01.txt
    examples/ground_truth/sample_02.txt
    examples/ground_truth/sample_03.txt
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
CLIPS_DIR = HERE / "clips"
GT_DIR = HERE / "ground_truth"
CLIPS_DIR.mkdir(exist_ok=True)
GT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Ground-truth texts.
# Designed to stress-test Whisper with:
#   • Proper nouns / names          (common capitalisation mismatches)
#   • Numbers, percentages, dates   (written vs spoken form divergence)
#   • Technical terms               (API, dashboard, pull request …)
# ---------------------------------------------------------------------------
CONVERSATIONS = {
    "sample_01": {
        "title": "Sprint Standup",
        "text": (
            "Alright, let's kick off the sprint planning. "
            "Sarah, what's the status on the API integration? "
            "I finished the auth module yesterday. "
            "I'm still working on the rate limiting — should be done by Wednesday. "
            "Great. I'll start on the frontend dashboard once Sarah's API is ready. "
            "We need to deploy to staging by Friday. Any blockers? "
            "I might need access to the production database schema. "
            "Got it, I'll send that over today. "
            "Let's reconvene Thursday morning at ten AM."
        ),
    },
    "sample_02": {
        "title": "Product Review",
        "text": (
            "Okay, the user dashboard feature is ready for review. "
            "We have about four hundred users in the beta program. "
            "The conversion rate is up twelve percent from last month. "
            "The main issues are loading time on mobile, averaging about three seconds, "
            "and we need to fix the notification bug before the launch. "
            "Decision: we launch next Monday if the mobile performance issue is resolved. "
            "Kevin, can you own the performance fix? "
            "Sure, I'll have a pull request up by tomorrow. "
            "Target is under one point five seconds load time."
        ),
    },
    "sample_03": {
        "title": "Q3 Planning",
        "text": (
            "Let's finalize the Q3 roadmap. "
            "We have three main initiatives: the checkout redesign, "
            "the recommendation engine, and the analytics dashboard. "
            "The checkout redesign is highest priority. "
            "The approved budget is fifty thousand dollars. "
            "Timeline: design complete by July thirty-first, "
            "development runs through September, and we launch in October. "
            "Main risk: the payment processor migration might cause delays. "
            "Action items: Lisa will schedule the design review by July fifteenth. "
            "Mark will send the payment processor API documentation to the team by end of week. "
            "Our next meeting is July seventeenth at two PM."
        ),
    },
}


def _write_ps1(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def generate_wav_windows(text: str, output_path: Path, rate: int = -2) -> None:
    """
    Generate a WAV file via Windows SAPI (System.Speech.Synthesis).
    `rate` ranges from -10 (slowest) to 10 (fastest); -2 gives clear speech.
    """
    # Escape single-quotes for PowerShell here-string
    safe_text = text.replace("'", "''")
    safe_out = str(output_path.resolve()).replace("\\", "/")

    ps_content = f"""
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = {rate}
$synth.SetOutputToWaveFile('{safe_out}')
$synth.Speak('{safe_text}')
$synth.Dispose()
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(ps_content)
        ps1_path = tmp.name

    try:
        result = subprocess.run(
            [
                "powershell",
                "-ExecutionPolicy", "Bypass",
                "-NonInteractive",
                "-File", ps1_path,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  SAPI error: {result.stderr.strip()}", file=sys.stderr)
            raise RuntimeError("PowerShell SAPI failed")
    finally:
        os.unlink(ps1_path)


def main() -> None:
    if sys.platform != "win32":
        print("generate_samples.py uses Windows SAPI and must run on Windows.")
        print("On macOS/Linux use 'say' or 'espeak' to generate clips manually.")
        sys.exit(1)

    for stem, info in CONVERSATIONS.items():
        wav_path = CLIPS_DIR / f"{stem}.wav"
        gt_path = GT_DIR / f"{stem}.txt"

        # Ground-truth transcript
        gt_path.write_text(info["text"].strip(), encoding="utf-8")
        print(f"[ground truth] {gt_path.name}")

        # Audio clip
        print(f"[generating]   {wav_path.name}  ({info['title']}) ...", end=" ", flush=True)
        generate_wav_windows(info["text"], wav_path)
        size_kb = wav_path.stat().st_size // 1024
        print(f"done  ({size_kb} KB)")

    print("\nAll clips generated. Next step:")
    print("  python examples/run_pipeline.py")


if __name__ == "__main__":
    main()
