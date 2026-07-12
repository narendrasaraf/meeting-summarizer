#!/usr/bin/env python3
"""
cost_estimate.py
────────────────
Estimates the API cost and wall-clock latency for each sample meeting
processed by the Meeting Summarizer pipeline.

Reads the saved examples/results/<stem>.json files produced by run_pipeline.py.
No network calls — all calculations are deterministic arithmetic on the
fields already present in the JSON (duration_seconds, asr_seconds,
summary_seconds, transcript word count).

Provider rate table
--------------------
Rates are hardcoded below in PROVIDERS dict and clearly dated. Update the
dict entry (and the date comment) whenever pricing changes.

Current rates (source / date noted per entry):
  openai  : OpenAI Whisper-1 ASR + gpt-4o-mini summarization
  groq    : Groq whisper-large-v3 ASR + llama-3.3-70b-versatile summarization
  gemini  : Gemini 2.0 Flash (multimodal audio + text summarization)

Token count estimation
-----------------------
The saved JSON contains the full transcript text but not raw token counts.
We estimate tokens as  words * 1.33  (a standard rough ratio for English;
real token counts will vary by ~10-15% depending on punctuation and word length).
Prompt overhead is estimated at 400 tokens input + 200 tokens output for the
system prompt and JSON schema wrapper used by summarizer.py.

Usage
-----
    python examples/cost_estimate.py
    python examples/cost_estimate.py --results examples/results
    python examples/cost_estimate.py --provider groq
    python examples/cost_estimate.py --scale 10   # project cost for a 10-min meeting
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
RESULTS_DIR = HERE / "results"

# ---------------------------------------------------------------------------
# Provider rate table
# Rates sourced from publicly listed pricing pages.
# Update these values (and the date comment) when pricing changes.
# ---------------------------------------------------------------------------
#
# ASR pricing is per MINUTE of audio.
# LLM pricing is per 1,000,000 tokens (input and output separately).

PROVIDERS: dict = {
    "openai": {
        # Source: https://openai.com/api/pricing/  — checked 2026-07-12
        "label": "OpenAI  (whisper-1 + gpt-4o-mini)",
        "asr_per_min":       0.006,     # USD/min  — Whisper-1 transcription API
        "llm_input_per_mtok": 0.150,    # USD/Mtok — gpt-4o-mini input
        "llm_output_per_mtok": 0.600,   # USD/Mtok — gpt-4o-mini output
        "free_asr": False,
        "free_llm": False,
    },
    "groq": {
        # Source: https://console.groq.com/settings/billing  — checked 2026-07-12
        "label": "Groq    (whisper-large-v3 + llama-3.3-70b)",
        "asr_per_min":       0.00185,   # USD/min  — $0.111/hr / 60
        "llm_input_per_mtok": 0.590,    # USD/Mtok — llama-3.3-70b input
        "llm_output_per_mtok": 0.790,   # USD/Mtok — llama-3.3-70b output
        "free_asr": False,
        "free_llm": False,
    },
    "gemini": {
        # Source: https://ai.google.dev/pricing  — checked 2026-07-12
        # Gemini 2.0 Flash: free under the free-tier quota (15 RPM / 1M TPD).
        # Paid tier: audio input = $0.70/Mtok at 25 tokens/sec;
        # text in/out = $0.10 / $0.40 per Mtok (gemini-2.0-flash paid).
        # We model ASR as audio-token billing: 25 tok/sec -> USD cost below.
        "label": "Gemini  (gemini-2.0-flash audio + summarization)",
        "asr_per_min":       0.00105,   # USD/min  — 25 tok/sec * 60s * $0.70/Mtok
        "llm_input_per_mtok": 0.100,    # USD/Mtok — text input
        "llm_output_per_mtok": 0.400,   # USD/Mtok — text output
        "free_asr": True,               # free under quota
        "free_llm": True,               # free under quota
    },
}

PRICING_DATE = "2026-07-12"

# ---------------------------------------------------------------------------
# Token estimation helpers
# ---------------------------------------------------------------------------

WORDS_TO_TOKENS = 1.33      # rough English conversion ratio
PROMPT_INPUT_OVERHEAD = 400  # tokens: system prompt + schema + user message wrapper
PROMPT_OUTPUT_OVERHEAD = 200 # tokens: JSON framing tokens in the response

def _estimate_tokens(text: str) -> tuple:
    """
    Returns (input_tokens, output_tokens) estimate for the summarization call.

    input  = transcript tokens + system prompt overhead
    output = transcript tokens * 0.35 (typical compression ratio) + JSON overhead
    """
    words = len(text.split())
    transcript_tokens = int(words * WORDS_TO_TOKENS)
    input_tokens = transcript_tokens + PROMPT_INPUT_OVERHEAD
    output_tokens = int(transcript_tokens * 0.35) + PROMPT_OUTPUT_OVERHEAD
    return input_tokens, output_tokens


def _cost(provider_key: str, duration_seconds: float, transcript: str) -> dict:
    """Calculate estimated cost breakdown for one meeting."""
    p = PROVIDERS[provider_key]
    duration_min = duration_seconds / 60.0

    asr_cost = duration_min * p["asr_per_min"]

    inp_tok, out_tok = _estimate_tokens(transcript)
    llm_cost = (
        inp_tok  / 1_000_000 * p["llm_input_per_mtok"]
        + out_tok / 1_000_000 * p["llm_output_per_mtok"]
    )
    total = asr_cost + llm_cost

    return {
        "duration_min": duration_min,
        "asr_cost": asr_cost,
        "llm_cost": llm_cost,
        "total": total,
        "input_tokens": inp_tok,
        "output_tokens": out_tok,
        "free_tier": p.get("free_asr") and p.get("free_llm"),
    }


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def _fmt_cost(usd: float) -> str:
    if usd < 0.001:
        return f"${usd * 1000:.4f}m"  # show in milli-dollars if tiny
    return f"${usd:.5f}"


def _print_table(rows: list, provider_key: str, scale_min: float) -> None:
    col = 80
    p = PROVIDERS[provider_key]
    print(f"\n{'-' * col}")
    print(f"  Provider : {p['label']}")
    print(f"  Rates    : ASR ${p['asr_per_min']:.5f}/min | "
          f"LLM ${p['llm_input_per_mtok']:.3f}/${p['llm_output_per_mtok']:.3f} per Mtok (in/out)")
    print(f"  Pricing date: {PRICING_DATE}  "
          f"(estimates only — see script docstring)")
    if p["free_asr"] or p["free_llm"]:
        print(f"  NOTE: this provider has a free tier; costs shown are paid-tier rates.")
    print(f"{'-' * col}")
    print(
        f"  {'Sample':<20} {'Audio(min)':>10} {'ASR$':>9} {'LLM$':>9} "
        f"{'Total$':>9} {'ASR(s)':>8} {'LLM(s)':>8}"
    )
    print(f"{'-' * col}")

    total_cost = 0.0
    total_dur = 0.0
    total_asr_s = 0.0
    total_sum_s = 0.0

    for row in rows:
        c = row["cost"]
        total_cost += c["total"]
        total_dur  += c["duration_min"]
        total_asr_s += row.get("asr_seconds") or 0.0
        total_sum_s += row.get("summary_seconds") or 0.0

        asr_s = f"{row['asr_seconds']:.1f}s" if row.get("asr_seconds") is not None else "n/a"
        sum_s = f"{row['summary_seconds']:.1f}s" if row.get("summary_seconds") is not None else "n/a"
        print(
            f"  {row['stem']:<20} {c['duration_min']:>10.2f} "
            f"{_fmt_cost(c['asr_cost']):>9} {_fmt_cost(c['llm_cost']):>9} "
            f"{_fmt_cost(c['total']):>9} {asr_s:>8} {sum_s:>8}"
        )

    print(f"{'-' * col}")
    n = len(rows)
    avg_asr_s  = total_asr_s / n if n else 0
    avg_sum_s  = total_sum_s / n if n else 0
    avg_dur    = total_dur   / n if n else 0
    avg_cost   = total_cost  / n if n else 0

    print(
        f"  {'AVERAGE':<20} {avg_dur:>10.2f} "
        f"{'':>9} {'':>9} "
        f"{_fmt_cost(avg_cost):>9} {avg_asr_s:>7.1f}s {avg_sum_s:>7.1f}s"
    )
    print(f"{'-' * col}\n")

    # Projection for a 10-minute meeting
    asr_rate = total_cost / total_dur if total_dur else 0  # $/min of audio
    projected = asr_rate * scale_min
    print(f"  Projected cost for a {scale_min:.0f}-min meeting : {_fmt_cost(projected)}")

    if total_asr_s and total_dur:
        proc_ratio = (total_asr_s / n) / (avg_dur * 60)
        print(f"  Avg ASR processing time per minute of audio  : {proc_ratio:.2f}x real-time")
    if total_sum_s:
        print(f"  Avg summarization latency                    : {avg_sum_s:.1f}s")
    print()
    print("  Columns: ASR$ = transcription cost | LLM$ = summarization cost")
    print("           ASR(s) = wall-clock ASR time | LLM(s) = wall-clock summary time")
    print("  Token estimate: words * 1.33 + 400 tok prompt overhead")
    print(f"  ** Prices are estimates based on published rates as of {PRICING_DATE}.")
    print(f"     Actual billing may differ. Verify current rates before budgeting.")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estimate API cost and latency for Meeting Summarizer samples."
    )
    parser.add_argument("--results", type=Path, default=RESULTS_DIR)
    parser.add_argument(
        "--provider", choices=list(PROVIDERS.keys()), default=None,
        help="Show estimates for this provider only (default: show all providers)",
    )
    parser.add_argument(
        "--scale", type=float, default=10.0,
        help="Projected meeting length in minutes for the cost-per-meeting estimate (default: 10)",
    )
    args = parser.parse_args()

    result_files = sorted(args.results.glob("*.json"))
    if not result_files:
        print(f"No result files found in {args.results}.")
        print("Run:  python examples/run_pipeline.py")
        sys.exit(1)

    rows = []
    for result_path in result_files:
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  Warning: could not load {result_path.name}: {exc}")
            continue

        duration = data.get("duration_seconds")
        transcript = (data.get("transcript") or "").strip()
        if not duration or not transcript:
            print(f"  Warning: {result_path.stem} missing duration or transcript — skipped.")
            continue

        rows.append({
            "stem": result_path.stem,
            "duration_seconds": duration,
            "transcript": transcript,
            "asr_seconds": data.get("asr_seconds"),
            "summary_seconds": data.get("summary_seconds"),
        })

    if not rows:
        print("No usable samples found (all missing duration or transcript).")
        sys.exit(1)

    providers_to_show = [args.provider] if args.provider else list(PROVIDERS.keys())

    for pkey in providers_to_show:
        for row in rows:
            row["cost"] = _cost(pkey, row["duration_seconds"], row["transcript"])
        _print_table(rows, pkey, args.scale)


if __name__ == "__main__":
    main()
