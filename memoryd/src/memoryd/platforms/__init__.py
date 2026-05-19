"""Platform detection + dispatch.

Three supported: darwin (macOS), linux, windows. Anything else raises
UnsupportedPlatform when a platform-specific helper is invoked.
"""
from __future__ import annotations

import platform
from typing import Literal

PlatformName = Literal["darwin", "linux", "windows"]


class UnsupportedPlatform(Exception):
    """Raised when running on a platform memoryd does not support."""


def detect() -> PlatformName:
    """Return the current platform name as PlatformName."""
    name = platform.system().lower()
    if name == "darwin":
        return "darwin"
    if name == "linux":
        return "linux"
    if name == "windows":
        return "windows"
    raise UnsupportedPlatform(f"unsupported platform: {platform.system()}")


def is_macos() -> bool:
    return detect() == "darwin"


def is_linux() -> bool:
    return detect() == "linux"


def is_windows() -> bool:
    return detect() == "windows"
