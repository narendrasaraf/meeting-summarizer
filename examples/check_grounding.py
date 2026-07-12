#!/usr/bin/env python3
"""
check_grounding.py
──────────────────
Measures "summary faithfulness" — how well the LLM-produced key_decisions
and action_items are grounded in the source transcript.

Motivation
----------
backend/app/services/summarizer.py uses an anti-hallucination instruction
("Extract only decisions/action items actually stated..."). This script
verifies that instruction holds empirically, using a deterministic heuristic
that requires zero API calls and is fully reproducible.

Algorithm
---------
For each key_decision string and each action_item "task" string:

1. Tokenise: lowercase, strip punctuation, split on whitespace.
2. Remove stopwords (hardcoded list below — common English function words
   that carry no domain meaning and would trivially match anything).
3. For each remaining "significant" word, check whether it or a simple
   morphological stem appears verbatim anywhere in the transcript.
   Stems: strip a trailing "s", "ed", or "ing" (in that priority order)
   if the resulting stem is >= 3 characters.  No external NLP library needed.
4. grounding_score = matched_words / total_significant_words
5. A decision/action item is "grounded" if grounding_score > THRESHOLD.

Threshold
---------
THRESHOLD = 0.6 (60 %)

Rationale: a legitimate extraction can reasonably paraphrase ~40% of its
words (e.g. "Fix the mobile performance issue" -> "mobile performance issue"
appeared verbatim, while "Fix" was not said literally but "resolved" was).
Below 60% the item contains too many words with no trace in the transcript,
suggesting fabrication or hallucination.  The threshold was chosen by manual
inspection of the three benchmark samples; at 0.6 all true positives pass
and near-paraphrases still score above 0.6 on their significant content words.

Directory layout expected
--------------------------
    examples/results/<stem>.json   <- API response saved by run_pipeline.py

The JSON must contain at least:
    "transcript"    : str
    "key_decisions" : list[str]
    "action_items"  : list[{"task": str, ...}]

Usage
-----
    python examples/check_grounding.py
    python examples/check_grounding.py --results examples/results
    python examples/check_grounding.py --verbose    # shows failing items + missing words
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
RESULTS_DIR = HERE / "results"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

THRESHOLD = 0.6  # minimum grounded-word fraction to call an item "grounded"

# Hardcoded English stopwords (no external dependency).
# These are deterministic — adding/removing a word here changes scores.
STOPWORDS: frozenset = frozenset(
    {
        "a", "an", "the",
        "and", "but", "or", "nor", "for", "yet", "so",
        "in", "on", "at", "to", "for", "of", "with", "by", "from",
        "up", "about", "into", "through", "during",
        "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did",
        "will", "would", "could", "should", "may", "might", "shall",
        "i", "we", "you", "he", "she", "it", "they",
        "me", "us", "him", "her", "them",
        "my", "our", "your", "his", "its", "their",
        "this", "that", "these", "those",
        "if", "then", "than", "so", "as", "also",
        "not", "no", "only",
        "all", "any", "each", "every", "both", "few", "more", "most",
        "other", "such", "own", "same", "just", "very",
        "can", "need", "let",
    }
)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _tokenise(text):
    """Lowercase + strip punctuation + split."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)   # drop punctuation
    return [t for t in text.split() if t]


def _significant_words(text):
    """Return tokens with stopwords removed."""
    return [w for w in _tokenise(text) if w not in STOPWORDS]


def _simple_stem(word):
    """
    Strip a trailing 's', 'ed', or 'ing' to get a crude morphological root.
    The stem must remain >= 3 characters; otherwise return the original word.
    Priority order: 'ing' first (longest suffix), then 'ed', then 's'.
    """
    for suffix in ("ing", "ed", "s"):
        if word.endswith(suffix):
            root = word[: -len(suffix)]
            if len(root) >= 3:
                return root
    return word


def _build_transcript_tokens(transcript):
    """
    Build a lookup set containing every significant token in the transcript
    *and* each token's stem, so we can match both verbatim and stem-matched
    words in O(1) per query.
    """
    tokens = _tokenise(transcript)
    expanded = set()
    for tok in tokens:
        expanded.add(tok)
        expanded.add(_simple_stem(tok))
    return frozenset(expanded)


# ---------------------------------------------------------------------------
# Grounding check
# ---------------------------------------------------------------------------

def _check_item(text, transcript_tokens):
    """
    Return (grounding_score, list_of_unmatched_words).

    grounding_score = matched / total_significant   (or 1.0 if no sig. words)
    """
    sig = _significant_words(text)
    if not sig:
        return 1.0, []   # empty / all stopwords -> trivially grounded

    unmatched = []
    for word in sig:
        # Check verbatim match OR stem match
        if word in transcript_tokens or _simple_stem(word) in transcript_tokens:
            pass
        else:
            unmatched.append(word)

    matched = len(sig) - len(unmatched)
    score = matched / len(sig)
    return score, unmatched


def analyse_sample(stem, data, verbose, threshold):
    """
    Analyse one result JSON.

    Returns (n_decisions, n_grounded_decisions, n_action_items, n_grounded_actions).
    """
    transcript = (data.get("transcript") or "").strip()
    key_decisions = data.get("key_decisions") or []
    action_items = data.get("action_items") or []

    if not transcript:
        print(f"  [{stem}] WARNING: transcript is empty, skipping grounding check.")
        return 0, 0, 0, 0

    transcript_tokens = _build_transcript_tokens(transcript)

    n_dec = len(key_decisions)
    n_dec_ok = 0
    n_act = len(action_items)
    n_act_ok = 0

    if verbose and (n_dec or n_act):
        print(f"\n  -- {stem} --")

    # --- Key decisions ---
    for dec in key_decisions:
        score, unmatched = _check_item(dec, transcript_tokens)
        is_grounded = score >= threshold
        if is_grounded:
            n_dec_ok += 1
        if verbose:
            tag = "OK" if is_grounded else "FAIL"
            print(f"    [{tag}] DECISION  ({score:.0%}) : {dec!r}")
            if not is_grounded and unmatched:
                print(f"         unmatched words: {unmatched}")

    # --- Action items ---
    for item in action_items:
        task = (item.get("task") or "").strip()
        if not task:
            continue
        score, unmatched = _check_item(task, transcript_tokens)
        is_grounded = score >= threshold
        if is_grounded:
            n_act_ok += 1
        if verbose:
            tag = "OK" if is_grounded else "FAIL"
            print(f"    [{tag}] ACTION    ({score:.0%}) : {task!r}")
            if not is_grounded and unmatched:
                print(f"         unmatched words: {unmatched}")

    return n_dec, n_dec_ok, n_act, n_act_ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Check grounding of LLM-generated summaries against transcripts."
    )
    parser.add_argument("--results", type=Path, default=RESULTS_DIR)
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print each decision/action item with its grounding score and any unmatched words",
    )
    parser.add_argument(
        "--threshold", type=float, default=THRESHOLD,
        help=f"Minimum grounding fraction to count as grounded (default: {THRESHOLD})",
    )
    args = parser.parse_args()

    threshold = args.threshold

    result_files = sorted(args.results.glob("*.json"))
    if not result_files:
        print(f"No result files found in {args.results}.")
        print("Run:  python examples/run_pipeline.py")
        sys.exit(1)

    # Table header -- mirror check_accuracy.py visual style
    col = 72
    print(f"\n{'-' * col}")
    print(
        f"  {'Sample':<20} {'#Dec':>5} {'GroundedDec':>12} "
        f"{'#Act':>5} {'GroundedAct':>12} {'Overall%':>9}"
    )
    print(f"{'-' * col}")

    total_dec = total_dec_ok = total_act = total_act_ok = 0

    for result_path in result_files:
        stem = result_path.stem

        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  {stem:<20} (failed to load: {exc})")
            continue

        n_dec, n_dec_ok, n_act, n_act_ok = analyse_sample(
            stem, data, verbose=args.verbose, threshold=threshold
        )

        total_dec += n_dec
        total_dec_ok += n_dec_ok
        total_act += n_act
        total_act_ok += n_act_ok

        total_items = n_dec + n_act
        total_ok = n_dec_ok + n_act_ok
        pct = (total_ok / total_items * 100) if total_items else float("nan")

        dec_str = f"{n_dec_ok}/{n_dec}"
        act_str = f"{n_act_ok}/{n_act}"
        pct_str = f"{pct:.1f}%" if total_items else "n/a"

        print(
            f"  {stem:<20} {n_dec:>5} {dec_str:>12} "
            f"{n_act:>5} {act_str:>12} {pct_str:>9}"
        )

    print(f"{'-' * col}")

    # Overall row
    grand_total = total_dec + total_act
    grand_ok = total_dec_ok + total_act_ok
    overall_pct = (grand_ok / grand_total * 100) if grand_total else float("nan")
    overall_dec_str = f"{total_dec_ok}/{total_dec}"
    overall_act_str = f"{total_act_ok}/{total_act}"
    overall_pct_str = f"{overall_pct:.1f}%"

    print(
        f"  {'OVERALL':<20} {total_dec:>5} {overall_dec_str:>12} "
        f"{total_act:>5} {overall_act_str:>12} {overall_pct_str:>9}"
    )
    print(f"{'-' * col}\n")

    print(f"Threshold : {threshold:.0%} grounded-word fraction required per item")
    pct_label = f"{threshold:.0%}"
    print(f"Columns   : #Dec  = key decisions extracted")
    print(f"            GroundedDec = decisions with >= {pct_label} words traceable to transcript")
    print(f"            #Act  = action items extracted")
    print(f"            GroundedAct = action items with >= {pct_label} words traceable to transcript")
    print(f"            Overall% = (grounded decisions + grounded actions) / total items\n")
    print(f"Stemming  : trailing 's', 'ed', 'ing' stripped (root >= 3 chars)")
    print(f"No API calls -- fully deterministic, zero marginal cost per run.\n")


if __name__ == "__main__":
    main()
