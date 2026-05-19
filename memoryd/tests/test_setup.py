"""memoryd setup subcommand tests."""
import json
import shutil
import tomllib
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.setup import (
    backup_file,
    install_launchd_mirror,
    remove_codex_stop_hook,
    swap_codex_notify,
)


_SAMPLE_TOML = """\
model = "gpt-5.5"
notify = ["/Applications/Codex Computer Use.app/SkyComputerUseClient", "turn-ended"]

[mcp_servers.feishu]
command = "node"
args = ["/x"]

[features]
memories = true
"""


def test_backup_file_creates_timestamped_copy(tmp_path: Path):
    src = tmp_path / "config.toml"
    src.write_text(_SAMPLE_TOML)
    backup_dir = tmp_path / "backups"
    bp = backup_file(src, backup_dir=backup_dir)
    assert bp.exists()
    assert bp.read_text() == _SAMPLE_TOML
    assert bp.parent == backup_dir


def test_swap_codex_notify_to_probe_preserves_other_keys(tmp_path: Path):
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    cfg = codex_dir / "config.toml"
    cfg.write_text(_SAMPLE_TOML)
    backup_dir = tmp_path / "backups"

    state_file = swap_codex_notify(
        to="probe",
        codex_dir=codex_dir,
        backup_dir=backup_dir,
        probe_path="/path/to/probe.sh",
        wrapper_path="/path/to/wrapper.sh",
    )
    assert state_file.exists()  # state file remembers original notify

    data = tomllib.loads(cfg.read_text())
    assert data["notify"][0].endswith("probe.sh")
    # other keys untouched
    assert data["model"] == "gpt-5.5"
    assert "mcp_servers" in data
    assert data["features"]["memories"] is True


def test_swap_to_wrapper_includes_original_notify_in_state(tmp_path: Path):
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    cfg = codex_dir / "config.toml"
    cfg.write_text(_SAMPLE_TOML)
    backup_dir = tmp_path / "backups"

    swap_codex_notify(
        to="wrapper",
        codex_dir=codex_dir,
        backup_dir=backup_dir,
        probe_path="/p",
        wrapper_path="/w",
    )
    state = json.loads((codex_dir / ".memoryd-notify-state.json").read_text())
    assert state["original"][0].startswith("/Applications/")


def test_swap_back_to_original(tmp_path: Path):
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    cfg = codex_dir / "config.toml"
    cfg.write_text(_SAMPLE_TOML)
    backup_dir = tmp_path / "backups"

    swap_codex_notify(to="wrapper", codex_dir=codex_dir, backup_dir=backup_dir, probe_path="/p", wrapper_path="/w")
    swap_codex_notify(to="original", codex_dir=codex_dir, backup_dir=backup_dir, probe_path="/p", wrapper_path="/w")

    data = tomllib.loads(cfg.read_text())
    assert data["notify"][0].startswith("/Applications/")


def test_remove_codex_stop_hook_drops_only_stop_entry(tmp_path: Path):
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    hooks = codex_dir / "hooks.json"
    hooks.write_text(json.dumps({
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "/x/codex-stop-hook.sh"}]}],
            "OtherEvent": [{"hooks": [{"type": "command", "command": "/y/keep.sh"}]}],
        }
    }))
    backup_dir = tmp_path / "backups"

    remove_codex_stop_hook(codex_dir=codex_dir, backup_dir=backup_dir)

    data = json.loads(hooks.read_text())
    assert "Stop" not in data["hooks"]
    assert "OtherEvent" in data["hooks"]


def test_install_launchd_mirror_renders_template(tmp_path: Path):
    template_src = tmp_path / "template.plist"
    template_src.write_text("<plist>__MEMORYD_BIN__ __MEMORYD_DATA_ROOT__</plist>")
    launch_dir = tmp_path / "LaunchAgents"
    launch_dir.mkdir()

    install_launchd_mirror(
        template_path=template_src,
        launch_dir=launch_dir,
        memoryd_bin="/path/to/bin",
        data_root="/path/to/data",
    )
    out = launch_dir / "com.memoryd.mirror.plist"
    assert out.exists()
    txt = out.read_text()
    assert "/path/to/bin" in txt
    assert "/path/to/data" in txt
    assert "__MEMORYD_BIN__" not in txt
