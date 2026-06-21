"""Browser connection management.

Attaches to the Electron shell's Chromium over the Chrome DevTools Protocol
using Playwright's `connect_over_cdp`. Exposes the active page/tab and helpers
for tab/popup/download handling.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

# patchright is a drop-in, CDP-leak-free fork of Playwright. It avoids the
# Runtime.enable leak (and other protocol tells) that Cloudflare/Turnstile use to
# flag automated browsers — the reason "verify you are human" failed even on a
# human click. Same API as playwright.
from patchright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from .config import config

log = logging.getLogger("navigator.browser")

# Minimal stealth: hide the most common automation tells on every new document.
# (Electron doesn't set navigator.webdriver, but this hardens normal sites.)
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""


class BrowserSession:
    """Wraps a CDP connection to the Electron-hosted Chromium."""

    def __init__(self) -> None:
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def connect(self, retries: int = 30, delay: float = 1.0) -> None:
        """Connect to the running Chromium, retrying until the CDP port is up."""
        self._pw = await async_playwright().start()
        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                self._browser = await self._pw.chromium.connect_over_cdp(config.cdp_url)
                break
            except Exception as e:  # noqa: BLE001 - retry any connect failure
                last_err = e
                log.info("CDP not ready (attempt %d/%d): %s", attempt, retries, e)
                await asyncio.sleep(delay)
        if self._browser is None:
            raise RuntimeError(f"Could not connect to CDP at {config.cdp_url}: {last_err}")

        # Electron exposes its existing window as a default context + page.
        contexts = self._browser.contexts
        self._context = contexts[0] if contexts else await self._browser.new_context()

        # The context contains BOTH the Electron chrome UI (a file:// page) and
        # the web-content tabs. Pick a real web page, never the UI shell.
        self._page = await self._wait_for_web_page()

        # Track new tabs/popups so switch_tab can find them.
        self._context.on("page", self._on_new_page)
        try:
            await self._context.add_init_script(_STEALTH_JS)
        except Exception:  # noqa: BLE001
            pass
        log.info("Connected to Chromium over CDP at %s (active: %s)", config.cdp_url, self._page.url)

    async def detach(self) -> None:
        """Disconnect the CDP/Playwright session WITHOUT closing Electron. This
        removes the automation traces (the CDP Runtime leak) that make Cloudflare
        Turnstile fail — so a human can pass the challenge with a real click."""
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        self._browser = None
        self._context = None
        self._page = None

    async def reattach(self) -> None:
        """Re-establish the CDP connection after a detach (e.g. once the human has
        cleared the Cloudflare challenge)."""
        assert self._pw is not None
        self._browser = await self._pw.chromium.connect_over_cdp(config.cdp_url)
        contexts = self._browser.contexts
        self._context = contexts[0] if contexts else await self._browser.new_context()
        self._context.on("page", self._on_new_page)
        try:
            await self._context.add_init_script(_STEALTH_JS)
        except Exception:  # noqa: BLE001
            pass
        self._page = await self._wait_for_web_page()
        log.info("Reattached to Chromium (active: %s)", self._page.url)

    @staticmethod
    def _is_web_page(page: Page) -> bool:
        """True for real browsing tabs; excludes the Electron UI and devtools."""
        url = page.url or ""
        return not (url.startswith("file://") or url.startswith("devtools://") or url.startswith("chrome://"))

    async def _wait_for_web_page(self, retries: int = 30, delay: float = 0.5) -> Page:
        assert self._context is not None
        for _ in range(retries):
            web = [p for p in self._context.pages if self._is_web_page(p)]
            if web:
                return web[-1]  # most recently created tab
            await asyncio.sleep(delay)
        # Fall back to any page so the service still starts.
        pages = self._context.pages
        return pages[-1] if pages else await self._context.new_page()

    def _on_new_page(self, page: Page) -> None:
        if not self._is_web_page(page):
            return  # ignore the UI shell / devtools targets
        log.info("New tab/popup opened: %s", page.url)
        # Newly opened pages become the active focus by default.
        self._page = page

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not connected")
        return self._page

    @property
    def pages(self) -> list[Page]:
        """Web-content tabs only (excludes the Electron UI shell)."""
        if self._context is None:
            return []
        return [p for p in self._context.pages if self._is_web_page(p)]

    def set_active(self, page: Page) -> None:
        self._page = page

    async def ensure_alive(self) -> Page:
        """Guarantee the active page is open; if it was closed (e.g. a tab opened
        and the old one died), switch to another live web page."""
        if self._page is not None and not self._page.is_closed():
            return self._page
        ctx_pages = self._context.pages if self._context else []
        live_web = [p for p in ctx_pages if self._is_web_page(p) and not p.is_closed()]
        if live_web:
            self._page = live_web[-1]
            return self._page
        live_any = [p for p in ctx_pages if not p.is_closed()]
        if live_any:
            self._page = live_any[-1]
            return self._page
        raise RuntimeError("no open page available")

    def set_active_by_url(self, url: str) -> bool:
        """Point the agent at the tab the user is viewing (reported by Electron)."""
        if self._context is None or not url:
            return False
        for p in self._context.pages:
            if self._is_web_page(p) and p.url == url:
                self._page = p
                return True
        return False

    async def new_tab(self) -> Page:
        assert self._context is not None
        page = await self._context.new_page()
        self._page = page
        return page

    async def close(self) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
        finally:
            if self._pw is not None:
                await self._pw.stop()
