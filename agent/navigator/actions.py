"""Action executor: maps the LLM's tool calls to Playwright/CDP operations.

Elements are resolved by the stable `data-agent-id` attribute that the
perception layer stamped onto the page, so an action like click(7) targets
exactly the element the model saw at index 7.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from patchright.async_api import Page

from . import vault
from .browser import BrowserSession

log = logging.getLogger("navigator.actions")


@dataclass
class ActionResult:
    ok: bool
    summary: str
    # Set when the action finishes the task.
    done: bool = False
    result: Optional[str] = None
    # Set when the agent needs a human (e.g. unsolvable CAPTCHA).
    needs_human: bool = False
    human_reason: str = ""
    # Set when the agent wants to ask the user a question and wait for an answer.
    asks_user: bool = False
    question: str = ""


def _selector(index: int) -> str:
    return f'[data-agent-id="{index}"]'


class ActionExecutor:
    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    @property
    def page(self) -> Page:
        return self.session.page

    async def _resolve(self, index: int):
        """Find element [index] in whichever frame it lives (top doc or iframe).

        Indices are globally unique across frames, so the first frame that
        contains the data-agent-id owns it.
        """
        sel = _selector(index)
        for frame in self.page.frames:
            loc = frame.locator(sel).first
            try:
                if await loc.count() > 0:
                    return loc
            except Exception:  # noqa: BLE001 - detached frame
                continue
        return self.page.locator(sel).first  # fallback (will raise a clear error)

    async def execute(self, tool: str, args: dict[str, Any]) -> ActionResult:
        handler: Optional[Callable] = getattr(self, f"_do_{tool}", None)
        if handler is None:
            return ActionResult(False, f"Unknown tool '{tool}'")
        try:
            return await handler(args)
        except Exception as e:  # noqa: BLE001 - surface failure to the model for self-correction
            log.info("action %s failed: %s", tool, e)
            return ActionResult(False, f"{tool} failed: {e}")

    # --- Navigation -------------------------------------------------------
    async def _do_navigate(self, a: dict) -> ActionResult:
        url = a["url"]
        if "://" not in url:
            url = "https://" + url
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return ActionResult(True, f"navigated to {url}")

    async def _do_go_back(self, a: dict) -> ActionResult:
        await self.page.go_back(wait_until="domcontentloaded")
        return ActionResult(True, "went back")

    async def _do_go_forward(self, a: dict) -> ActionResult:
        await self.page.go_forward(wait_until="domcontentloaded")
        return ActionResult(True, "went forward")

    # --- Interaction ------------------------------------------------------
    async def _do_click(self, a: dict) -> ActionResult:
        idx = int(a["index"])
        loc = await self._resolve(idx)
        try:
            await loc.scroll_into_view_if_needed(timeout=4000)
        except Exception:  # noqa: BLE001 - not fatal
            pass
        try:
            await loc.click(timeout=4500)
        except Exception:  # noqa: BLE001 - overlay/animation intercepted the click
            # Fall back to dispatching the click straight to the element, which
            # bypasses suggestion dropdowns / overlays that sit on top of it.
            await loc.dispatch_event("click")
        return ActionResult(True, f"clicked [{idx}]")

    async def _do_type(self, a: dict) -> ActionResult:
        idx = int(a["index"])
        text = str(a.get("text", ""))
        submit = bool(a.get("submit", False))
        loc = await self._resolve(idx)
        try:
            await loc.scroll_into_view_if_needed(timeout=4000)
        except Exception:  # noqa: BLE001 - not fatal
            pass
        # fill() focuses and sets the value WITHOUT a pointer click, so it works
        # even when an overlay (e.g. Gmail's recipient-autocomplete dropdown)
        # covers the field — the previous click-to-focus was what kept timing out.
        try:
            await loc.fill(text, timeout=4000)
        except Exception:  # noqa: BLE001 - retry via explicit focus
            await loc.focus()
            await loc.fill(text, timeout=4000)
        if submit:
            await loc.press("Enter")
        shown = text if len(text) < 40 else text[:37] + "..."
        return ActionResult(True, f'typed "{shown}" into [{idx}]' + (" + Enter" if submit else ""))

    async def _do_select_option(self, a: dict) -> ActionResult:
        idx = int(a["index"])
        value = str(a["value"])
        loc = await self._resolve(idx)
        await loc.select_option(value)
        return ActionResult(True, f"selected '{value}' in [{idx}]")

    async def _do_press_key(self, a: dict) -> ActionResult:
        key = str(a["key"])
        await self.page.keyboard.press(key)
        return ActionResult(True, f"pressed {key}")

    async def _do_scroll(self, a: dict) -> ActionResult:
        direction = a.get("direction", "down")
        amount = int(a.get("amount", 600))
        dy = amount if direction == "down" else -amount
        await self.page.mouse.wheel(0, dy)
        return ActionResult(True, f"scrolled {direction} {amount}px")

    async def _do_wait(self, a: dict) -> ActionResult:
        """Wait dynamically for the page to finish loading. If `ms` is given, wait
        at most that long; otherwise wait for the network to go idle (capped)."""
        page = self.page
        ms = int(a.get("ms", 0))
        try:
            if ms:
                # Honor an explicit wait, but cap it.
                await page.wait_for_timeout(min(ms, 8000))
                return ActionResult(True, f"waited {min(ms, 8000)}ms")
            # No duration given: wait for the load event (fires reliably), NOT
            # networkidle (busy sites never go idle and would stall the full cap).
            await page.wait_for_load_state("load", timeout=5000)
            return ActionResult(True, "waited for the page to load")
        except Exception:  # noqa: BLE001 - timed out, or page changed/closed
            return ActionResult(True, "page settled; continuing")

    # --- Tabs -------------------------------------------------------------
    async def _do_open_tab(self, a: dict) -> ActionResult:
        await self.session.new_tab()
        return ActionResult(True, "opened a new tab")

    async def _do_switch_tab(self, a: dict) -> ActionResult:
        i = int(a["index"])
        pages = self.session.pages
        if 0 <= i < len(pages):
            self.session.set_active(pages[i])
            await pages[i].bring_to_front()
            return ActionResult(True, f"switched to tab {i}")
        return ActionResult(False, f"no tab {i} (have {len(pages)})")

    # --- Content ----------------------------------------------------------
    async def _do_extract(self, a: dict) -> ActionResult:
        """Return visible text (top document + iframes) so the model can answer."""
        parts: list[str] = []
        for frame in self.page.frames[:25]:
            try:
                t = await asyncio.wait_for(
                    frame.evaluate("() => document.body ? document.body.innerText : ''"), timeout=2.5
                )
            except Exception:  # noqa: BLE001 - slow/detached frame skipped
                continue
            if t and t.strip():
                parts.append(t.strip())
        text = "\n\n".join(parts)
        if len(text) > 6000:
            text = text[:6000] + "\n...[truncated]"
        return ActionResult(True, f"extracted page text ({len(text)} chars)", result=text)

    # --- Credentials (autonomous) ----------------------------------------
    async def _do_get_credentials(self, a: dict) -> ActionResult:
        """Look up creds for a domain WITHOUT exposing them to the model.

        The username goes into the username field index and the password into
        the password field index that the model identified. Values never leave
        this process boundary in plain text toward the LLM.
        """
        domain = a.get("domain") or self.page.url
        cred = vault.get(domain)
        if cred is None:
            return ActionResult(False, f"no stored credentials for {vault.domain_of(domain)}")

        user_idx = a.get("username_index")
        pass_idx = a.get("password_index")
        filled = []
        if user_idx is not None:
            loc = await self._resolve(int(user_idx))
            await loc.fill(cred.username)
            filled.append("username")
        if pass_idx is not None:
            loc = await self._resolve(int(pass_idx))
            await loc.fill(cred.password)
            filled.append("password")

        if a.get("submit"):
            await self.page.keyboard.press("Enter")
            filled.append("submit")

        if not filled:
            return ActionResult(False, "no username_index/password_index provided")
        return ActionResult(True, f"filled credentials ({', '.join(filled)}) for {cred.domain}")

    async def _do_fill_totp(self, a: dict) -> ActionResult:
        domain = a.get("domain") or self.page.url
        cred = vault.get(domain)
        if not cred or not cred.totp_seed:
            return ActionResult(False, "no TOTP seed stored for this domain")
        code = vault.current_totp(cred.totp_seed)
        if not code:
            return ActionResult(False, "could not generate TOTP code")
        idx = int(a["index"])
        loc = await self._resolve(idx)
        await loc.fill(code)
        if a.get("submit"):
            await loc.press("Enter")
        return ActionResult(True, f"entered 2FA code into [{idx}]")

    # --- Escalation / completion -----------------------------------------
    async def _do_request_human_help(self, a: dict) -> ActionResult:
        reason = a.get("reason", "manual intervention required")
        return ActionResult(True, f"requesting human help: {reason}", needs_human=True, human_reason=reason)

    async def _do_ask_user(self, a: dict) -> ActionResult:
        question = a.get("question", "").strip() or "Could you provide more detail?"
        return ActionResult(True, f"asked the user: {question}", asks_user=True, question=question)

    async def _do_done(self, a: dict) -> ActionResult:
        result = str(a.get("result", ""))
        return ActionResult(True, "task complete", done=True, result=result)
