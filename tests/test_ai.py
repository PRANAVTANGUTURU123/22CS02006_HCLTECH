import json
import logging

import httpx
import pytest

from backend.ai import (
    LLMError,
    MockLLM,
    Summarizer,
    WithFallback,
    check_summary,
    facts_by_notification,
    llm_from_settings,
)
from backend.ai.llm import GeminiLLM, GroqLLM
from backend.cli import main
from backend.engine import clean_readings, evaluate_fleet, load_raw_file
from backend.notify import InMemorySentStore, dispatch
from backend.settings import Settings
from tests.test_seed_data import SEED

GOOD_T04 = (
    "Truck T-04 has been above 8 °C since 08:25 (peak 9.1 °C) with readings missing "
    "08:30–08:40; likely a reefer problem or a connectivity issue."
)


@pytest.fixture(scope="module")
def report():
    return evaluate_fleet(clean_readings(load_raw_file(SEED)))


@pytest.fixture(scope="module")
def facts(report):
    return facts_by_notification(report)


def t04(facts):
    return next(f for f in facts.values() if f["truck_id"] == "T-04")


# --- facts ----------------------------------------------------------------------------


def test_facts_are_numbers_only_and_as_of_the_notification(facts):
    f = t04(facts)
    assert f["out_of_range_since"] == "08:25"
    assert f["now"] == "08:45"
    assert f["minutes_out_of_range"] == 20
    assert f["peak_temp_c"] == 9.1
    assert f["latest_temp_c"] == 8.5  # not the later 7.2 reading
    assert f["missing_data"] == [{"from": "08:30", "to": "08:40", "minutes": 10}]
    assert f["humidity_start_pct"] == 56 and f["humidity_latest_pct"] == 57
    assert set(f) <= {
        "truck_id", "event", "severity", "direction", "limit_c", "out_of_range_since", "now",
        "minutes_out_of_range", "first_temp_c", "latest_temp_c", "peak_temp_c",
        "temperature_trend", "missing_data", "humidity_start_pct", "humidity_latest_pct",
    }


def test_three_seed_notifications_get_facts(facts):
    assert sorted(f["truck_id"] for f in facts.values()) == ["T-01", "T-04", "T-05"]


# --- the answer check -----------------------------------------------------------------


def test_good_summary_is_accepted(facts):
    assert check_summary(GOOD_T04, t04(facts)) == GOOD_T04


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("Truck T-04 peaked at 9.8 °C; likely a reefer problem.", "invented number 9.8"),
        ("Truck T-04 has been warm since 08:20.", "invented time 08:20"),
        ("Truck T-01 has been above 8 °C for 20 min.", "does not name the truck"),
        ("Truck T-04 " + "is warm " * 50, "too long"),
        ("   ", "empty"),
        ("Truck T-04 has been warm for 25 min.", "invented number 25"),
    ],
)
def test_bad_summaries_are_rejected(facts, caplog, text, why):
    with caplog.at_level(logging.WARNING):
        assert check_summary(text, t04(facts)) is None
    assert why in caplog.text


def test_markdown_and_newlines_are_cleaned(facts):
    assert check_summary("**Truck T-04**\nis above 8 °C.", t04(facts)) == "Truck T-04 is above 8 °C."


def test_truck_id_digits_and_single_digit_times_are_fine(facts):
    assert check_summary("Truck T-04 above 8 °C since 8:25.", t04(facts)) is not None


# --- summarizer -----------------------------------------------------------------------


class FakeLLM:
    name = "fake"

    def __init__(self, reply=GOOD_T04, error=None):
        self.reply, self.error, self.calls = reply, error, 0

    def complete(self, system, prompt, timeout):
        self.calls += 1
        if self.error:
            raise self.error
        return self.reply


def t04_note(report):
    return next(n for n in report.notifications if n.truck_id == "T-04")


def test_summary_is_used_and_cached(report, facts):
    llm = FakeLLM()
    summarize = Summarizer(llm, facts)
    assert summarize(t04_note(report)) == GOOD_T04
    assert summarize(t04_note(report)) == GOOD_T04
    assert llm.calls == 1


def test_prompt_contains_only_the_facts(report, facts):
    seen = {}

    class Spy(FakeLLM):
        def complete(self, system, prompt, timeout):
            seen.update(prompt=prompt, timeout=timeout)
            return GOOD_T04

    Summarizer(Spy(), facts, timeout_seconds=5)(t04_note(report))
    assert json.loads(seen["prompt"]) == t04(facts)
    assert seen["timeout"] == 5


def test_llm_error_or_invented_number_falls_back_to_plain(report, facts):
    assert Summarizer(FakeLLM(error=LLMError("timed out")), facts)(t04_note(report)) is None
    assert Summarizer(FakeLLM(reply="Truck T-04 hit 12 °C."), facts)(t04_note(report)) is None


def test_notification_without_facts_is_not_sent_to_the_llm(report):
    llm = FakeLLM()
    assert Summarizer(llm, {})(t04_note(report)) is None
    assert llm.calls == 0


def test_dispatch_sends_ai_text_with_facts_underneath(report, facts):
    sent = []

    class Sender:
        name = "fake"

        def send(self, payload):
            sent.append(payload)

    dispatch([t04_note(report)], Sender(), InMemorySentStore(), Summarizer(FakeLLM(), facts))
    assert sent[0]["text"].startswith("🟠 Truck T-04 has been above 8 °C since 08:25")
    assert "\nFacts: WARNING: Truck T-04" in sent[0]["text"]
    assert sent[0]["ai_summary"] is True


def test_mock_llm_writes_valid_summaries_for_all_seed_alerts(report, facts):
    summarize = Summarizer(MockLLM(), facts)
    for n in report.notifications:
        summary = summarize(n)
        assert summary is not None and n.truck_id in summary


# --- fallback and providers -------------------------------------------------------------


def test_fallback_is_used_when_primary_fails():
    llm = WithFallback(FakeLLM(error=LLMError("HTTP 503")), FakeLLM(reply="ok"))
    assert llm.complete("s", "p", 5) == "ok"
    assert llm.last_used == "fake"


def test_no_fallback_when_time_is_used_up():
    llm = WithFallback(FakeLLM(error=LLMError("timed out")), FakeLLM(reply="ok"))
    with pytest.raises(LLMError):
        llm.complete("s", "p", 0.5)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_groq_request_and_response_shape():
    def handler(request):
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer k"
        assert body["model"] == "m" and body["reasoning_effort"] == "low"
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    assert GroqLLM("k", "m", _client(handler), "low").complete("s", "p", 5) == "hi"


def test_gemini_request_and_response_shape():
    def handler(request):
        assert request.headers["x-goog-api-key"] == "k"
        assert "key=" not in str(request.url)  # key in a header, never in the URL
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})

    assert GeminiLLM("k", "m", _client(handler)).complete("s", "p", 5) == "hi"


@pytest.mark.parametrize("status", [401, 429, 500])
def test_provider_http_errors_become_llm_errors_without_the_key(status):
    client = _client(lambda r: httpx.Response(status, json={"error": "secret-key-echo"}))
    with pytest.raises(LLMError) as exc:
        GroqLLM("secret-key", "m", client).complete("s", "p", 5)
    assert str(exc.value) == f"HTTP {status}"


def test_timeout_becomes_llm_error():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(LLMError, match="timed out"):
        GroqLLM("k", "m", _client(handler)).complete("s", "p", 5)


def test_missing_key_is_a_config_error():
    with pytest.raises(LLMError):
        GroqLLM("", "m", _client(lambda r: httpx.Response(200)))


def test_settings_build_mock_by_default_and_skip_a_broken_fallback():
    llm = llm_from_settings(Settings(_env_file=None))
    assert llm.name == "mock"
    llm = llm_from_settings(Settings(_env_file=None, llm_provider="mock", llm_fallback_provider="groq"))
    assert llm.fallback is None  # no Groq key: fallback skipped, primary still works


def test_cli_ai_flag_uses_mock_in_tests(capsys):
    assert main(["--ai"]) == 0
    out = capsys.readouterr().out
    assert "AI SUMMARIES (provider: mock)" in out
    assert "(plain message used)" not in out
