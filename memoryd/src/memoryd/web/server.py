"""memoryd web CLI entry: bootstrap port/token, run uvicorn."""
from __future__ import annotations

import os
import secrets
import socket
import sys
import webbrowser
from pathlib import Path


def pick_free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def gen_token() -> str:
    return secrets.token_urlsafe(32)


def run(port: int | None = None, open_browser: bool = True) -> int:
    """Start uvicorn with random port + token; blocks until SIGINT."""
    import uvicorn
    from . import create_app
    p = port or pick_free_port()
    token = gen_token()
    data_root = Path(
        os.environ.get(
            "MEMORYD_DATA_ROOT",
            str(Path.home() / ".local" / "share" / "memoryd"),
        )
    )
    app = create_app(token=token, data_root=data_root)
    url = f"http://127.0.0.1:{p}/?token={token}"
    print(f"memoryd web on {url}", file=sys.stderr, flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(app, host="127.0.0.1", port=p, log_level="warning")
    return 0
