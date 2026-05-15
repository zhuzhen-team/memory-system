from unittest.mock import patch

import keyring
import keyring.backend
import pytest

from memoryd import enc


class _FailKeyring(keyring.backend.KeyringBackend):
    """Marks itself with .fail module path to mimic fallback."""
    priority = -1
    @classmethod
    def get_priority(cls): return -1
    def get_password(self, service, account): return None
    def set_password(self, service, account, password): pass
    def delete_password(self, service, account): pass


def test_no_backend_raises_friendly_on_linux(monkeypatch):
    fail_kr = _FailKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: fail_kr)
    monkeypatch.setattr(fail_kr.__class__, "__module__", "keyring.backends.fail")
    monkeypatch.setattr("platform.system", lambda: "Linux")
    with pytest.raises(enc.EncError) as exc:
        enc._check_backend_available()
    assert "gnome-keyring" in str(exc.value)


def test_no_backend_raises_friendly_on_windows(monkeypatch):
    fail_kr = _FailKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: fail_kr)
    monkeypatch.setattr(fail_kr.__class__, "__module__", "keyring.backends.fail")
    monkeypatch.setattr("platform.system", lambda: "Windows")
    with pytest.raises(enc.EncError) as exc:
        enc._check_backend_available()
    assert "Windows Credential Manager" in str(exc.value)


def test_real_backend_does_not_raise(monkeypatch):
    """On the test machine (macOS) the real Keychain is available; this should not raise."""
    enc._check_backend_available()
