"""AI alert summary: the rules decide, the LLM only words the message.

The answer is checked before use: it must name the right truck, be short, and contain
no time or number that isn't in the facts. Any failure returns None, and the plain
rule-based message is sent instead. One LLM call per notification (cached).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from backend.ai.llm import LLM, LLMError
from backend.engine.models import Notification

log = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 300

SYSTEM_PROMPT = (
    "You write alert messages for the operations team of a refrigerated truck fleet. "
    "Given JSON facts about one temperature alert, write one or two short sentences "
    f"(under {MAX_SUMMARY_CHARS} characters, plain text, no markdown): what is happening to "
    "which truck, and the likely cause, worded as 'likely'. Use only numbers and times that "
    "appear in the facts, copied exactly; do not calculate new ones. Hints: a steady or rising "
    "temperature with rising humidity suggests warm air getting in or a failing reefer unit; a "
    "falling temperature suggests it is recovering; missing data may mean a connectivity issue."
)

_TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def _allowed(facts: dict[str, Any]) -> tuple[set[str], set[float]]:
    times: set[str] = set()
    numbers: set[float] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, bool) or value is None:
            return
        elif isinstance(value, int | float):
            numbers.add(round(float(value), 1))
        elif isinstance(value, str) and _TIME.fullmatch(value):
            times.add(value)

    walk(facts)
    return times, numbers


def check_summary(text: str, facts: dict[str, Any]) -> str | None:
    """Return the cleaned summary, or None with the reason logged."""
    text = " ".join(text.replace("**", "").split())
    truck = str(facts["truck_id"])
    reason = None
    if not text:
        reason = "empty"
    elif len(text) > MAX_SUMMARY_CHARS:
        reason = "too long"
    elif truck not in text:
        reason = "does not name the truck"
    else:
        times, numbers = _allowed(facts)
        rest = text.replace(truck, " ")
        for t in _TIME.findall(rest):
            if t not in times and f"0{t}" not in times:
                reason = f"invented time {t}"
                break
        if reason is None:
            rest = _TIME.sub(" ", rest)
            for raw in _NUMBER.findall(rest):
                if round(float(raw), 1) not in numbers:
                    reason = f"invented number {raw}"
                    break
    if reason:
        log.warning("AI summary rejected for %s: %s", truck, reason)
        return None
    return text


class Summarizer:
    """Callable used by the dispatcher: Notification -> summary text or None."""

    def __init__(self, llm: LLM, facts: dict[str, dict[str, Any]], timeout_seconds: float = 5.0):
        self._llm = llm
        self._facts = facts
        self._timeout = timeout_seconds
        self._cache: dict[str, str | None] = {}

    def __call__(self, n: Notification) -> str | None:
        if n.notification_id in self._cache:
            return self._cache[n.notification_id]
        facts = self._facts.get(n.notification_id)
        summary = None
        if facts is not None:
            try:
                raw = self._llm.complete(SYSTEM_PROMPT, json.dumps(facts), self._timeout)
                summary = check_summary(raw, facts)
            except LLMError as exc:
                log.warning("AI summary unavailable for %s: %s", n.notification_id, exc)
        self._cache[n.notification_id] = summary
        return summary
