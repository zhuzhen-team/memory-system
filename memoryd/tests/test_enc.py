"""enc.py tests with in-memory keyring stub."""
from typing import Any

import pytest

from memoryd import enc
from memoryd.enc import (
    EncError,
    decrypt_bytes,
    delete_scope_key,
    encrypt_bytes,
    get_or_create_scope_key,
)


class _InMemKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self.store[(service, account)] = password

    def delete_password(self, service: str, account: str) -> None:
        self.store.pop((service, account), None)


@pytest.fixture(autouse=True)
def stub_keyring(monkeypatch):
    fake = _InMemKeyring()
    monkeypatch.setattr(enc, "_keyring", lambda: fake)
    return fake


def test_get_or_create_creates_new_key(stub_keyring):
    k = get_or_create_scope_key("scope1")
    assert len(k) == 32
    assert ("memoryd-scope-key", "scope1") in stub_keyring.store


def test_get_or_create_returns_existing_key(stub_keyring):
    k1 = get_or_create_scope_key("scope1")
    k2 = get_or_create_scope_key("scope1")
    assert k1 == k2


def test_encrypt_decrypt_roundtrip():
    pt = b"hello sensitive content"
    blob = encrypt_bytes("scope1", pt)
    assert pt not in blob  # actually encrypted
    out = decrypt_bytes("scope1", blob)
    assert out == pt


def test_decrypt_rejects_wrong_scope_hash():
    """Associated_data binding prevents cross-scope ciphertext reuse."""
    blob = encrypt_bytes("scope1", b"secret")
    with pytest.raises(Exception):
        decrypt_bytes("scope2", blob)


def test_delete_scope_key(stub_keyring):
    get_or_create_scope_key("s")
    delete_scope_key("s")
    assert ("memoryd-scope-key", "s") not in stub_keyring.store


def test_encrypt_different_nonces_each_call():
    """Two encryptions of the same plaintext produce different ciphertexts."""
    a = encrypt_bytes("scope1", b"same input")
    b = encrypt_bytes("scope1", b"same input")
    assert a != b
