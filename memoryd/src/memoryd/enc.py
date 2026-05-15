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
    """Return 32-byte AES key for scope_hash.

    Mode is decided by config.toml [sensitive] key_source:
    - "random" (default, Plan 4): per-scope 32B random key in OS keyring
    - "passphrase" (Plan 6 opt-in): PBKDF2-HMAC-SHA256 derive from master passphrase
    """
    _check_backend_available()
    try:
        from .config import load_config
        cfg = load_config()
        key_source = cfg.sensitive.key_source if hasattr(cfg, "sensitive") else "random"
        kdf_iters = cfg.sensitive.kdf_iters if hasattr(cfg, "sensitive") else 600000
    except Exception:
        key_source = "random"
        kdf_iters = 600000
    if key_source == "passphrase":
        return _get_passphrase_scope_key(scope_hash, kdf_iters)
    return _get_random_scope_key(scope_hash)


def _get_passphrase_scope_key(scope_hash: str, iters: int) -> bytes:
    from . import passphrase as pp
    p = pp.get()
    if not p:
        raise EncError("master passphrase unset; run `memoryd set-passphrase`")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=scope_hash.encode("utf-8"),
        iterations=iters,
    )
    return kdf.derive(p)


def _get_random_scope_key(scope_hash: str) -> bytes:
    """Original Plan 4 implementation: random 32B per scope, stored in OS keyring."""
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


def _check_backend_available() -> None:
    """Raise EncError with platform-specific install hint if no keyring backend."""
    kr = _keyring()
    get_kr = getattr(kr, "get_keyring", None)
    if get_kr is None:
        # Stubbed keyring (e.g. tests inject a fake): nothing to check.
        return
    backend = get_kr()
    # keyring.backends.fail.Keyring is the no-op fallback when nothing usable
    if backend.__class__.__module__.endswith(".fail"):
        from .platforms import detect
        plat = detect()
        if plat == "linux":
            hint = (
                "No usable keyring backend. Install one:\n"
                "  Debian/Ubuntu: sudo apt install gnome-keyring libsecret-tools\n"
                "  Fedora:        sudo dnf install gnome-keyring\n"
                "  Or install KeePassXC and enable Secret Service Integration."
            )
        elif plat == "windows":
            hint = "Windows Credential Manager unavailable — check user session not headless."
        else:
            hint = "macOS Keychain unavailable — unlock keychain and retry."
        raise EncError(hint)
