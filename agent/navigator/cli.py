"""Tiny CLI to manage the credential vault.

Usage:
  python -m navigator.cli add github.com
  python -m navigator.cli list
  python -m navigator.cli remove github.com
"""
from __future__ import annotations

import getpass
import sys

from . import vault


def _add(domain: str) -> None:
    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    totp = getpass.getpass("TOTP seed (base32, optional): ").strip() or None
    vault.store(vault.Credential(domain=domain, username=username, password=password, totp_seed=totp))
    print(f"Stored credentials for {vault.domain_of(domain)}")


def _remove(domain: str) -> None:
    vault.delete(domain)
    print(f"Removed credentials for {vault.domain_of(domain)}")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "add" and len(argv) == 2:
        _add(argv[1])
    elif cmd == "remove" and len(argv) == 2:
        _remove(argv[1])
    elif cmd == "list":
        # keyring has no enumeration API; we just confirm the backend.
        import keyring

        print(f"Vault backend: {keyring.get_keyring().__class__.__name__}")
        print("(keyring does not support listing; query a specific domain via the agent.)")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
