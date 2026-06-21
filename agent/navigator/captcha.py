"""CAPTCHA detection, best-effort auto-attempt, and human escalation.

Policy (per the approved plan, fully-autonomous-first):
  1. Detect a challenge via DOM heuristics.
  2. Auto-attempt:
       (a) GPT-4o mini vision on a screenshot for simple text/image challenges.
       (b) Optional pluggable external solver (e.g. 2Captcha) if configured.
  3. If auto-solve fails or confidence is low, escalate to a human via the
     takeover overlay (handled by the agent loop / bridge).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from patchright.async_api import Page

from .config import config

log = logging.getLogger("navigator.captcha")

# Detect ONLY an actually-visible, active challenge widget. Many sites embed
# reCAPTCHA/Turnstile invisibly or leave an already-passed widget in the DOM —
# those must NOT be treated as a blocker (that caused false "solve the CAPTCHA"
# prompts). We require the provider iframe/element to be visibly rendered.
_DETECT_JS = r"""
() => {
  function visible(el) {
    if (!el) return false;
    const s = window.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity || '1') < 0.1) return false;
    const r = el.getBoundingClientRect();
    return r.width > 40 && r.height > 30 && r.bottom > 0 && r.right > 0;
  }
  function visibleFrame(re) {
    return Array.from(document.querySelectorAll('iframe')).some(f => re.test(f.src || '') && visible(f));
  }
  const signals = [];
  if (visibleFrame(/google\.com\/recaptcha\/(api2|enterprise)\/(anchor|bframe)/i) || visible(document.querySelector('.g-recaptcha')))
    signals.push('recaptcha');
  if (visibleFrame(/hcaptcha\.com/i) || visible(document.querySelector('.h-captcha')))
    signals.push('hcaptcha');
  if (visibleFrame(/challenges\.cloudflare\.com/i) || visible(document.querySelector('.cf-turnstile')))
    signals.push('turnstile');
  return { present: signals.length > 0, signals };
}
"""


@dataclass
class CaptchaState:
    present: bool
    signals: list[str]


async def detect(page: Page) -> CaptchaState:
    try:
        data = await asyncio.wait_for(page.evaluate(_DETECT_JS), timeout=2.0)
        return CaptchaState(present=bool(data["present"]), signals=list(data["signals"]))
    except Exception as e:  # noqa: BLE001 - timed out or page busy
        log.debug("captcha detect failed: %s", e)
        return CaptchaState(present=False, signals=[])


async def try_auto_solve(page: Page, llm) -> bool:
    """Best-effort automated solve. Returns True if it believes it succeeded.

    `llm` is the LLMClient (used for vision on simple challenges). Token/iframe
    based providers (reCAPTCHA/hCaptcha/Turnstile) generally cannot be solved by
    a vision model alone — those route to the external solver if configured,
    otherwise they escalate to a human.
    """
    state = await detect(page)
    if not state.present:
        return True  # nothing to solve

    # (b) External solver, if the user opted in via config.
    if config.external_solver:
        ok = await _external_solve(page, state)
        if ok:
            return True

    # (a) Vision attempt only makes sense for simple in-page text/image captchas,
    # not for sandboxed iframe widgets we cannot script into.
    if state.signals == ["text"]:
        try:
            shot = await page.screenshot(type="png")
            answer = await llm.read_captcha_image(shot)
            if answer:
                # Try to type the answer into the most likely text input.
                box = page.locator("input[type=text], input:not([type])").first
                if await box.count() > 0:
                    await box.fill(answer)
                    log.info("Vision auto-solve typed a candidate answer")
                    return True
        except Exception as e:  # noqa: BLE001
            log.info("vision auto-solve failed: %s", e)

    return False


async def _external_solve(page: Page, state: CaptchaState) -> bool:
    """Hook for a third-party solving provider. Disabled unless configured.

    Intentionally a stub: real integration (2Captcha/anti-captcha) submits the
    site key + page URL, polls for a token, and injects it. Left pluggable so the
    core app has no hard dependency on a paid service.
    """
    if config.external_solver == "2captcha" and config.solver_api_key:
        log.info("External solver '2captcha' configured but integration is a stub; escalating.")
    return False
