from unittest.mock import patch

import pytest

from memoryd.platforms import (
    PlatformName,
    UnsupportedPlatform,
    detect,
    is_linux,
    is_macos,
    is_windows,
)


@pytest.mark.parametrize(
    "system,expected",
    [
        ("Darwin", "darwin"),
        ("Linux", "linux"),
        ("Windows", "windows"),
        ("darwin", "darwin"),
    ],
)
def test_detect_known_platforms(system, expected):
    with patch("platform.system", return_value=system):
        assert detect() == expected


def test_detect_unknown_raises():
    with patch("platform.system", return_value="Plan9"):
        with pytest.raises(UnsupportedPlatform):
            detect()


def test_helpers_dispatch():
    with patch("platform.system", return_value="Darwin"):
        assert is_macos() and not is_linux() and not is_windows()
    with patch("platform.system", return_value="Linux"):
        assert is_linux() and not is_macos() and not is_windows()
    with patch("platform.system", return_value="Windows"):
        assert is_windows() and not is_macos() and not is_linux()
