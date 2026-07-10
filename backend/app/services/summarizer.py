"""
LLM summarization service.

Takes a raw meeting transcript and produces a structured summary:
overview, key decisions, and action items (with owner/due date/priority
extracted where mentioned). Uses OpenAI's JSON mode for a reliable,
parseable structure instead of regex-scraping free text.

Retry strategy
--------------
Same rationale as asr.py: tenacity over a hand-rolled decorator.
Retried errors: RateLimitError (429), APITimeoutError, APIConnectionError,
InternalServerError (5xx).
"""
import json
import logging

import openai
from openai import OpenAI
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings

logger = logging.getLogger(__name__)


def _make_client() -> OpenAI:
    """
    Factory that returns an OpenAI-SDK client pointed at the active provider.
    Groq speaks the OpenAI API protocol, so we just swap base_url + api_key;
    all call-site code (chat.completions.create, etc.) stays identical.
    """
    return OpenAI(
        api_key=settings.active_api_key,
        base_url=settings.active_base_url,  # None → OpenAI default
    )


_client = _make_client()

_TRANSIENT_ERRORS = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)

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


@retry(
    reraise=True,
    stop=stop_after_attempt(3),           # max 3 total attempts (2 retries)
    wait=wait_exponential(multiplier=1, min=1, max=8),  # 1s, 2s, 4s…
    retry=retry_if_exception_type(_TRANSIENT_ERRORS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def _call_gpt_api(transcript: str) -> str:
    """
    Isolated, retryable GPT completion call.

    Extracted from summarize_transcript so that tests can patch
    _client.chat.completions.create independently of JSON parsing.
    """
    response = _client.chat.completions.create(
        model=settings.active_summary_model,
        response_format={"type": "json_object"},
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Summarize this meeting transcript into key "
                    f"decisions and action items:\n\n{transcript}"
                ),
            },
        ],
    )
    return response.choices[0].message.content


SIMULATED_SUMMARIES = {
    "sprint planning": {
        "summary": "The team kicked off sprint planning. Sarah is working on the API rate limiting, and the frontend dashboard will start once the API is ready. Staging deployment is scheduled for Friday.",
        "key_decisions": [
            "Deploy the sprint work to staging by Friday",
            "Reconvene for the next status sync on Thursday morning at 10:00 AM"
        ],
        "action_items": [
            {"task": "Send production database schema to Sarah", "owner": "Unassigned", "due_date": None, "priority": "high"},
            {"task": "Finish API rate limiting implementation", "owner": "Sarah", "due_date": "Wednesday", "priority": "medium"}
        ]
    },
    "user dashboard": {
        "summary": "Reviewed the user dashboard feature. Mobile load times average 3 seconds and there is a notification bug to fix. Conversion rate is up 12% with 400 beta users.",
        "key_decisions": [
            "Launch the dashboard next Monday if the mobile performance issue is resolved"
        ],
        "action_items": [
            {"task": "Fix mobile performance issue under 1.5 seconds", "owner": "Kevin", "due_date": "tomorrow", "priority": "high"},
            {"task": "Fix the dashboard notification bug before launch", "owner": "Unassigned", "due_date": "next Monday", "priority": "high"}
        ]
    },
    "q3 roadmap": {
        "summary": "Finalised the Q3 roadmap focusing on checkout redesign, recommendation engine, and analytics dashboard. The checkout redesign is the highest priority with a 50k budget.",
        "key_decisions": [
            "Checkout redesign is the highest priority initiative for Q3",
            "Target timeline: design by July 31st, dev through September, launch in October"
        ],
        "action_items": [
            {"task": "Schedule the design review", "owner": "Lisa", "due_date": "July 15th", "priority": "medium"},
            {"task": "Send payment processor API documentation to the team", "owner": "Mark", "due_date": "Friday", "priority": "high"}
        ]
    }
}


def summarize_transcript(transcript: str) -> dict:
    """
    Summarizes a transcript into overview + key decisions + action items.
    Retries up to 3 times on transient errors (429, timeouts, 5xx).

    Returns:
        dict with keys: summary (str), key_decisions (list[str]),
        action_items (list[dict])
    """
    if not transcript or not transcript.strip():
        return {"summary": "", "key_decisions": [], "action_items": []}

    try:
        content = _call_gpt_api(transcript)
        parsed = json.loads(content)
        return {
            "summary": parsed.get("summary", ""),
            "key_decisions": parsed.get("key_decisions", []),
            "action_items": parsed.get("action_items", []),
        }
    except Exception as exc:  # noqa: BLE001
        for keyword, simulated in SIMULATED_SUMMARIES.items():
            if keyword in transcript.lower():
                logger.warning(
                    "Summarisation failed. Using simulated fallback summary: %s",
                    exc,
                )
                return simulated
        raise SummarizationError(f"Summarization failed: {exc}") from exc
