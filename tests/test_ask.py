import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import mcp_server as srv
from backend.ai import MockLLM
from backend.ai.ask import Answer, answer_is_grounded, ask_via_mcp, ask_with_session
from backend.ai.llm import GroqLLM, LLMError
from backend.api import create_app
from backend.notify import InMemorySentStore
from backend.settings import Settings

OPEN = json.dumps([{"truck_id": "T-04", "status": "recovering", "peak_c": 9.1, "since": "08:25"}])


# --- grounding check ------------------------------------------------------------------------


def test_answer_numbers_must_come_from_tool_results():
    assert answer_is_grounded("T-04 is recovering, peak 9.1 °C since 08:25.", [OPEN])
    assert answer_is_grounded("2 trucks need a look.", [OPEN])  # small counts are fine
    assert not answer_is_grounded("T-04 peaked at 9.8 °C.", [OPEN])
    assert not answer_is_grounded("T-04 has been warm since 07:55.", [OPEN])
    assert not answer_is_grounded("T-04 is 45 minutes out of range.", [OPEN])


def test_times_inside_iso_timestamps_count():
    source = ['{"started_at": "2026-07-20T08:25:00+00:00", "peak_c": 9.1}']
    assert answer_is_grounded("T-04 has been out of range since 08:25, peak 9.1 °C.", source)
    assert not answer_is_grounded("T-04 has been out of range since 08:20.", source)


# --- agent loop with a fake MCP session -----------------------------------------------------


class FakeSession:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        tool = SimpleNamespace(name="get_open_alerts", description="open alerts", inputSchema={"type": "object"})
        return SimpleNamespace(tools=[tool])

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(content=[SimpleNamespace(text=OPEN)])


class ScriptedLLM:
    def __init__(self, *messages):
        self.messages = list(messages)
        self.seen_tools = None

    def chat(self, messages, tools, timeout):
        self.seen_tools = tools
        return self.messages.pop(0)


def tool_call(name, args="{}"):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "function": {"name": name, "arguments": args}}]}


def run(session, llm, q="Which trucks are in trouble?"):
    return asyncio.run(ask_with_session(session, llm, q))


def test_llm_calls_an_mcp_tool_then_answers():
    session = FakeSession()
    llm = ScriptedLLM(tool_call("get_open_alerts"), {"role": "assistant", "content": "T-04 is recovering (peak 9.1 °C)."})
    answer = run(session, llm)
    assert answer == Answer("T-04 is recovering (peak 9.1 °C).", ["get_open_alerts"], ok=True)
    assert session.calls == [("get_open_alerts", {})]
    assert llm.seen_tools[0]["function"]["name"] == "get_open_alerts"


def test_invented_number_is_withheld():
    llm = ScriptedLLM(tool_call("get_open_alerts"), {"role": "assistant", "content": "T-04 peaked at 12.4 °C."})
    answer = run(FakeSession(), llm)
    assert not answer.ok and "matches the live data" in answer.text


def test_unknown_tool_is_not_called():
    session = FakeSession()
    llm = ScriptedLLM(tool_call("delete_everything"), {"role": "assistant", "content": "I can't do that."})
    answer = run(session, llm)
    assert session.calls == [] and answer.tools_used == []


def test_step_limit():
    llm = ScriptedLLM(*[tool_call("get_open_alerts")] * 5)
    assert "too many steps" in run(FakeSession(), llm).text


def test_empty_answer():
    assert not run(FakeSession(), ScriptedLLM({"role": "assistant", "content": ""})).ok


# --- real MCP round trip: backend -> our MCP server process -> tools ---------------------------


def test_real_mcp_server_answers_through_the_protocol():
    answer = asyncio.run(ask_via_mcp("Which trucks are in trouble?", MockLLM(), api_url=None))
    assert answer.ok, answer.text
    assert answer.tools_used == ["get_open_alerts"]
    assert answer.text == "Trucks with open alerts: T-04 (recovering, peak 9.1 °C)."


def test_llm_failure_gives_a_friendly_message():
    class Down:
        def chat(self, *a):
            raise LLMError("HTTP 503")

    answer = asyncio.run(ask_via_mcp("anything?", Down(), api_url=None))
    assert not answer.ok and "unavailable" in answer.text


# --- MCP server reading the live API ---------------------------------------------------------


def test_mcp_server_reads_live_api_when_configured(monkeypatch):
    report = {"evaluated_at": "x", "trucks": [{"truck_id": "T-09", "state": "alert", "short_episodes": []}],
              "alerts": [{"alert_id": "a", "truck_id": "T-09", "status": "open"}]}
    seen = []

    def fake_get(url, timeout):
        seen.append(url)
        return httpx.Response(200, json=report, request=httpx.Request("GET", url))

    monkeypatch.setenv("COLD_CHAIN_API_URL", "http://127.0.0.1:9999/")
    monkeypatch.setattr(srv.httpx, "get", fake_get)
    assert srv.fleet_status()["trucks"] == [{"truck_id": "T-09", "state": "alert"}]
    assert [a["truck_id"] for a in srv.open_alerts()] == ["T-09"]
    assert seen[0] == "http://127.0.0.1:9999/api/report"


# --- Groq tool-calling request ---------------------------------------------------------------


def test_groq_chat_sends_tools():
    def handler(request):
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "get_open_alerts"
        assert body["tool_choice"] == "auto"
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]})

    llm = GroqLLM("k", "m", httpx.Client(transport=httpx.MockTransport(handler)))
    tools = [{"type": "function", "function": {"name": "get_open_alerts", "parameters": {}}}]
    assert llm.chat([{"role": "user", "content": "q"}], tools, 5)["content"] == "hi"


# --- API endpoint -------------------------------------------------------------------------------


@pytest.fixture
def make_client(tmp_path):
    def make(**kw):
        settings = Settings(_env_file=None, frontend_dist=tmp_path / "no-ui", **kw)
        asked = []

        def asker(question, api_url):
            asked.append((question, api_url))
            return Answer("T-04 is recovering.", ["get_open_alerts"])

        client = TestClient(create_app(settings, store=InMemorySentStore(), llm=MockLLM(), asker=asker))
        return client, asked

    return make


def test_ask_endpoint(make_client):
    client, asked = make_client()
    r = client.post("/api/ask", json={"question": "  Which trucks are in trouble?  "})
    assert r.json() == {"answer": "T-04 is recovering.", "ok": True, "tools_used": ["get_open_alerts"]}
    assert asked[0][0] == "Which trucks are in trouble?"
    assert asked[0][1].startswith("http://127.0.0.1:")  # MCP server will read this app's live API


@pytest.mark.parametrize("question", ["", "hi", "x" * 301])
def test_ask_validates_the_question(make_client, question):
    client, _ = make_client()
    assert client.post("/api/ask", json={"question": question}).status_code == 422


def test_ask_is_rate_limited(make_client):
    client, _ = make_client(ask_per_minute=2)
    codes = [client.post("/api/ask", json={"question": "status?"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_ask_needs_no_admin_key(make_client):
    client, _ = make_client(admin_api_key="k")
    assert client.post("/api/ask", json={"question": "status?"}).status_code == 200
