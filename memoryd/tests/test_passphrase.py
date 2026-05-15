from unittest.mock import MagicMock

import pytest


def test_get_returns_env_when_set(monkeypatch):
    monkeypatch.setenv("MEMORYD_MASTER_PASSPHRASE", "from-env-12345")
    from memoryd import passphrase
    assert passphrase.get() == b"from-env-12345"


def test_get_returns_keyring_value_when_env_unset(monkeypatch):
    monkeypatch.delenv("MEMORYD_MASTER_PASSPHRASE", raising=False)
    import keyring
    fake_kr = MagicMock()
    fake_kr.get_password.return_value = "from-keyring"
    monkeypatch.setattr(keyring, "get_password",
                       lambda s, a: fake_kr.get_password(s, a))
    from memoryd import passphrase
    assert passphrase.get() == b"from-keyring"
    fake_kr.get_password.assert_called_once_with(
        "memoryd-master-passphrase", "default"
    )


def test_get_returns_none_when_keyring_empty(monkeypatch):
    monkeypatch.delenv("MEMORYD_MASTER_PASSPHRASE", raising=False)
    import keyring
    monkeypatch.setattr(keyring, "get_password", lambda s, a: None)
    from memoryd import passphrase
    assert passphrase.get() is None


def test_get_returns_none_when_keyring_raises(monkeypatch):
    monkeypatch.delenv("MEMORYD_MASTER_PASSPHRASE", raising=False)
    import keyring
    def boom(s, a): raise RuntimeError("locked")
    monkeypatch.setattr(keyring, "get_password", boom)
    from memoryd import passphrase
    assert passphrase.get() is None


def test_set_rejects_too_short():
    from memoryd import passphrase
    with pytest.raises(passphrase.PassphraseError):
        passphrase.set_("short")


def test_set_stores_in_keyring(monkeypatch):
    import keyring
    fake_set = MagicMock()
    monkeypatch.setattr(keyring, "set_password", fake_set)
    from memoryd import passphrase
    passphrase.set_("a-secure-passphrase-1234")
    fake_set.assert_called_once_with(
        "memoryd-master-passphrase", "default", "a-secure-passphrase-1234"
    )
