"""WebSocket bridge: Python is the SERVER, Electron is the CLIENT.

Receives task.start / task.cancel / human.help.resolved / ping from Electron,
and streams agent.status / agent.action / agent.result / human.help.request back.
Message shapes follow shared/protocol.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from websockets.exceptions import ConnectionClosed
from websockets.asyncio.server import ServerConnection, serve

from . import vault
from .browser import BrowserSession
from .config import config
from .conversation import ChatStore
from .llm import LLMClient
from .loop import Agent

log = logging.getLogger("navigator.bridge")


class Bridge:
    def __init__(self, session: BrowserSession, llm: LLMClient) -> None:
        self.session = session
        self.llm = llm
        self._clients: set[ServerConnection] = set()
        self._agent: Optional[Agent] = None
        self._task: Optional[asyncio.Task] = None
        self._store = ChatStore()

    async def _emit(self, message: dict) -> None:
        if not self._clients:
            return
        data = json.dumps(message)
        await asyncio.gather(*(c.send(data) for c in self._clients), return_exceptions=True)

    async def _handle(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
        log.info("Electron client connected")
        await self._emit({"type": "ready", "payload": {"status": "connected"}})
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._dispatch(msg)
        except ConnectionClosed:
            pass  # normal client reload/close — not an error
        finally:
            self._clients.discard(ws)
            log.info("Electron client disconnected")

    async def _dispatch(self, msg: dict) -> None:
        mtype = msg.get("type")
        payload = msg.get("payload", {})

        if mtype == "ping":
            await self._emit({"type": "pong", "payload": {}})
        elif mtype == "hello":
            await self._emit({"type": "ready", "payload": {"status": "ok"}})
        elif mtype == "task.start":
            await self._start_task(payload.get("taskId", "task"), payload.get("chatId", "default"), payload.get("instruction", ""))
        elif mtype == "task.cancel":
            if self._task and not self._task.done():
                self._task.cancel()
        elif mtype == "chat.list":
            await self._emit({"type": "chat.list", "payload": {"chats": self._store.list()}})
        elif mtype == "chat.load":
            chat_id = payload.get("chatId", "")
            await self._emit({"type": "chat.log", "payload": {"chatId": chat_id, "log": self._store.load_log(chat_id)}})
        elif mtype == "chat.delete":
            self._store.delete(payload.get("chatId", ""))
            await self._emit({"type": "chat.list", "payload": {"chats": self._store.list()}})
        elif mtype == "tab.active":
            self.session.set_active_by_url(payload.get("url", ""))
        elif mtype == "human.help.resolved":
            if self._agent:
                self._agent.resume_from_human()
        elif mtype == "user.answer":
            if self._agent:
                self._agent.provide_answer(payload.get("answer", ""))
        elif mtype == "vault.add":
            await self._vault_add(payload)
        elif mtype == "vault.remove":
            vault.delete(payload.get("domain", ""))
            await self._emit({"type": "vault.result", "payload": {"ok": True, "action": "remove", "domain": vault.domain_of(payload.get("domain", ""))}})
        elif mtype == "vault.check":
            cred = vault.get(payload.get("domain", ""))
            await self._emit({"type": "vault.result", "payload": {"ok": True, "action": "check", "domain": vault.domain_of(payload.get("domain", "")), "exists": bool(cred), "username": cred.username if cred else None}})
        else:
            log.debug("unhandled message: %s", mtype)

    async def _vault_add(self, payload: dict) -> None:
        try:
            vault.store(
                vault.Credential(
                    domain=payload["domain"],
                    username=payload["username"],
                    password=payload["password"],
                    totp_seed=(payload.get("totp") or None),
                )
            )
            await self._emit({"type": "vault.result", "payload": {"ok": True, "action": "add", "domain": vault.domain_of(payload["domain"])}})
        except Exception as e:  # noqa: BLE001
            await self._emit({"type": "vault.result", "payload": {"ok": False, "action": "add", "error": str(e)}})

    async def _start_task(self, task_id: str, chat_id: str, instruction: str) -> None:
        # Auto-cancel any in-flight task so "Run" always starts the new one.
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Continue the persistent conversation for this chat (keeps full context).
        conv = self._store.get_or_create(chat_id)
        self._agent = Agent(self.session, self.llm, self._emit, conv, persist=lambda: self._store.save(conv))
        self._task = asyncio.create_task(self._after_task(self._agent.run_task(task_id, instruction), conv))

    async def _after_task(self, coro, conv) -> None:
        try:
            await coro
        finally:
            self._store.save(conv)
            await self._emit({"type": "chat.list", "payload": {"chats": self._store.list()}})

    async def serve_forever(self) -> None:
        async with serve(self._handle, config.ws_host, config.ws_port):
            log.info("WebSocket bridge listening on ws://%s:%d", config.ws_host, config.ws_port)
            await asyncio.Future()  # run until cancelled
