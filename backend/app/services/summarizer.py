"""
LLM summarization service.

Takes a raw meeting transcript and produces a structured summary:
overview, key decisions, and action items (with owner/due date/priority
extracted where mentioned). Uses OpenAI's JSON mode for a reliable,
parseable structure instead of regex-scraping free text.
"""
import json

from openai import OpenAI

from app.core.config import settings

_client = OpenAI(api_key=settings.OPENAI_API_KEY)

SYSTEM_PROMPT = """You are an expert meeting analyst. Given a raw meeting \
transcript, produce a concise, action-oriented summary.

Respond with STRICT JSON only, matching this exact schema, no prose outside JSON:
{
  "summary": "2-4 sentence high-level overview of what the meeting covered",
  "key_decisions": ["decision 1", "decision 2", ...],
  "action_items": [
    {"task": "...", "owner": "name or 'Unassigned'", "due_date": "date or null", "priority": "high|medium|low"}
  ]
}

Rules:
- Extract only decisions/action items actually stated or clearly implied in the transcript.
- If no owner or due date is mentioned for a task, use "Unassigned" and null respectively.
- If the transcript has no clear action items, return an empty list, don't invent tasks.
- Keep the summary factual and free of filler.
"""


class SummarizationError(Exception):
    pass


def summarize_transcript(transcript: str) -> dict:
    """
    Summarizes a transcript into overview + key decisions + action items.

    Returns:
        dict with keys: summary (str), key_decisions (list[str]),
        action_items (list[dict])
    """
    if not transcript or not transcript.strip():
        return {"summary": "", "key_decisions": [], "action_items": []}

    try:
        response = _client.chat.completions.create(
            model=settings.SUMMARY_MODEL,
            response_format={"type": "json_object"},
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Summarize this meeting transcript into key "
                    f"decisions and action items:\n\n{transcript}",
                },
            ],
        )
        content = response.choices[0].message.content
        parsed = json.loads(content)
        return {
            "summary": parsed.get("summary", ""),
            "key_decisions": parsed.get("key_decisions", []),
            "action_items": parsed.get("action_items", []),
        }
    except Exception as exc:  # noqa: BLE001
        raise SummarizationError(f"Summarization failed: {exc}") from exc
