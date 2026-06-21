"""Task memory: the running OpenAI message history + lightweight bookkeeping.

Keeps the conversation compact by trimming old observations (their indices are
stale anyway) while preserving the system prompt, the task, and recent context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .prompts import SYSTEM_PROMPT


@dataclass
class TaskMemory:
    instruction: str
    messages: list[dict] = field(default_factory=list)
    step: int = 0
    # Detect loops: track recent (tool, args) signatures.
    recent_actions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Task: {self.instruction}"},
        ]

    def add_observation(self, text: str, screenshot_b64: str | None = None) -> None:
        if screenshot_b64:
            content: Any = [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
            ]
        else:
            content = text
        self.messages.append({"role": "user", "content": content})

    def add_assistant_tool_call(self, call_id: str, name: str, arguments_json: str, text: str | None) -> None:
        self.messages.append(
            {
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments_json},
                    }
                ],
            }
        )

    def add_tool_result(self, call_id: str, summary: str) -> None:
        self.messages.append({"role": "tool", "tool_call_id": call_id, "content": summary})

    def add_assistant_text(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})

    def add_note(self, text: str) -> None:
        """Inject a system-style note as a user message (e.g. after a takeover)."""
        self.messages.append({"role": "user", "content": f"[system note] {text}"})

    def note_action(self, signature: str) -> int:
        """Record an action signature; return how many times it repeated recently."""
        self.recent_actions.append(signature)
        self.recent_actions = self.recent_actions[-6:]
        return self.recent_actions.count(signature)

    def trim(self, keep_last_observations: int = 3) -> None:
        """Replace older image/observation user-messages with a short placeholder
        to bound token growth. System + task + tool-call/result chain are kept."""
        obs_indices = [
            i
            for i, m in enumerate(self.messages)
            if m["role"] == "user" and i >= 2  # skip system + task
        ]
        for i in obs_indices[:-keep_last_observations]:
            self.messages[i] = {"role": "user", "content": "[older page observation omitted]"}
