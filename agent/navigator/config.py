"""Central configuration for the agent service.

Values come from environment variables (optionally loaded from a local .env)
so nothing sensitive is hard-coded. See .env.example at the repo root.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader (no external dependency needed)."""
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if not candidate.exists():
            continue
        for raw in candidate.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


@dataclass
class Config:
    # --- LLM ---
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    model: str = field(default_factory=lambda: os.environ.get("NAV_MODEL", "gpt-4o-mini"))
    temperature: float = field(default_factory=lambda: float(os.environ.get("NAV_TEMPERATURE", "0.1")))

    # --- Browser / CDP ---
    cdp_port: int = field(default_factory=lambda: int(os.environ.get("NAV_CDP_PORT", "9222")))
    cdp_host: str = field(default_factory=lambda: os.environ.get("NAV_CDP_HOST", "127.0.0.1"))

    # --- WebSocket bridge (Python is the server) ---
    ws_host: str = field(default_factory=lambda: os.environ.get("NAV_WS_HOST", "127.0.0.1"))
    ws_port: int = field(default_factory=lambda: int(os.environ.get("NAV_WS_PORT", "8787")))

    # --- Agent loop ---
    max_steps: int = field(default_factory=lambda: int(os.environ.get("NAV_MAX_STEPS", "40")))
    step_timeout_s: float = field(default_factory=lambda: float(os.environ.get("NAV_STEP_TIMEOUT", "30")))
    send_screenshot_every_step: bool = field(
        default_factory=lambda: os.environ.get("NAV_ALWAYS_SCREENSHOT", "0") == "1"
    )

    # --- CAPTCHA ---
    external_solver: str = field(default_factory=lambda: os.environ.get("NAV_CAPTCHA_SOLVER", ""))  # "" | "2captcha"
    solver_api_key: str = field(default_factory=lambda: os.environ.get("NAV_SOLVER_API_KEY", ""))

    # --- Misc ---
    stealth: bool = field(default_factory=lambda: os.environ.get("NAV_STEALTH", "1") == "1")
    log_level: str = field(default_factory=lambda: os.environ.get("NAV_LOG_LEVEL", "INFO"))

    @property
    def cdp_url(self) -> str:
        return f"http://{self.cdp_host}:{self.cdp_port}"


config = Config()
