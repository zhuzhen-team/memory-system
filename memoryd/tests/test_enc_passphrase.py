from unittest.mock import patch

import pytest

from memoryd import enc


def _passphrase_cfg(monkeypatch, *, mode="passphrase", iters=600000):
    """Make load_config() return cfg with sensitive.key_source=mode."""
    class _Sensitive:
        key_source = mode
        kdf_iters = iters
    class _Cfg:
        sensitive = _Sensitive()
    monkeypatch.setattr("memoryd.config.load_config", lambda: _Cfg())


def test_passphrase_mode_derivation_deterministic(monkeypatch):
    _passphrase_cfg(monkeypatch)
    monkeypatch.setenv("MEMORYD_MASTER_PASSPHRASE", "my-master-passphrase-1234")
    k1 = enc._get_passphrase_scope_key("scope_abc", 600000)
    k2 = enc._get_passphrase_scope_key("scope_abc", 600000)
    assert k1 == k2
    assert len(k1) == 32


def test_passphrase_mode_different_scope_different_key(monkeypatch):
    _passphrase_cfg(monkeypatch)
    monkeypatch.setenv("MEMORYD_MASTER_PASSPHRASE", "my-master-passphrase-1234")
    k1 = enc._get_passphrase_scope_key("scope_aaa", 600000)
    k2 = enc._get_passphrase_scope_key("scope_bbb", 600000)
    assert k1 != k2


def test_passphrase_mode_raises_when_unset(monkeypatch):
    _passphrase_cfg(monkeypatch)
    monkeypatch.delenv("MEMORYD_MASTER_PASSPHRASE", raising=False)
    # also clear keyring path
    import keyring
    monkeypatch.setattr(keyring, "get_password", lambda s, a: None)
    with pytest.raises(enc.EncError) as exc:
        enc._get_passphrase_scope_key("scope_x", 600000)
    assert "passphrase" in str(exc.value).lower()


def test_dispatch_uses_passphrase_when_config_says(monkeypatch):
    _passphrase_cfg(monkeypatch, mode="passphrase")
    monkeypatch.setenv("MEMORYD_MASTER_PASSPHRASE", "my-master-passphrase-1234")
    # Skip backend check
    monkeypatch.setattr(enc, "_check_backend_available", lambda: None)
    k = enc.get_or_create_scope_key("scope_xyz")
    # should match direct passphrase-derive
    k2 = enc._get_passphrase_scope_key("scope_xyz", 600000)
    assert k == k2


def test_dispatch_uses_random_when_config_says_random(monkeypatch):
    _passphrase_cfg(monkeypatch, mode="random")
    monkeypatch.setattr(enc, "_check_backend_available", lambda: None)
    # mock _get_random_scope_key
    sentinel = b"\x00" * 32
    called = {"n": 0}
    def fake_random(h):
        called["n"] += 1
        return sentinel
    monkeypatch.setattr(enc, "_get_random_scope_key", fake_random)
    k = enc.get_or_create_scope_key("scope_xyz")
    assert k == sentinel
    assert called["n"] == 1
