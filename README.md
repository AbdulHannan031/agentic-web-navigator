# Agentic Web Navigator

An autonomous web-navigation agent that drives **its own Chromium browser** to
perform real tasks on any website — navigating, filling forms, logging in with
stored credentials, extracting data, and completing multi-step workflows — driven
by **OpenAI GPT-4o mini**.

It is built as two cooperating processes:

| Process | Stack | Role |
| --- | --- | --- |
| **Browser shell** (`browser/`) | Electron + Chromium | A real branded browser: tabs, address bar, an **agent side-panel**, and a **human-takeover overlay**. Exposes Chromium over CDP. |
| **Agent service** (`agent/`) | Python | The brain: connects to Chromium over CDP (Playwright), perceives the page (indexed DOM + optional vision), reasons with GPT-4o mini, and acts. |

They coordinate over a local **WebSocket** (`shared/protocol.json`); the agent
controls the browser over the **Chrome DevTools Protocol** (CDP).

```
Electron (Chromium + UI)  ──CDP :9222──▶  Python agent (Playwright + GPT-4o mini)
        ▲                                          │
        └──────────── WebSocket :8787 ─────────────┘  (status / actions / human-help)
```

## How it works

1. **Observe** — the page is reduced to an indexed list of interactive elements
   (`[7] button "Sign in"`), each tagged with a stable `data-agent-id`. A
   screenshot is attached only when needed (CAPTCHA, canvas). *DOM-first hybrid —
   cheap and reliable, ideal for a small model.*
2. **Think** — GPT-4o mini receives the observation + tool schema and returns one
   tool call.
3. **Act** — the executor maps the call to Playwright (`click`, `type`,
   `navigate`, `extract`, `get_credentials`, …).
4. Repeat until `done()`.

**Autonomous logins:** credentials live in the OS keychain (`keyring`) and are
injected locally — they are never sent to the model. TOTP/2FA supported.

**CAPTCHAs:** detected via DOM heuristics → auto-attempted (vision + optional
external solver) → if still blocked, the agent escalates to a **human-takeover
overlay**, then resumes.

## Staying logged in (persistent profile)

The browser uses a **persistent Chromium profile** stored on disk, so logins
survive restarts — exactly like a normal browser. Sign into your Google account
once and **all of Google Workspace** (Gmail, Drive, Docs, Calendar, …) shares that
session automatically, because they all use the same `google.com` cookies. The
agent drives this same profile, so once you're signed in, it is too — no repeated
logins.

```
~/Library/Application Support/agentic-web-navigator-shell/   (macOS)
  Cookies, Local Storage/, ...   ← your sessions live here
```

> **Tip for Google:** Google sometimes blocks *programmatic* sign-in on
> automation-controlled browsers ("this browser may not be secure"). The smoothest
> path is to log into Google **manually once** in the browser window (or via the
> takeover overlay); persistence then keeps you signed in and the agent reuses the
> session. To sign out / reset, delete the profile folder above.

## Setup

Prerequisites: Node.js ≥ 18, Python ≥ 3.10.

```bash
# 1. Browser shell
cd browser && npm install && cd ..

# 2. Agent service
cd agent
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m playwright install chromium      # Playwright's CDP client
cd ..

# 3. Configure
cp .env.example .env        # add your OPENAI_API_KEY
```

### Store a login (optional, for autonomous sign-in)

```bash
cd agent && python -m navigator.cli add github.com
```

## Run

```bash
# Terminal A — the browser (opens the CDP port)
cd browser && npm start

# Terminal B — the agent service (attaches to the browser)
cd agent && python -m navigator.server
```

Or use the helper: `bash scripts/start.sh`.

Then type a task into the agent panel, e.g.
*"Go to Wikipedia, find Alan Turing's birth year, and tell me."*

## Tests

```bash
cd agent && pip install -e '.[dev]' && pytest -q
```

## Safety & scope

The agent acts autonomously, including typing stored credentials and attempting
CAPTCHAs. Automated browsing and CAPTCHA-solving can violate some sites' Terms of
Service and may be subject to local law. **You are responsible for using this only
where you are authorized to do so.** The human-takeover fallback is the reliable
path when a site genuinely blocks automation.

## Project layout

```
browser/   Electron shell (main/ tabs/ bridge/ preload/ renderer/)
agent/     Python service (navigator/: loop, perception, actions, llm, vault, captcha, bridge)
shared/    WebSocket protocol contract
```
