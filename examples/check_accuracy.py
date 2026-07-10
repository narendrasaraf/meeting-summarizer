#!/usr/bin/env python3
"""
check_accuracy.py
─────────────────
Computes Word Error Rate (WER) between hand-written ground-truth transcripts
and Whisper transcripts produced by the Meeting Summarizer pipeline.

WER = (Substitutions + Deletions + Insertions) / Reference_word_count

Uses a pure-Python Wagner-Fischer dynamic-programming implementation —
no external dependencies required.

Directory layout expected
-------------------------
    examples/ground_truth/<stem>.txt   ← hand-written reference
    examples/results/<stem>.json       ← API response saved by run_pipeline.py

Usage
-----
    python examples/check_accuracy.py
    python examples/check_accuracy.py --ground-truth examples/ground_truth
    python examples/check_accuracy.py --verbose      # shows word-level diffs
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).parent
GT_DIR = HERE / "ground_truth"
RESULTS_DIR = HERE / "results"


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """
    Prepare text for WER comparison:
      1. Lowercase
      2. Strip all punctuation except apostrophes (preserves contractions)
      3. Collapse whitespace
    """
    text = text.lower()
    text = re.sub(r"[^\w\s']", " ", text)       # remove punct except '
    text = re.sub(r"\s*'\s*", "'", text)          # normalise apostrophe spacing
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# WER via Wagner-Fischer DP
# ---------------------------------------------------------------------------

@dataclass
class WerResult:
    substitutions: int
    deletions: int
    insertions: int
    ref_len: int
    hyp_len: int

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def wer(self) -> float:
        if self.ref_len == 0:
            return 0.0 if self.hyp_len == 0 else float("inf")
        return self.errors / self.ref_len

    @property
    def accuracy(self) -> float:
        return max(0.0, 1.0 - self.wer)


def compute_wer(reference: str, hypothesis: str) -> WerResult:
    """Return detailed WER breakdown between two strings."""
    ref = _normalise(reference).split()
    hyp = _normalise(hypothesis).split()

    n, m = len(ref), len(hyp)

    # DP table
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(
                    dp[i - 1][j],       # deletion  (ref word missing in hyp)
                    dp[i][j - 1],       # insertion (extra word in hyp)
                    dp[i - 1][j - 1],   # substitution
                )

    # Backtrace to classify errors
    i, j = n, m
    subs = dels = ins = 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            i -= 1; j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            subs += 1; i -= 1; j -= 1
        elif j > 0 and dp[i][j] == dp[i][j - 1] + 1:
            ins += 1; j -= 1
        else:
            dels += 1; i -= 1

    return WerResult(
        substitutions=subs,
        deletions=dels,
        insertions=ins,
        ref_len=n,
        hyp_len=m,
    )


# ---------------------------------------------------------------------------
# Diff display (verbose mode)
# ---------------------------------------------------------------------------

def _diff_words(reference: str, hypothesis: str) -> list[str]:
    """Return a coloured word-level alignment (REF | HYP)."""
    ref = _normalise(reference).split()
    hyp = _normalise(hypothesis).split()
    n, m = len(ref), len(hyp)

    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): dp[i][0] = i
    for j in range(m + 1): dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i-1] == hyp[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = 1 + min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1])

    lines = []
    i, j = n, m
    ops = []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i-1] == hyp[j-1]:
            ops.append(("ok", ref[i-1], hyp[j-1])); i -= 1; j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i-1][j-1] + 1:
            ops.append(("sub", ref[i-1], hyp[j-1])); i -= 1; j -= 1
        elif j > 0 and dp[i][j] == dp[i][j-1] + 1:
            ops.append(("ins", "", hyp[j-1])); j -= 1
        else:
            ops.append(("del", ref[i-1], "")); i -= 1

    ops.reverse()
    for op, r, h in ops:
        if op == "ok":
            lines.append(f"  {r}")
        elif op == "sub":
            lines.append(f"  SUB  [{r}] -> [{h}]")
        elif op == "ins":
            lines.append(f"  INS       + [{h}]")
        else:
            lines.append(f"  DEL  [{r}]")
    return lines


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Compute WER for Meeting Summarizer samples.")
    parser.add_argument("--ground-truth", type=Path, default=GT_DIR)
    parser.add_argument("--results", type=Path, default=RESULTS_DIR)
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print word-level diff for each sample")
    args = parser.parse_args()

    gt_files = sorted(args.ground_truth.glob("*.txt"))
    if not gt_files:
        print(f"No ground-truth files found in {args.ground_truth}.")
        print("Run:  python examples/generate_samples.py")
        sys.exit(1)

    print(f"\n{'-'*68}")
    print(f"  {'Sample':<20} {'Ref':>6} {'Hyp':>6} {'S':>5} {'D':>5} {'I':>5} {'WER':>8}")
    print(f"{'-'*68}")

    total_errors = 0
    total_ref = 0
    missing = []

    for gt_path in gt_files:
        stem = gt_path.stem
        result_path = args.results / f"{stem}.json"

        reference = gt_path.read_text(encoding="utf-8").strip()

        if not result_path.exists():
            missing.append(stem)
            print(f"  {stem:<20} {'(no result yet)':>40}")
            continue

        data = json.loads(result_path.read_text(encoding="utf-8"))
        hypothesis = (data.get("transcript") or "").strip()

        if not hypothesis:
            print(f"  {stem:<20} {'(transcript empty -- pipeline failed?)':>40}")
            continue

        r = compute_wer(reference, hypothesis)
        total_errors += r.errors
        total_ref += r.ref_len

        wer_str = f"{r.wer * 100:.1f}%"
        print(
            f"  {stem:<20} {r.ref_len:>6} {r.hyp_len:>6} "
            f"{r.substitutions:>5} {r.deletions:>5} {r.insertions:>5} {wer_str:>8}"
        )

        if args.verbose:
            print()
            for line in _diff_words(reference, hypothesis):
                print(f"    {line}")
            print()

    print(f"{'-'*68}")
    if total_ref > 0:
        avg_wer = total_errors / total_ref
        print(f"  {'OVERALL':<20} {total_ref:>6} {'':>6} {'':>5} {'':>5} {'':>5} {avg_wer*100:>7.1f}%")
    print(f"{'-'*68}\n")

    if missing:
        print(f"  Tip: {len(missing)} result(s) missing. Run:")
        print("    python examples/run_pipeline.py\n")

    print("Columns: Ref=reference words  Hyp=hypothesis words")
    print("         S=substitutions  D=deletions  I=insertions")
    print(f"         WER = (S+D+I) / Ref\n")


if __name__ == "__main__":
    main()
