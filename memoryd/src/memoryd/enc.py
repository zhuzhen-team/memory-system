"""macOS Keychain-backed AES-256-GCM encryption for sensitive memories.

Per scope_hash -> 32-byte AES key stored in macOS Keychain via `keyring`.
File format: base64(nonce[12] || ciphertext || tag[16]).
Associated_data = scope_hash (prevents ciphertext from being swapped
between scopes).
"""
from __future__ import annotations

import base64
import os
import secrets
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


SERVICE: Final = "memoryd-scope-key"


class EncError(Exception):
    """Raised when encryption / decryption / key access fails."""


def _keyring():
    try:
        import keyring
    except ImportError as e:
        raise EncError("keyring SDK not installed") from e
    return keyring


def get_or_create_scope_key(scope_hash: str) -> bytes:
    kr = _keyring()
    existing = kr.get_password(SERVICE, scope_hash)
    if existing:
        try:
            return base64.b64decode(existing)
        except Exception as e:
            raise EncError(f"corrupt key for {scope_hash}") from e
    key = secrets.token_bytes(32)
    kr.set_password(SERVICE, scope_hash, base64.b64encode(key).decode())
    return key


def delete_scope_key(scope_hash: str) -> None:
    kr = _keyring()
    try:
        kr.delete_password(SERVICE, scope_hash)
    except Exception:
        pass  # best-effort


def encrypt_bytes(scope_hash: str, plaintext: bytes) -> bytes:
    key = get_or_create_scope_key(scope_hash)
    aes = AESGCM(key)
    nonce = secrets.token_bytes(12)
    ct = aes.encrypt(nonce, plaintext, scope_hash.encode())
    return base64.b64encode(nonce + ct)


def decrypt_bytes(scope_hash: str, blob: bytes) -> bytes:
    key = get_or_create_scope_key(scope_hash)
    raw = base64.b64decode(blob)
    nonce, ct = raw[:12], raw[12:]
    aes = AESGCM(key)
    return aes.decrypt(nonce, ct, scope_hash.encode())
