"""Service entrypoint: connect to Chromium over CDP, then run the WS bridge."""
from __future__ import annotations

import asyncio
import logging

from .bridge import Bridge
from .browser import BrowserSession
from .config import config
from .llm import LLMClient


def _setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def main() -> None:
    _setup_logging()
    log = logging.getLogger("navigator")

    session = BrowserSession()
    log.info("Waiting for Chromium CDP at %s ...", config.cdp_url)
    await session.connect()

    llm = LLMClient()
    bridge = Bridge(session, llm)
    try:
        await bridge.serve_forever()
    finally:
        await session.close()


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
