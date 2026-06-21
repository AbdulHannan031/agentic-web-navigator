"""Perception: turn the live page into a compact observation for the LLM.

DOM-first hybrid: the primary signal is an indexed list of interactive elements
(each tagged with a stable `data-agent-id`), so the model references elements by
index (`click(7)`) instead of guessing pixel coordinates. A screenshot is only
attached when explicitly requested (CAPTCHA, canvas, ambiguous layout).
"""
from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from typing import Optional

from patchright.async_api import Page

log = logging.getLogger("navigator.perception")

# JS injected into EACH frame (top document + every iframe). Walks the DOM, finds
# visible interactive elements, tags each with a GLOBALLY-unique data-agent-id
# (starting at `startIndex`), and returns them. Running per-frame is what lets the
# agent see/click inside cross-origin iframes (payments, embedded logins, etc.).
_COLLECT_JS = r"""
(startIndex) => {
  const INTERACTIVE = new Set(['A','BUTTON','INPUT','SELECT','TEXTAREA','SUMMARY']);
  const CLICKABLE_ROLES = new Set(['button','link','checkbox','radio','tab','menuitem','switch','option']);

  function isVisible(el) {
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    if (r.bottom < 0 || r.right < 0 || r.top > window.innerHeight || r.left > window.innerWidth) return false;
    return true;
  }

  function label(el) {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    if (el.placeholder) return el.placeholder.trim();
    if (el.name) return el.name.trim();
    const title = el.getAttribute('title');
    if (title) return title.trim();
    const text = (el.innerText || el.value || '').trim().replace(/\s+/g, ' ');
    return text.slice(0, 120);
  }

  function role(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const t = el.tagName.toLowerCase();
    if (t === 'a') return 'link';
    if (t === 'button') return 'button';
    if (t === 'input') return (el.type || 'text');
    if (t === 'select') return 'select';
    if (t === 'textarea') return 'textbox';
    return t;
  }

  function isInteractive(el) {
    if (INTERACTIVE.has(el.tagName)) return true;
    const r = el.getAttribute('role');
    if (r && CLICKABLE_ROLES.has(r)) return true;
    if (el.hasAttribute('onclick')) return true;
    if (el.getAttribute('contenteditable') === 'true') return true;
    if (el.tabIndex >= 0 && (el.onclick || el.getAttribute('role'))) return true;
    return false;
  }

  // Clear stale ids in THIS document only.
  document.querySelectorAll('[data-agent-id]').forEach(e => e.removeAttribute('data-agent-id'));

  const all = Array.from(document.querySelectorAll('*'));
  const out = [];
  let idx = startIndex;
  for (const el of all) {
    if (!isInteractive(el)) continue;
    if (!isVisible(el)) continue;
    el.setAttribute('data-agent-id', String(idx));
    out.push({
      index: idx,
      role: role(el),
      label: label(el),
      value: (el.value || '').toString().slice(0, 80),
      disabled: !!el.disabled,
    });
    idx++;
  }
  return {
    url: location.href,
    title: document.title,
    scrollY: Math.round(window.scrollY),
    scrollHeight: Math.round(document.body ? document.body.scrollHeight : 0),
    viewportHeight: window.innerHeight,
    count: out.length,
    elements: out,
  };
}
"""


@dataclass
class Observation:
    url: str
    title: str
    scroll_y: int
    scroll_height: int
    viewport_height: int
    elements: list[dict] = field(default_factory=list)
    screenshot_b64: Optional[str] = None

    def render_text(self, max_elements: int = 120) -> str:
        """Compact text form fed to the LLM."""
        lines = [f"URL: {self.url}", f"Title: {self.title}"]
        more = ""
        if self.scroll_height > self.viewport_height:
            pct = int(100 * self.scroll_y / max(1, self.scroll_height - self.viewport_height))
            more = f" (scrolled {pct}% — more content may be below/above)"
        lines.append(f"Page{more}")
        lines.append("Interactive elements [index] role \"label\" (value):")
        for el in self.elements[:max_elements]:
            val = f' = "{el["value"]}"' if el.get("value") else ""
            dis = " [disabled]" if el.get("disabled") else ""
            lines.append(f'  [{el["index"]}] {el["role"]} "{el["label"]}"{val}{dis}')
        if len(self.elements) > max_elements:
            lines.append(f"  ... {len(self.elements) - max_elements} more elements (scroll to reveal)")
        return "\n".join(lines)


async def observe(page: Page, with_screenshot: bool = False, max_elements: int = 250) -> Observation:
    """Build an Observation by scanning the top document AND every iframe.

    Each frame is evaluated independently (Playwright can reach into cross-origin
    frames), with a running offset so indices stay globally unique. Actions later
    resolve an index by searching all frames for its data-agent-id.
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:  # noqa: BLE001 - best effort; some pages never fully settle
        pass

    main: dict | None = None
    elements: list[dict] = []
    offset = 0
    scanned = 0
    for frame in page.frames:
        if offset >= max_elements or scanned >= 25:
            break
        scanned += 1
        try:
            # Bound EACH frame's evaluate: a slow/unresponsive cross-origin frame
            # (ads, YouTube/social embeds) must never freeze the whole observation.
            data = await asyncio.wait_for(frame.evaluate(_COLLECT_JS, offset), timeout=2.5)
        except Exception:  # noqa: BLE001 - timed-out/detached/blank frames are skipped
            continue
        if main is None:
            main = data  # the top frame supplies url/title/scroll
        elements.extend(data["elements"])
        offset += data["count"]

    if main is None:  # extremely defensive fallback
        main = {"url": page.url, "title": "", "scrollY": 0, "scrollHeight": 0, "viewportHeight": 0}

    shot = None
    if with_screenshot:
        raw = await page.screenshot(type="png", full_page=False)
        shot = base64.b64encode(raw).decode("ascii")

    return Observation(
        url=main["url"],
        title=main["title"],
        scroll_y=main["scrollY"],
        scroll_height=main["scrollHeight"],
        viewport_height=main["viewportHeight"],
        elements=elements,
        screenshot_b64=shot,
    )
