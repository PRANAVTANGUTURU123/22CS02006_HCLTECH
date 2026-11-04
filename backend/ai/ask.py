"""'Ask about the fleet': the backend is an MCP client, the LLM chooses which MCP tools to call."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from backend.ai.llm import LLMError
from backend.settings import PROJECT_ROOT

log = logging.getLogger(__name__)

MAX_STEPS = 4
MAX_TOOL_CHARS = 8000
SYSTEM_PROMPT = (
    "You answer questions from the operations manager of a refrigerated truck fleet. "
    "Use the tools to get the live data, and answer only from the tool results. "
    "Reply in at most three short sentences of plain text, no markdown. Copy numbers "
    "from the tool results; write times as HH:MM (e.g. 08:25), not full timestamps. "
    "If the tools don't answer the question, say so."
)

_TRUCK = re.compile(r"\bT-\d+\b")
_TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
# also finds 08:25 inside 2026-07-20T08:25:00
_SOURCE_TIME = re.compile(r"(?:(?<=T)|\b)(\d{2}:\d{2})")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


class ChatLLM(Protocol):
    def chat(self, messages: list[dict], tools: list[dict], timeout: float) -> dict[str, Any]: ...


class ToolSession(Protocol):
    async def list_tools(self) -> Any: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


@dataclass
class Answer:
    text: str
    tools_used: list[str] = field(default_factory=list)
    ok: bool = True


def answer_is_grounded(answer: str, tool_outputs: list[str]) -> bool:
    """Every time and number in the answer must be in the tool results (small counts allowed)."""
    source = " ".join(tool_outputs)
    times = set(_SOURCE_TIME.findall(source))
    numbers = {round(float(n), 1) for n in _NUMBER.findall(_TIME.sub(" ", source))}
    text = _TRUCK.sub(" ", answer)
    for t in _TIME.findall(text):
        if t not in times and f"0{t}" not in times:
            return False
    for n in _NUMBER.findall(_TIME.sub(" ", text)):
        value = round(float(n), 1)
        if value not in numbers and not (value.is_integer() and 0 <= value <= 10):
            return False
    return True


def _tool_text(result: Any) -> str:
    return "".join(getattr(c, "text", "") for c in getattr(result, "content", []))[:MAX_TOOL_CHARS]


async def ask_with_session(
    session: ToolSession, llm: ChatLLM, question: str, llm_timeout: float = 15.0
) -> Answer:
    listed = await session.list_tools()
    tools = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in listed.tools
    ]
    names = {t["function"]["name"] for t in tools}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    used: list[str] = []
    outputs: list[str] = []
    for _ in range(MAX_STEPS):
        try:
            message = await asyncio.to_thread(llm.chat, messages, tools, llm_timeout)
        except LLMError as exc:
            log.warning("ask: LLM failed: %s", exc)
            return Answer("The AI service is unavailable right now.", used, ok=False)
        calls = message.get("tool_calls") or []
        if not calls:
            text = " ".join(str(message.get("content") or "").replace("**", "").split())
            if not text:
                return Answer("The assistant gave no answer. Please try again.", used, ok=False)
            if not answer_is_grounded(text, outputs):
                log.warning("ask: answer rejected, numbers not found in tool results")
                return Answer(
                    "I couldn't give an answer that matches the live data. "
                    "Please check the dashboard.",
                    used,
                    ok=False,
                )
            return Answer(text, used)
        messages.append(
            {"role": "assistant", "content": message.get("content"), "tool_calls": calls}
        )
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if name in names:
                used.append(name)
                output = _tool_text(await session.call_tool(name, args))
            else:
                output = f"error: unknown tool {name}"
            outputs.append(output)
            messages.append(
                {"role": "tool", "tool_call_id": call.get("id", name), "content": output}
            )
    return Answer("That needed too many steps. Please ask a simpler question.", used, ok=False)


async def ask_via_mcp(
    question: str, llm: ChatLLM, api_url: str | None, time_limit: float = 25.0
) -> Answer:
    # The full environment: on Cloud Run the installed packages are found through it.
    env = {**get_default_environment(), **os.environ}
    if api_url:
        env["COLD_CHAIN_API_URL"] = api_url
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "backend.mcp_server"],
        env=env,
        cwd=str(PROJECT_ROOT),
    )

    async def run() -> Answer:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return await ask_with_session(session, llm, question)

    try:
        return await asyncio.wait_for(run(), time_limit)
    except TimeoutError:
        return Answer("The assistant took too long. Please try again.", ok=False)
