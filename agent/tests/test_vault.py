from navigator import vault


def test_domain_normalization():
    assert vault.domain_of("https://www.github.com/login") == "github.com"
    assert vault.domain_of("accounts.google.com") == "accounts.google.com"
    assert vault.domain_of("EXAMPLE.com") == "example.com"


def test_store_and_get_roundtrip(monkeypatch):
    # Force the in-memory fallback so the test never touches the real keychain.
    monkeypatch.setattr(vault, "_HAS_KEYRING", False)
    vault._MEM.clear()

    vault.store(vault.Credential(domain="github.com", username="alice", password="s3cret"))
    cred = vault.get("https://github.com/login")
    assert cred is not None
    assert cred.username == "alice"
    assert cred.password == "s3cret"


def test_parent_domain_fallback(monkeypatch):
    monkeypatch.setattr(vault, "_HAS_KEYRING", False)
    vault._MEM.clear()

    vault.store(vault.Credential(domain="google.com", username="bob", password="pw"))
    # Stored on the registrable domain; lookup on a subdomain should still resolve.
    cred = vault.get("accounts.google.com")
    assert cred is not None
    assert cred.username == "bob"
