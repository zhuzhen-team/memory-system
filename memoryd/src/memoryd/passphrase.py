"""Master passphrase: env-overridable, OS keyring backed.

Used in Plan 6 sensitive scope passphrase mode (opt-in). Not used in
default random-key mode.
"""
from __future__ import annotations

import os
from typing import Final

_SERVICE: Final = "memoryd-master-passphrase"
_ACCOUNT: Final = "default"
_ENV: Final = "MEMORYD_MASTER_PASSPHRASE"
_MIN_LEN: Final = 12


class PassphraseError(Exception):
    """Raised when passphrase is invalid (too short, mismatched, etc)."""


def get() -> bytes | None:
    """Return passphrase bytes; env var wins over keyring; None if unset."""
    env = os.environ.get(_ENV)
    if env:
        return env.encode("utf-8")
    try:
        import keyring
        v = keyring.get_password(_SERVICE, _ACCOUNT)
    except Exception:
        return None
    return v.encode("utf-8") if v else None


def set_(passphrase: str) -> None:
    """Validate and store passphrase in OS keyring."""
    if len(passphrase) < _MIN_LEN:
        raise PassphraseError(f"passphrase must be at least {_MIN_LEN} characters")
    import keyring
    keyring.set_password(_SERVICE, _ACCOUNT, passphrase)


def clear() -> None:
    """Remove passphrase from OS keyring (best-effort)."""
    try:
        import keyring
        keyring.delete_password(_SERVICE, _ACCOUNT)
    except Exception:
        pass
