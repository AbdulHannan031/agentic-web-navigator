"""Encrypted credential vault.

Credentials are stored in the OS keychain via the `keyring` library
(macOS Keychain / Windows Credential Manager / libsecret on Linux). Secrets are
NEVER sent to the LLM — the model only ever references a domain string; the
actual username/password/TOTP are injected locally by the action executor.

Supports optional TOTP/2FA codes when a base32 secret seed is stored.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger("navigator.vault")

_SERVICE = "agentic-web-navigator"

try:
    import keyring  # type: ignore

    _HAS_KEYRING = True
except Exception:  # noqa: BLE001
    keyring = None  # type: ignore
    _HAS_KEYRING = False
    log.warning("keyring not available — vault will use an in-memory store only")

# Fallback in-memory store (process lifetime only) when keyring is unavailable.
_MEM: dict[str, str] = {}


@dataclass
class Credential:
    domain: str
    username: str
    password: str
    totp_seed: Optional[str] = None


def _key(domain: str) -> str:
    return domain.lower().lstrip(".")


def domain_of(url_or_domain: str) -> str:
    """Normalize a URL or bare host to a registrable-ish domain key."""
    s = url_or_domain.strip()
    if "://" in s:
        host = urlparse(s).hostname or s
    else:
        host = s
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _get_raw(domain: str) -> Optional[str]:
    k = _key(domain)
    if _HAS_KEYRING:
        return keyring.get_password(_SERVICE, k)
    return _MEM.get(k)


def _set_raw(domain: str, blob: str) -> None:
    k = _key(domain)
    if _HAS_KEYRING:
        keyring.set_password(_SERVICE, k, blob)
    else:
        _MEM[k] = blob


def store(cred: Credential) -> None:
    blob = json.dumps(
        {
            "username": cred.username,
            "password": cred.password,
            "totp_seed": cred.totp_seed,
        }
    )
    _set_raw(domain_of(cred.domain), blob)
    log.info("Stored credentials for %s", domain_of(cred.domain))


def get(domain: str) -> Optional[Credential]:
    """Look up by domain; tries exact host then its parent domain."""
    host = domain_of(domain)
    candidates = [host]
    parts = host.split(".")
    if len(parts) > 2:
        candidates.append(".".join(parts[-2:]))  # e.g. accounts.google.com -> google.com
    for cand in candidates:
        raw = _get_raw(cand)
        if raw:
            data = json.loads(raw)
            return Credential(
                domain=cand,
                username=data["username"],
                password=data["password"],
                totp_seed=data.get("totp_seed"),
            )
    return None


def delete(domain: str) -> None:
    k = _key(domain_of(domain))
    if _HAS_KEYRING:
        try:
            keyring.delete_password(_SERVICE, k)
        except Exception:  # noqa: BLE001
            pass
    else:
        _MEM.pop(k, None)


def current_totp(seed: str) -> Optional[str]:
    """Generate the current 6-digit TOTP code from a base32 seed."""
    try:
        import pyotp  # type: ignore

        return pyotp.TOTP(seed).now()
    except Exception as e:  # noqa: BLE001
        log.warning("Could not generate TOTP: %s", e)
        return None
