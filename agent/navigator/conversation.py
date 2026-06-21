"""Persistent conversation + chat store.

A Conversation accumulates the LLM message history across multiple task
submissions, so follow-up instructions build on everything that came before
(one ongoing goal). Conversations are persisted to disk so chats survive
restarts; the UI can list, load, switch, and delete them.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .prompts import SYSTEM_PROMPT

CHATS_DIR = Path.home() / ".navgo" / "chats"


def _strip_images(messages: list[dict]) -> list[dict]:
    """Drop base64 screenshots before persisting (keep text only)."""
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            text = " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
            m = {**m, "content": text or "[image omitted]"}
        out.append(m)
    return out


class Conversation:
    """Same message-management interface the agent loop expects, but persistent
    across tasks (not reset each run) and serializable."""

    def __init__(self, chat_id: str, title: str = "", created_at: float | None = None) -> None:
        self.chat_id = chat_id
        self.title = title
        self.created_at = created_at if created_at is not None else time.time()
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.step = 0
        self.recent_actions: list[str] = []
        self.log: list[dict] = []  # compact UI entries for replay

    # --- task lifecycle ---
    def start_task(self, instruction: str) -> None:
        # First task names the chat.
        if not self.title:
            self.title = instruction.strip()[:60] or "New chat"
        # Continuation: tell the model this is a new instruction in the same session.
        prefix = "Task" if len(self.messages) <= 1 else "Next instruction (same session, keep prior context)"
        self.messages.append({"role": "user", "content": f"{prefix}: {instruction}"})
        self.recent_actions.clear()
        self.add_log("task", instruction, "think")

    # --- message helpers (mirror the old TaskMemory) ---
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
                "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments_json}}],
            }
        )

    def add_tool_result(self, call_id: str, summary: str) -> None:
        self.messages.append({"role": "tool", "tool_call_id": call_id, "content": summary})

    def add_assistant_text(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})

    def add_note(self, text: str) -> None:
        self.messages.append({"role": "user", "content": f"[system note] {text}"})

    def note_action(self, signature: str) -> int:
        self.recent_actions.append(signature)
        self.recent_actions = self.recent_actions[-6:]
        return self.recent_actions.count(signature)

    def trim(self, keep_last_observations: int = 3) -> None:
        obs_indices = [i for i, m in enumerate(self.messages) if m["role"] == "user" and i >= 2]
        for i in obs_indices[:-keep_last_observations]:
            self.messages[i] = {"role": "user", "content": "[older page observation omitted]"}

    # --- UI log ---
    def add_log(self, tool: str, text: str, cls: str) -> None:
        self.log.append({"tool": tool, "text": text or "", "cls": cls})
        self.log = self.log[-300:]

    # --- persistence ---
    def to_dict(self) -> dict:
        return {
            "chat_id": self.chat_id,
            "title": self.title or "New chat",
            "created_at": self.created_at,
            "messages": _strip_images(self.messages),
            "log": self.log,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Conversation":
        c = cls(d["chat_id"], d.get("title", ""), d.get("created_at"))
        c.messages = d.get("messages") or c.messages
        c.log = d.get("log") or []
        return c


class ChatStore:
    """Loads/saves conversations as JSON files under ~/.navgo/chats."""

    def __init__(self, directory: Path = CHATS_DIR) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Conversation] = {}

    def _path(self, chat_id: str) -> Path:
        safe = "".join(c for c in chat_id if c.isalnum() or c in "-_")
        return self.dir / f"{safe}.json"

    def get_or_create(self, chat_id: str) -> Conversation:
        if chat_id in self._cache:
            return self._cache[chat_id]
        p = self._path(chat_id)
        if p.exists():
            try:
                conv = Conversation.from_dict(json.loads(p.read_text()))
            except Exception:  # noqa: BLE001 - corrupt file -> start fresh
                conv = Conversation(chat_id)
        else:
            conv = Conversation(chat_id)
        self._cache[chat_id] = conv
        return conv

    def save(self, conv: Conversation) -> None:
        try:
            self._path(conv.chat_id).write_text(json.dumps(conv.to_dict()))
        except Exception:  # noqa: BLE001
            pass

    def list(self) -> list[dict]:
        items = []
        for p in self.dir.glob("*.json"):
            try:
                d = json.loads(p.read_text())
                items.append(
                    {
                        "chatId": d["chat_id"],
                        "title": d.get("title") or "New chat",
                        "createdAt": d.get("created_at", 0),
                        "updatedAt": p.stat().st_mtime,
                    }
                )
            except Exception:  # noqa: BLE001
                continue
        items.sort(key=lambda x: x["updatedAt"], reverse=True)
        return items

    def load_log(self, chat_id: str) -> list[dict]:
        return self.get_or_create(chat_id).log

    def delete(self, chat_id: str) -> None:
        self._cache.pop(chat_id, None)
        p = self._path(chat_id)
        if p.exists():
            try:
                p.unlink()
            except Exception:  # noqa: BLE001
                pass
