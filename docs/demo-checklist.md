# Demo video checklist

Keep it under 3-4 minutes. Suggested flow:

1. **Intro (10s)** — "This is a meeting summarizer: upload audio, get a transcript, summary, and action items."
2. **Show the upload** — drag a short (30-60s) sample audio clip (a voice memo works fine) onto the dropzone.
3. **Show processing state** — point out the polling/status badge going `processing → completed`.
4. **Walk through the result** — summary, key decisions, action items with owner/priority, then expand the full transcript.
5. **Show history sidebar** — click a past meeting to show persistence (SQLite).
6. **Quick code tour (30-60s)** — open `app/services/summarizer.py`, show the JSON-mode prompt; open `app/routers/meetings.py`, show the background-task flow.
7. **Run the tests on camera** — `pytest -v` passing is a strong, fast credibility signal.
8. **Close** — mention what you'd add with more time (queue-based processing, chunking for >25MB files, auth).

Record with OBS / Loom / QuickTime screen recording. Upload to YouTube (unlisted) or Loom and paste the link into the README's Demo section.
