"""GPT-4o mini client (async) with tool-calling and a vision helper.

Uses the official OpenAI Python SDK. The model receives the system prompt, the
running message history, and the tool schema; it replies with a single tool call
that the agent loop executes.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from openai import AsyncOpenAI

from .config import config
from .prompts import tool_schema

log = logging.getLogger("navigator.llm")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


def _ensure_tool_responses(messages: list[dict]) -> list[dict]:
    """Make the message list valid for OpenAI's tool-call rules, both directions:
      * every assistant `tool_calls` id is followed by a matching tool message
        (insert a synthetic one if missing), and
      * every `tool` message immediately follows an assistant `tool_calls`
        (drop orphaned tool messages).
    This keeps requests valid even if a turn left the history slightly off."""
    out: list[dict] = []
    i, n = 0, len(messages)
    while i < n:
        m = messages[i]
        if m.get("role") == "tool":
            i += 1  # orphaned tool (not consumed by an assistant branch) -> drop
            continue
        out.append(m)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            need = [tc["id"] for tc in m["tool_calls"]]
            j = i + 1
            satisfied = set()
            while j < n and messages[j].get("role") == "tool":
                tid = messages[j].get("tool_call_id")
                if tid in need and tid not in satisfied:
                    out.append(messages[j])
                    satisfied.add(tid)
                j += 1
            for tid in need:
                if tid not in satisfied:
                    out.append({"role": "tool", "tool_call_id": tid, "content": "(no result recorded)"})
            i = j
            continue
        i += 1
    return out


class LLMClient:
    def __init__(self) -> None:
        if not config.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set (see .env.example)")
        self._client = AsyncOpenAI(api_key=config.openai_api_key)
        self._tools = tool_schema()
        # Params the current model rejects (discovered once, then skipped forever).
        self._unsupported: set[str] = set()

    async def _create(self, messages: list[dict]):
        """Call chat.completions, retrying without params a given model rejects
        (e.g. reasoning models like o-series / GPT-5 may not accept temperature or
        parallel_tool_calls). This lets NAV_MODEL be set to any model safely."""
        params = dict(
            model=config.model,
            messages=messages,
            tools=self._tools,
            tool_choice="auto",
            parallel_tool_calls=False,
            temperature=config.temperature,
        )
        # Skip params this model already told us it doesn't support — so we don't
        # waste a failed request on every single step.
        for p in self._unsupported:
            params.pop(p, None)
        for _ in range(3):
            try:
                return await self._client.chat.completions.create(**params)
            except Exception as e:  # noqa: BLE001
                msg = str(e).lower()
                dropped = False
                for p in ("temperature", "parallel_tool_calls"):
                    if p in msg and p in params:
                        params.pop(p)
                        self._unsupported.add(p)  # remember for next time
                        dropped = True
                if not dropped:
                    raise
        return await self._client.chat.completions.create(**params)

    async def next_action(self, messages: list[dict]) -> tuple[Optional[ToolCall], Optional[str]]:
        """Ask the model for the next tool call.

        Returns (tool_call, assistant_text). assistant_text is any free-text
        reasoning the model emitted alongside (or instead of) a tool call.
        """
        resp = await self._create(_ensure_tool_responses(messages))
        msg = resp.choices[0].message
        text = msg.content or None

        if not msg.tool_calls:
            return None, text

        call = msg.tool_calls[0]
        try:
            args = json.loads(call.function.arguments or "{}")
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            args = {}
        return ToolCall(id=call.id, name=call.function.name, arguments=args), text

    async def read_captcha_image(self, png_bytes: bytes) -> Optional[str]:
        """Best-effort: ask the vision model to read a simple text CAPTCHA."""
        b64 = base64.b64encode(png_bytes).decode("ascii")
        resp = await self._client.chat.completions.create(
            model=config.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "If this image contains a simple text/number CAPTCHA, reply with ONLY "
                                "the characters. If it is a checkbox/image-grid/interactive challenge "
                                "you cannot solve from a still image, reply exactly: UNSOLVABLE"
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ],
                }
            ],
            temperature=0,
        )
        answer = (resp.choices[0].message.content or "").strip()
        if not answer or "UNSOLVABLE" in answer.upper():
            return None
        return answer
