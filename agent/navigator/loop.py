"""The ReAct agent loop: observe -> think (GPT-4o mini) -> act -> repeat.

Emits structured events (status/action/result/human-help) through an async
callback so the WebSocket bridge can stream them to the Electron UI.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

from . import captcha
from .actions import ActionExecutor
from .browser import BrowserSession
from .config import config
from .conversation import Conversation
from .llm import LLMClient
from .perception import observe

log = logging.getLogger("navigator.loop")

# event: dict -> awaitable. Shapes follow shared/protocol.json.
EventSink = Callable[[dict], Awaitable[None]]

# Irreversible "commit" actions (sending mail, paying, posting, deleting). We let
# the agent do ONE of these per task, then refuse repeats — this is what stops a
# runaway loop like "it kept sending the email over and over".
_SENSITIVE_VERBS = (
    "send", "submit", "pay", "buy", "purchase", "place order", "checkout",
    "publish", "post", "delete", "remove", "confirm", "transfer", "book now",
)


def _is_sensitive_action(tool: str, args: dict, label: str) -> bool:
    lbl = (label or "").lower().strip()
    if tool == "click" and any(v in lbl for v in _SENSITIVE_VERBS):
        return True
    if tool == "press_key" and str(args.get("key", "")).lower() in ("control+enter", "meta+enter"):
        return True  # e.g. Gmail's send shortcut
    if tool == "type" and args.get("submit") and any(v in lbl for v in _SENSITIVE_VERBS):
        return True
    return False


class Agent:
    def __init__(self, session: BrowserSession, llm: LLMClient, emit: EventSink, conv: Conversation, persist=None) -> None:
        self.session = session
        self.llm = llm
        self.actions = ActionExecutor(session)
        self.emit = emit
        self.conv = conv
        self._persist = persist  # optional callback to save the conversation
        # Set by the bridge when the human finishes a takeover.
        self.human_resume = asyncio.Event()
        # Set by the bridge when the user answers an ask_user question.
        self.answer_event = asyncio.Event()
        self._answer = ""
        # After a human takeover we suppress one re-escalation cycle so control
        # actually returns to the agent instead of immediately re-blocking.
        self._skip_block_once = False
        self._fail_streak = 0
        self._committed = 0  # count of irreversible send/submit/pay actions done
        self._need_vision = False  # only screenshot when a page is visual/sparse
        self._captcha_suppressed = False  # stop re-asking once handled this task

    async def run_task(self, task_id: str, instruction: str) -> None:
        # Continue the SAME persistent conversation (keeps all prior context).
        mem = self.conv
        mem.start_task(instruction)
        self._save()
        await self.emit({"type": "agent.status", "payload": {"taskId": task_id, "state": "thinking", "message": "starting"}})

        try:
            for step in range(1, config.max_steps + 1):
                mem.step = step

                # Recover if the active tab was closed (e.g. a link opened a new
                # tab and the old page died).
                try:
                    await self.session.ensure_alive()
                except Exception:  # noqa: BLE001
                    await self._result(task_id, False, "the browser has no open page")
                    return

                # --- OBSERVE ---
                # Vision is EXPENSIVE (a screenshot roughly doubles the model's
                # response time), so attach one only when it actually helps: a
                # genuinely visual/sparse page, right after a failure, or a CAPTCHA.
                # Normal DOM-rich pages are read from the element list alone (fast).
                cap = await captcha.detect(self.session.page)
                want_shot = config.send_screenshot_every_step or cap.present or self._need_vision
                obs = await observe(self.session.page, with_screenshot=want_shot)
                # Only a near-empty element list (canvas/visual page) warrants vision
                # on the next turn.
                self._need_vision = len(obs.elements) < 3

                # --- CAPTCHA: auto-attempt before asking the model to act ---
                # _skip_block_once is set right after a human takeover so the
                # agent gets one clean turn to act instead of re-escalating on a
                # block that the human just cleared.
                if cap.present and not self._skip_block_once and not self._captcha_suppressed:
                    await self.emit({"type": "agent.status", "payload": {"taskId": task_id, "state": "acting", "message": f"captcha detected ({','.join(cap.signals)}), auto-attempting"}})
                    solved = await captcha.try_auto_solve(self.session.page, self.llm)
                    if not solved:
                        # Cloudflare/Turnstile must be solved with the CDP session
                        # detached, or it fails even on a human click.
                        is_cf = any(s in ("turnstile", "cloudflare") for s in cap.signals)
                        reason = (
                            "Cloudflare security check — please click the checkbox / complete it in the "
                            "page on the left, then Continue." if is_cf else "captcha"
                        )
                        if not await self._escalate(task_id, reason, detach_cdp=is_cf):
                            return
                        # Ask at most ONCE per task — if it still "detects" after a
                        # resume it's a stale/invisible widget, not a real block.
                        self._captcha_suppressed = True
                        self._after_resume(mem, "the security check / CAPTCHA")
                        continue  # re-observe after human solved it
                self._skip_block_once = False

                mem.add_observation(obs.render_text(), obs.screenshot_b64)
                mem.trim()

                # Periodic goal-state reflection (keeps the agent grounded on the
                # goal and breaks it out of unproductive loops).
                if step % 6 == 0:
                    mem.add_note(
                        "Reflection checkpoint: in one line restate the goal, what you've achieved, "
                        "and the single next concrete step. If you've been repeating actions without "
                        "progress, switch strategy now or ask_user."
                    )

                # --- THINK ---
                await self.emit({"type": "agent.status", "payload": {"taskId": task_id, "state": "thinking", "message": f"step {step}"}})
                call, text = await self.llm.next_action(mem.messages)

                if call is None:
                    # Model returned only prose — treat as a clarification/finish.
                    mem.add_assistant_text(text or "")
                    await self._result(task_id, True, text or "(no action)")
                    return

                args_json = json.dumps(call.arguments)
                mem.add_assistant_tool_call(call.id, call.name, args_json, text)

                # Surface the model's reasoning so the user can see WHY it acts.
                if text and text.strip():
                    thought = text.strip()
                    mem.add_log("thinking", thought, "think")
                    await self.emit({"type": "agent.action", "payload": {"taskId": task_id, "step": step, "tool": "thinking", "args": {}, "summary": thought}})

                # Loop guard. If the agent is clearly stuck (same action many
                # times), proactively ask the human for help instead of spinning.
                signature = f"{call.name}:{args_json}"
                repeats = mem.note_action(signature)
                if repeats >= 5:
                    mem.add_tool_result(call.id, "Repeated the same action 5x — pausing to ask the human for help.")
                    if not await self._escalate(task_id, "I'm stuck on this page and need your help — please take over, then resume."):
                        return
                    self._after_resume(mem, "the step you were stuck on")
                    continue
                if repeats >= 3:
                    mem.add_tool_result(call.id, "You have repeated this exact action 3x with no progress. Try a different element or approach.")
                    continue

                # --- IRREVERSIBLE-ACTION GUARDRAIL ---
                # Resolve the label of the element being acted on, then decide if
                # this is a commit (send/pay/post). Allow exactly one per task.
                labels = {e["index"]: e.get("label", "") for e in obs.elements}
                idx_arg = call.arguments.get("index")
                lbl = labels.get(int(idx_arg), "") if isinstance(idx_arg, (int, float, str)) and str(idx_arg).lstrip("-").isdigit() else ""
                is_commit = _is_sensitive_action(call.name, call.arguments, lbl)
                if is_commit and self._committed >= 1:
                    mem.add_tool_result(
                        call.id,
                        "BLOCKED: you have already performed a send/submit/pay action for this "
                        "task. Do NOT repeat irreversible actions. If the goal is achieved, call "
                        "done() now with a summary. If a DIFFERENT remaining step is needed, do that "
                        "instead — but never resend.",
                    )
                    await self.emit({"type": "agent.action", "payload": {"taskId": task_id, "step": step, "tool": call.name, "args": call.arguments, "summary": "blocked repeated send/submit"}})
                    continue

                # --- ACT ---
                await self.emit({
                    "type": "agent.action",
                    "payload": {"taskId": task_id, "step": step, "tool": call.name, "args": call.arguments, "summary": ""},
                })
                result = await self.actions.execute(call.name, call.arguments)

                if is_commit and result.ok:
                    self._committed += 1
                    mem.add_note(
                        "You just performed the send/submit/pay action — this is normally the final "
                        "step. Verify it succeeded on the page, then call done() with a short summary. "
                        "Do NOT perform it again."
                    )

                # Feed extracted content back to the model when present.
                tool_msg = result.summary
                if result.result is not None:
                    tool_msg = f"{result.summary}\n---\n{result.result}"
                mem.add_tool_result(call.id, tool_msg)

                await self.emit({
                    "type": "agent.action",
                    "payload": {"taskId": task_id, "step": step, "tool": call.name, "args": call.arguments, "summary": result.summary},
                })
                mem.add_log(call.name, result.summary, "ok" if result.ok else "err")

                # Track failures so a string of broken actions also asks for help.
                self._fail_streak = 0 if result.ok else self._fail_streak + 1
                if not result.ok:
                    self._need_vision = True  # show the agent the page on retry

                if result.asks_user:
                    mem.add_log("question", result.question, "human")
                    self._save()
                    answer = await self._ask_user(task_id, result.question)
                    if answer is None:
                        return  # cancelled
                    mem.add_log("you", answer, "think")
                    mem.recent_actions.clear()
                    mem.add_note(f'You asked the user: "{result.question}". They answered: "{answer}". Use this and continue.')
                    self._save()
                    continue

                if result.needs_human:
                    if not await self._escalate(task_id, result.human_reason):
                        return
                    self._after_resume(mem, "the action you requested help with")
                    continue

                if self._fail_streak >= 5:
                    if not await self._escalate(task_id, "Several actions failed in a row — I need your help to continue."):
                        return
                    self._after_resume(mem, "the failing step")
                    continue

                if result.done:
                    await self._result(task_id, True, result.result or "")
                    return

                # Dynamically wait for the page to settle after the action (network
                # to go idle), instead of a fixed sleep. Closed/changed pages are
                # tolerated — the next iteration re-acquires a live page.
                await self._settle()

            await self._result(task_id, False, f"reached step limit ({config.max_steps})")

        except Exception as e:  # noqa: BLE001 - report, never crash the service
            log.exception("task failed")
            await self._result(task_id, False, f"error: {e}")

    async def _escalate(self, task_id: str, reason: str, detach_cdp: bool = False) -> bool:
        """Ask the Electron UI for a human takeover; wait for resume.

        If detach_cdp is True (Cloudflare/Turnstile), the agent disconnects its CDP
        session while the human solves — removing the automation traces that make
        the challenge fail even on a human click — then reattaches on resume.

        Returns True if the human resolved it, False if the task was cancelled.
        """
        self.human_resume.clear()
        shot = None
        try:
            raw = await self.session.page.screenshot(type="png")
            import base64

            shot = base64.b64encode(raw).decode("ascii")
        except Exception:  # noqa: BLE001
            pass
        self.conv.add_log("help needed", reason, "human")
        self._save()

        # Detach BEFORE the human acts so the page is no longer automation-flagged,
        # then reload the page so the Cloudflare/Turnstile widget re-initializes in
        # a clean (non-CDP) context — otherwise it was already flagged at load time
        # and fails even on a genuine human click.
        if detach_cdp:
            await self.session.detach()
            await self.emit({"type": "tab.reload", "payload": {}})

        await self.emit({"type": "agent.status", "payload": {"taskId": task_id, "state": "waiting_human", "message": reason}})
        await self.emit({"type": "human.help.request", "payload": {"taskId": task_id, "reason": reason, "screenshot": shot}})
        try:
            await asyncio.wait_for(self.human_resume.wait(), timeout=600)
        except asyncio.TimeoutError:
            if detach_cdp:
                try:
                    await self.session.reattach()
                except Exception:  # noqa: BLE001
                    pass
            await self._result(task_id, False, "timed out waiting for human help")
            return False

        if detach_cdp:
            await self.session.reattach()  # reconnect now that the challenge is cleared
        await self.emit({"type": "agent.status", "payload": {"taskId": task_id, "state": "acting", "message": "resumed — continuing the task"}})
        return True

    def _after_resume(self, mem: Conversation, what: str) -> None:
        """Reset stuck/loop state and tell the model the human handled it, so it
        continues instead of immediately re-asking for help."""
        self._skip_block_once = True
        self._fail_streak = 0
        mem.recent_actions.clear()
        mem.add_note(
            f"The human has finished handling {what}; it should now be resolved. "
            "Re-read the current page and continue the task. Do NOT ask for help "
            "again unless a clearly new blocker appears."
        )

    def resume_from_human(self) -> None:
        self.human_resume.set()

    async def _ask_user(self, task_id: str, question: str) -> str | None:
        """Ask the user a question mid-task and wait for a typed answer.

        Returns the answer string, or None if the task was cancelled/timed out.
        """
        self._answer = ""
        self.answer_event.clear()
        await self.emit({"type": "agent.status", "payload": {"taskId": task_id, "state": "waiting_user", "message": "waiting for your answer"}})
        await self.emit({"type": "agent.question", "payload": {"taskId": task_id, "question": question}})
        try:
            await asyncio.wait_for(self.answer_event.wait(), timeout=600)
        except asyncio.TimeoutError:
            await self._result(task_id, False, "timed out waiting for your answer")
            return None
        await self.emit({"type": "agent.status", "payload": {"taskId": task_id, "state": "acting", "message": "got your answer — continuing"}})
        return self._answer

    def provide_answer(self, answer: str) -> None:
        self._answer = answer or ""
        self.answer_event.set()

    def _save(self) -> None:
        if self._persist:
            try:
                self._persist()
            except Exception:  # noqa: BLE001
                pass

    async def _result(self, task_id: str, ok: bool, text: str) -> None:
        self.conv.add_log("result", text, "ok" if ok else "err")
        self._save()
        await self.emit({"type": "agent.result", "payload": {"taskId": task_id, "ok": ok, "result": text}})

    async def _settle(self) -> None:
        """Wait dynamically for the page to finish reacting to the last action:
        let the DOM load and the network go (mostly) idle, capped so we never
        hang. All errors (incl. a page that just closed) are ignored."""
        try:
            page = self.session.page
            if page.is_closed():
                return
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:  # noqa: BLE001
                pass
            # Brief render settle. We do NOT wait for "networkidle": busy apps
            # (Google, Gmail, etc.) never go idle, so that would burn the full
            # timeout on every single step — the main cause of "it gets stuck".
            try:
                await page.wait_for_timeout(300)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001 - page/context closed mid-settle
            pass
