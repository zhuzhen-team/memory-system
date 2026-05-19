"""Textual digest TUI + thin reducer functions.

The reducer layer (``list_pending`` / ``approve_all_pending`` / ``reject_one``)
is independent of the ``textual`` library and is what unit tests exercise.
``run_tui`` is a thin wrapper that wires the reducers into a Textual ``App``;
it is exercised manually (real terminal) because Textual needs a tty and we
do not want stdin/stdout fighting with pytest.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Reducer functions (no textual dependency; unit-testable).
# ---------------------------------------------------------------------------


def list_pending(data_root: Path) -> list[dict]:
    """Thin wrapper around governance.analyze.list_pending_promotions."""
    from memoryd.governance.analyze import list_pending_promotions
    return list_pending_promotions(data_root)


def approve_all_pending(
    data_root: Path,
    *,
    approve_fn: Callable[[Path, int], None] | None = None,
    list_fn: Callable[[Path], list[dict]] | None = None,
) -> list[int]:
    """Approve every pending promotion; return the list of approved ids.

    ``approve_fn`` / ``list_fn`` are injectable so tests can run the loop
    without touching SQLite.
    """
    if approve_fn is None:
        from memoryd.governance.analyze import approve_promotion
        approve_fn = approve_promotion
    if list_fn is None:
        list_fn = list_pending
    approved: list[int] = []
    for item in list_fn(data_root):
        approve_fn(data_root, item["id"])
        approved.append(item["id"])
    return approved


def reject_one(data_root: Path, promotion_id: int) -> None:
    """Reject a single promotion (delegates to governance.analyze)."""
    from memoryd.governance.analyze import reject_promotion
    reject_promotion(data_root, promotion_id)


# ---------------------------------------------------------------------------
# Textual App (thin wrapper over reducers).
# ---------------------------------------------------------------------------


def run_tui(data_root: Path) -> int:
    """Launch the digest Textual App. Blocks until the user quits."""
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.widgets import Footer, Header, Label, ListItem, ListView

    class DigestApp(App):
        BINDINGS = [
            Binding("a", "approve_all", "Approve all"),
            Binding("r", "reject", "Reject current"),
            Binding("s", "skip", "Skip"),
            Binding("q", "quit", "Quit"),
        ]
        CSS = """
        Screen { layout: vertical; }
        ListView { height: 1fr; }
        """

        def __init__(self, data_root_: Path, **kw):
            super().__init__(**kw)
            self.data_root_ = data_root_

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield ListView(id="promotions")
            yield Footer()

        def on_mount(self) -> None:
            self._refresh()

        def _refresh(self) -> None:
            lv = self.query_one("#promotions", ListView)
            lv.clear()
            for it in list_pending(self.data_root_):
                lv.append(
                    ListItem(
                        Label(
                            f"#{it['id']} [{it['proposed_type']}] "
                            f"{it['proposed_title']}"
                        )
                    )
                )

        def action_approve_all(self) -> None:
            approve_all_pending(self.data_root_)
            self._refresh()

        def action_reject(self) -> None:
            lv = self.query_one("#promotions", ListView)
            if lv.highlighted_child is None:
                self.bell()
                return
            label = lv.highlighted_child.query_one(Label).renderable
            text = str(label)
            try:
                pid = int(text.split()[0].lstrip("#"))
            except Exception:
                self.bell()
                return
            try:
                reject_one(self.data_root_, pid)
            except Exception:
                self.bell()
            self._refresh()

        def action_skip(self) -> None:
            # Textual's ListView already handles j/k + arrow cursor movement;
            # this action is reserved for an explicit "skip and remember" UX.
            pass

    DigestApp(data_root_=data_root).run()
    return 0
