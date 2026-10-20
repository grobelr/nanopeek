"""Cancelling the save-as prompt opened from the exit flow must restore
normal editing with a live preview."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

from textual.widgets import TextArea  # noqa: E402

from nanopeek import NanoPeek, SaveScreen  # noqa: E402


async def _scenario() -> dict:
    app = NanoPeek(None)
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause(0.2)
        editor = app.query_one("#editor", TextArea)
        editor.insert("# draft\n")
        await pilot.pause(0.1)
        await pilot.press("ctrl+x")  # dirty buffer -> save/discard prompt
        await pilot.pause(0.2)
        await pilot.press("y")       # save -> no path -> save-as prompt
        await pilot.pause(0.2)
        assert isinstance(app.screen, SaveScreen), "expected save-as prompt"
        await pilot.press("escape")  # cancel the save-as prompt
        await pilot.pause(0.3)

        state = {
            "running": app.is_running,
            "exiting": app._exiting,
            "layout": app.layout_mode,
        }
        # Normal editing again: typing must schedule a live preview refresh.
        editor.insert("more words\n")
        await pilot.pause(0.1)
        state["refresh_scheduled"] = app._refresh_timer is not None
        await pilot.pause(0.5)
        state["rendered"] = "more words" in app.editor.text and not app._exiting
        await pilot.press("ctrl+x")
        await pilot.pause(0.1)
        return state


def test_cancel_save_as_from_exit_restores_editing_and_preview() -> None:
    state = asyncio.run(_scenario())
    assert state["running"], "app must keep running after cancelling save-as"
    assert state["exiting"] is False, "exit shutdown state must be cleared on cancel"
    assert state["refresh_scheduled"], "typing must schedule a live preview refresh"
    assert state["rendered"]
