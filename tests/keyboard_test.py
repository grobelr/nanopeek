#!/usr/bin/env python3
"""Exhaustive keyboard + save-flow tests for nanopeek.

Part A: every keybinding routes to the right action (editor and preview
        focus), plus functional checks for cursor/edit keys, pane
        switching, dialogs, and the display modes.
Part B: every save / no-save / exit path, verified against the real
        filesystem (content AND mtime, so a silent rewrite cannot pass).

Run:  python3 tests/keyboard_test.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from textual.filter import NoColor
from textual.widgets import Input, TextArea

from nanopeek import (
    COLOR_MODES,
    ConfirmExitScreen,
    NanoPeek,
    SaveScreen,
    SearchScreen,
    THEME_CYCLE,
)

PASS = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"ok: {msg}")


def snap(path: Path) -> tuple[bytes, int]:
    st = path.stat()
    return path.read_bytes(), st.st_mtime_ns


# --------------------------------------------------------------------------
# Part A — keybinding routing
# --------------------------------------------------------------------------

KEY2ACTION = {
    "ctrl+x": "exit", "ctrl+s": "save", "ctrl+o": "write_out",
    "ctrl+w": "where_is", "ctrl+k": "cut_line", "ctrl+u": "uncut",
    "ctrl+p": "toggle_preview", "ctrl+y": "page_up", "ctrl+v": "page_down",
    "ctrl+g": "help",
    "alt+t": "cycle_theme", "alt+d": "toggle_dark",
    "alt+c": "cycle_color", "alt+a": "toggle_ascii",
}
# ^A/^E are owned by TextArea's own "home,ctrl+a" / "end,ctrl+e" bindings,
# whose actions are exactly nano's line-start/line-end semantics; they are
# verified functionally in part_a_functional instead of via the app spies.


async def part_a_routing() -> None:
    for focus_preview in (False, True):
        with tempfile.TemporaryDirectory() as tmp:
            app = NanoPeek(Path(tmp) / "n.md")
            fired: list[str] = []
            for name in set(KEY2ACTION.values()):
                setattr(app, f"action_{name}", (lambda n: lambda: fired.append(n))(name))
            async with app.run_test(size=(110, 32)) as pilot:
                await pilot.pause(0.2)
                if focus_preview:
                    app.query_one("#preview").focus()
                await pilot.pause(0.1)
                bad = []
                for key, want in KEY2ACTION.items():
                    fired.clear()
                    await pilot.press(key)
                    await pilot.pause(0.05)
                    if want not in fired:
                        bad.append(f"{key}!={want}")
                assert not bad, f"focus_preview={focus_preview}: {bad}"
                ok(f"all {len(KEY2ACTION)} bindings fire with "
                   f"{'preview' if focus_preview else 'editor'} focused")


async def part_a_functional() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = NanoPeek(Path(tmp) / "n.md")
        async with app.run_test(size=(110, 32)) as pilot:
            ed = app.query_one("#editor", TextArea)
            await pilot.pause(0.2)

            # typing + basic edit keys
            await pilot.press(*"hello world")
            assert ed.text.startswith("hello world")
            await pilot.press("backspace")
            assert ed.text.startswith("hello worl")
            await pilot.press("ctrl+d")           # delete char right (nano ^D)
            assert ed.text.startswith("hello worl")
            await pilot.press("enter")
            assert ed.text.count("\n") >= 1
            ok("typing, backspace, ^D delete, enter")

            # cursor movement keys
            await pilot.press("home")
            assert ed.cursor_location[1] == 0
            await pilot.press("up")            # back to the "hello worl" line
            await pilot.press("end")
            assert ed.cursor_location[1] == len("hello worl")
            await pilot.press("left", "down")
            await pilot.press("pageup", "pagedown")
            ok("home/end/arrows/pageup/pagedown move cursor")

            # ^A / ^E (nano line beg/end)
            ed.move_cursor((0, 3))
            await pilot.pause(0.05)
            await pilot.press("ctrl+a")
            assert ed.cursor_location == (0, 0)
            await pilot.press("ctrl+e")
            assert ed.cursor_location[1] == len(ed.text.split("\n")[0])
            ok("^A/^E line start/end")

            # undo (^Z) restores deleted char
            before = ed.text
            await pilot.press("end", "backspace")
            assert ed.text != before
            await pilot.press("ctrl+z")
            assert ed.text == before
            ok("^Z undo restores text")

            # Tab switches panes, Shift+Tab back
            await pilot.press("tab")
            assert app.query_one("#preview").has_focus, "Tab did not focus preview"
            await pilot.press("shift+tab")
            assert ed.has_focus, "Shift+Tab did not focus editor"
            ok("Tab / Shift+Tab switch panes")

            # mouse/keyboard selection auto-copies to the clipboard
            copied: list[str] = []
            orig_copy = app.copy_to_clipboard
            app.copy_to_clipboard = copied.append
            ed.selection = ((0, 0), (0, 5))   # "hello" (drag grows it)
            await pilot.pause(0.05)
            ed.selection = ((0, 0), (0, 10))
            await pilot.pause(0.05)
            assert copied[-1] == "hello worl", copied[-3:]
            assert len(copied) >= 2, "each selection change should copy"
            n = len(copied)
            ed.selection = ((0, 3), (0, 3))   # collapsed: nothing new copied
            await pilot.pause(0.05)
            assert len(copied) == n
            app.copy_to_clipboard = orig_copy
            ok("selection changes auto-copy to clipboard")

            # dialogs: ^G help opens and closes
            await pilot.press("ctrl+g")
            await pilot.pause(0.1)
            assert len(app.screen_stack) == 2, "^G did not open help"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert len(app.screen_stack) == 1, "enter did not close help"
            ok("^G help opens / enter closes")

            # command palette relocated to ctrl+shift+p
            await pilot.press("ctrl+shift+p")
            await pilot.pause(0.2)
            assert len(app.screen_stack) == 2, "ctrl+shift+p did not open palette"
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert len(app.screen_stack) == 1, "escape did not close palette"
            ok("ctrl+shift+p opens palette, escape closes")

            # search dialog: enter jumps, ctrl+w/escape close (no stacking)
            await pilot.press("ctrl+w")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SearchScreen)
            await pilot.press("ctrl+w")   # hardened: closes instead of stacking
            await pilot.pause(0.1)
            assert len(app.screen_stack) == 1, "^W inside search stacked a dialog"
            await pilot.press("ctrl+w")
            await pilot.pause(0.1)
            app.screen.query_one("#search-input", Input).value = "hello"
            await pilot.press("enter")
            await pilot.pause(0.1)
            row = ed.cursor_location[0]
            assert "hello" in ed.text.split("\n")[row]
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert len(app.screen_stack) == 1
            ok("^W search: ctrl+w closes, enter jumps, escape closes")

    # display modes (real actions, separate app)
    with tempfile.TemporaryDirectory() as tmp:
        app = NanoPeek(Path(tmp) / "n.md")
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.2)
            assert app.theme == "textual-dark"
            await pilot.press("alt+t")
            await pilot.pause(0.05)
            assert app.theme == THEME_CYCLE[1] == "textual-light", app.theme
            await pilot.press("alt+d")
            await pilot.pause(0.05)
            assert app.theme == "textual-dark"
            ok("alt+t cycles theme, alt+d toggles dark/light")

            await pilot.press("alt+c")   # truecolor -> ansi
            await pilot.pause(0.1)
            assert app.color_mode == "ansi" and app.ansi_color is True
            assert app.theme in ("ansi-dark", "ansi-light")
            await pilot.press("alt+c")   # ansi -> mono
            await pilot.pause(0.1)
            assert app.color_mode == "mono"
            assert any(isinstance(f, NoColor) for f in app._filters)
            await pilot.press("alt+c")   # mono -> truecolor (theme restored)
            await pilot.pause(0.1)
            assert app.color_mode == "truecolor" and app.ansi_color is False
            assert app.theme == "textual-dark", app.theme
            assert not any(isinstance(f, NoColor) for f in app._filters)
            ok(f"alt+c cycles {COLOR_MODES} and restores the theme")

            await pilot.press("alt+a")
            await pilot.pause(0.05)
            assert app.ascii_mode and getattr(app._driver, "_plain_write", None)
            await pilot.press("alt+a")
            await pilot.pause(0.05)
            assert not app.ascii_mode and getattr(app._driver, "_plain_write", None) is None
            ok("alt+a toggles ascii mode (driver wrapper on/off)")

            # ascii mode must not affect the buffer contents
            await pilot.press("alt+a")
            await pilot.pause(0.05)
            app.query_one("#editor", TextArea).text = "caf\u00e9 — \u2026 \u2713"
            await pilot.pause(0.05)
            assert "caf\u00e9" in app.query_one("#editor", TextArea).text
            ok("ascii mode leaves buffer contents untouched")


# --------------------------------------------------------------------------
# Part B — save / exit flows
# --------------------------------------------------------------------------

async def scenario(name: str, *, content: str | None, with_path: bool,
                   keys_and_dialogs, tmp: str, expect_running: bool,
                   expect_file: str | None, filename: str | None = None):
    """Run one exit/save scenario; keys_and_dialogs drives the app."""
    path = Path(tmp) / (filename or f"{name}.md")
    if content is not None:
        path.write_text(content, encoding="utf-8")
    before = snap(path) if path.exists() else None

    app = NanoPeek(path if with_path else None)
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause(0.2)
        await keys_and_dialogs(app, pilot)
        await pilot.pause(0.4)
        if expect_running:
            assert app.is_running, f"{name}: app should still be running"
        else:
            assert not app.is_running, f"{name}: app should have exited"
    if expect_file is None:
        if before is None:
            assert not path.exists(), f"{name}: file must not have been created"
        else:
            assert snap(path) == before, f"{name}: file must be untouched"
    else:
        assert path.read_text(encoding="utf-8") == expect_file, \
            f"{name}: file content wrong"
    ok(name)


async def type_text(app, pilot, text: str) -> None:
    ed = app.query_one("#editor", TextArea)
    lines = ed.text.split("\n")
    ed.move_cursor((len(lines) - 1, len(lines[-1])))  # append at end, like typing
    ed.insert(text)
    await pilot.pause(0.1)
    assert app.dirty


async def part_b() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = "# original\n"

        async def s1(app, pilot):
            await pilot.press("ctrl+x")
        await scenario("1 clean ^X exits, file untouched", content=base,
                       with_path=True, keys_and_dialogs=s1, tmp=tmp,
                       expect_running=False, expect_file=None)

        async def s2(app, pilot):
            await type_text(app, pilot, "added\n")
            await pilot.press("ctrl+x")
            await pilot.pause(0.2)
            assert isinstance(app.screen, ConfirmExitScreen)
            await pilot.press("y")            # Yes = save
        await scenario("2 dirty ^X Yes -> saved and exited", content=base,
                       with_path=True, keys_and_dialogs=s2, tmp=tmp,
                       expect_running=False, expect_file=base + "added\n")

        async def s3(app, pilot):
            await type_text(app, pilot, "added\n")
            await pilot.press("ctrl+x")
            await pilot.pause(0.2)
            await pilot.press("n")            # No = discard
        await scenario("3 dirty ^X No -> exits, file untouched", content=base,
                       with_path=True, keys_and_dialogs=s3, tmp=tmp,
                       expect_running=False, expect_file=None)

        async def s4(app, pilot):
            await type_text(app, pilot, "added\n")
            await pilot.press("ctrl+x")
            await pilot.pause(0.2)
            await pilot.press("escape")       # Cancel
        await scenario("4 dirty ^X Esc -> stays, file untouched", content=base,
                       with_path=True, keys_and_dialogs=s4, tmp=tmp,
                       expect_running=True, expect_file=None)

        async def s5(app, pilot):
            await type_text(app, pilot, "added\n")
            await pilot.press("ctrl+c")       # safe exit too
            await pilot.pause(0.2)
            assert isinstance(app.screen, ConfirmExitScreen)
            app.screen.query_one("#cancel").press()
        await scenario("5 dirty ^C Cancel button -> stays", content=base,
                       with_path=True, keys_and_dialogs=s5, tmp=tmp,
                       expect_running=True, expect_file=None)

        async def s6(app, pilot):
            await type_text(app, pilot, "one\n")
            await pilot.press("ctrl+s")
            await pilot.pause(0.2)
            assert not app.dirty
            await type_text(app, pilot, "two\n")
            await pilot.press("ctrl+s")
            await pilot.pause(0.2)
        await scenario("6 ^S saves (twice), content correct", content=base,
                       with_path=True, keys_and_dialogs=s6, tmp=tmp,
                       expect_running=True, expect_file=base + "one\ntwo\n")

        async def s7(app, pilot):
            await type_text(app, pilot, "draft\n")
            await pilot.press("ctrl+s")       # no path yet -> prompt
            await pilot.pause(0.2)
            assert isinstance(app.screen, SaveScreen)
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert not (Path(tmp) / "7.md").exists()
            assert app.dirty
        await scenario("7 ^S new buffer, Esc -> no file written", content=None,
                       with_path=False, keys_and_dialogs=s7, tmp=tmp,
                       expect_running=True, expect_file=None)

        async def s8(app, pilot):
            await type_text(app, pilot, "saved\n")
            await pilot.press("ctrl+s")
            await pilot.pause(0.2)
            app.screen.query_one("#save-input", Input).value = str(Path(tmp) / "8.md")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert not app.dirty and app.path == Path(tmp) / "8.md"
            await type_text(app, pilot, "more\n")
            await pilot.press("ctrl+s")       # now saves directly, no prompt
            await pilot.pause(0.2)
            assert not app.dirty
        await scenario("8 ^S new buffer, name -> saved; path adopted", content=None,
                       with_path=False, keys_and_dialogs=s8, tmp=tmp,
                       expect_running=True, expect_file="saved\nmore\n",
                       filename="8.md")
        assert (Path(tmp) / "8.md").read_text() == "saved\nmore\n"
        ok("8b adopted path used by later ^S")

        async def s9(app, pilot):
            await type_text(app, pilot, "out\n")
            await pilot.press("ctrl+o")
            await pilot.pause(0.2)
            assert isinstance(app.screen, SaveScreen)
            assert app.screen.query_one("#save-input", Input).value == str(app.path)
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert not app.dirty
        await scenario("9 ^O prefills current path and saves", content=base,
                       with_path=True, keys_and_dialogs=s9, tmp=tmp,
                       expect_running=True, expect_file=base + "out\n")

        async def s10(app, pilot):
            await type_text(app, pilot, "newname\n")
            await pilot.press("ctrl+o")
            await pilot.pause(0.2)
            app.screen.query_one("#save-input", Input).value = str(Path(tmp) / "10-new.md")
            await pilot.press("enter")
            await pilot.pause(0.2)
            await type_text(app, pilot, "after\n")
            await pilot.press("ctrl+s")
            await pilot.pause(0.2)
        await scenario("10 ^O to new name; later ^S writes there", content=base,
                       with_path=True, keys_and_dialogs=s10, tmp=tmp,
                       expect_running=True, expect_file=None, filename="10.md")
        assert (Path(tmp) / "10-new.md").read_text() == base + "newname\nafter\n"
        assert (Path(tmp) / "10.md").read_text() == base
        ok("10b old file untouched, ^S follows the new path")

        # 11: save into an unwritable directory -> error, stay dirty, keep running
        locked = Path(tmp) / "locked"
        locked.mkdir()
        os.chmod(locked, 0o500)
        try:
            async def s11(app, pilot):
                await type_text(app, pilot, "x\n")
                await pilot.press("ctrl+s")
                await pilot.pause(0.2)
                assert isinstance(app.screen, SaveScreen)
                app.screen.query_one("#save-input", Input).value = str(locked / "nope.md")
                await pilot.press("enter")
                await pilot.pause(0.3)
                assert app.dirty, "failed save must keep dirty flag"
                assert app.path is None, "failed save must not adopt the path"
            await scenario("11 ^S to unwritable dir -> error, no adopt", content=None,
                           with_path=False, keys_and_dialogs=s11, tmp=tmp,
                           expect_running=True, expect_file=None)
        finally:
            os.chmod(locked, 0o700)

        # 12: ^X -> Yes but the file is read-only -> must NOT exit (no data loss)
        target = Path(tmp) / "12-readonly.md"
        target.write_text("orig\n")
        os.chmod(target, 0o400)
        try:
            app = NanoPeek(target)
            async with app.run_test(size=(110, 32)) as pilot:
                await pilot.pause(0.2)
                await type_text(app, pilot, "important\n")
                await pilot.press("ctrl+x")
                await pilot.pause(0.2)
                await pilot.press("y")
                await pilot.pause(0.4)
                assert app.is_running, "must not exit when save failed"
                assert app.dirty
            assert target.read_text() == "orig\n"
            ok("12 ^X Yes with failing save -> stays running, file intact")
        finally:
            os.chmod(target, 0o600)

        async def s13(app, pilot):
            await type_text(app, pilot, "draft\n")
            await pilot.press("ctrl+x")
            await pilot.pause(0.2)
            await pilot.press("y")            # Yes, no path -> save-as prompt
            await pilot.pause(0.2)
            assert isinstance(app.screen, SaveScreen)
            await pilot.press("escape")       # cancel chained exit
            await pilot.pause(0.2)
        await scenario("13 ^X Yes, cancel save-as -> stays running", content=None,
                       with_path=False, keys_and_dialogs=s13, tmp=tmp,
                       expect_running=True, expect_file=None)

        async def s14(app, pilot):
            await type_text(app, pilot, "final\n")
            await pilot.press("ctrl+x")
            await pilot.pause(0.2)
            await pilot.press("y")
            await pilot.pause(0.2)
            app.screen.query_one("#save-input", Input).value = str(Path(tmp) / "14.md")
            await pilot.press("enter")
            await pilot.pause(0.3)
        await scenario("14 ^X Yes, save-as name -> saved and exited", content=None,
                       with_path=False, keys_and_dialogs=s14, tmp=tmp,
                       expect_running=False, expect_file="final\n", filename="14.md")

        async def s15(app, pilot):
            await pilot.press("ctrl+o")
            await pilot.pause(0.2)
            await pilot.press("ctrl+s")       # hardened: closes, no stacking
            await pilot.pause(0.15)
            assert len(app.screen_stack) == 1, "^S stacked on save prompt"
        await scenario("15 ^S inside save prompt closes it (no stack)", content=base,
                       with_path=True, keys_and_dialogs=s15, tmp=tmp,
                       expect_running=True, expect_file=None)

        # 16: new buffer, clean, ^X exits immediately
        app = NanoPeek(None)
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.2)
            assert not app.dirty
            await pilot.press("ctrl+x")
            await pilot.pause(0.4)
            assert not app.is_running
        ok("16 clean New Buffer ^X exits immediately")


async def main() -> None:
    await part_a_routing()
    await part_a_functional()
    await part_b()
    print(f"\nALL KEYBOARD & SAVE-FLOW TESTS PASSED ({PASS} checks)")


if __name__ == "__main__":
    asyncio.run(main())
