#!/usr/bin/env python3
"""Headless smoke test for nanopeek using Textual's run_test harness.

Run:  python3 tests/smoke_test.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from textual.widgets import Markdown, TextArea

from nanopeek import ConfirmExitScreen, NanoPeek, SaveScreen

REPO = Path(__file__).resolve().parent.parent
SAMPLE = (REPO / "sample.md").read_text()


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "note.md"
        path.write_text(SAMPLE, encoding="utf-8")

        app = NanoPeek(path)
        async with app.run_test(size=(120, 34)) as pilot:
            editor = app.query_one("#editor", TextArea)
            preview = app.query_one("#preview", Markdown)

            # 1. initial render: preview should have rendered Markdown blocks
            await pilot.pause(0.8)
            assert preview.children, "preview rendered no Markdown blocks"
            rendered = " ".join(
                getattr(w, "renderable", "") and str(getattr(w, "renderable", ""))
                for w in preview.query("Static")
            )
            assert "nanopeek demo" in rendered or len(preview.children) > 3, (
                f"preview content unexpected: {len(preview.children)} children"
            )
            print(f"ok: initial preview rendered ({len(preview.children)} blocks)")

            # 2. typing marks dirty and refreshes the preview
            await pilot.press("A", "\n")
            await pilot.pause(0.8)
            assert app.dirty, "typing did not mark buffer dirty"
            assert editor.text.startswith("A"), "keystroke not inserted"
            print("ok: typing marks buffer dirty")

            # 3. save writes the file
            app.action_save()
            await pilot.pause(0.2)
            assert not app.dirty, "save did not clear dirty flag"
            assert path.read_text(encoding="utf-8") == editor.text, "file != buffer"
            print("ok: save writes file")

            # 4. ^P cycles split -> full-screen preview -> editor only -> split
            app.action_toggle_preview()
            await pilot.pause(0.3)
            assert app.layout_mode == "preview" and app.preview_visible
            assert app.query_one("#main").has_class("preview-only")
            assert app.query_one("#preview").has_focus
            app.action_toggle_preview()
            await pilot.pause(0.2)
            assert app.layout_mode == "edit" and not app.preview_visible
            assert app.query_one("#main").has_class("no-preview")
            assert app.query_one("#editor").has_focus
            app.action_toggle_preview()
            await pilot.pause(0.8)
            assert app.layout_mode == "split"
            main_classes = app.query_one("#main")
            assert not main_classes.has_class("preview-only")
            assert not main_classes.has_class("no-preview")
            print("ok: ^P cycles split / full preview / editor only")

            # 5. cut line / uncut
            text_before = editor.text
            app.action_cut_line()
            await pilot.pause(0.1)
            assert editor.text != text_before, "^K did not cut"
            app.action_uncut()
            await pilot.pause(0.1)
            assert editor.text == text_before, "^U did not restore"
            print("ok: cut/uncut round-trip")

            # 6. search
            app.find_and_jump("Block quotes")
            await pilot.pause(0.1)
            row, col = editor.cursor_location
            line = editor.text.split("\n")[row]
            assert "Block quotes" in line, f"search landed wrong: {line!r}"
            print("ok: where-is jumps to match")

            # 7. exit with dirty buffer asks first (real Ctrl+X keypress)
            editor.text += "\nmore"
            await pilot.pause(0.1)
            await pilot.press("ctrl+x")
            await pilot.pause(0.3)
            assert any(isinstance(s, ConfirmExitScreen) for s in app.screen_stack), (
                "ctrl+x did not ask for confirmation on a dirty buffer"
            )
            app.screen.query_one("#no").press()
            await pilot.pause(0.3)
            assert not app.is_running, "choosing 'No' did not exit the app"
            print("ok: ctrl+x asks to save, then exits")

            # 8. new buffer save-as prompt exists and cancels cleanly
            app2 = NanoPeek(None)
            async with app2.run_test(size=(120, 34)) as pilot2:
                await pilot2.press("ctrl+o")
                await pilot2.pause(0.2)
                assert any(isinstance(s, SaveScreen) for s in app2.screen_stack), (
                    "ctrl+o did not open the write-out prompt"
                )
                app2.screen.query_one("#save-input").value = str(Path(tmp) / "new.md")
                await pilot2.press("enter")
                await pilot2.pause(0.2)
                assert (Path(tmp) / "new.md").exists(), "save-as did not write file"
                print("ok: write-out to new file")

                # 9. Exercise export without overwriting public documentation.
                svg = app2.export_screenshot()
                out = Path(tmp) / "screenshot.svg"
                out.parent.mkdir(exist_ok=True)
                out.write_text(svg, encoding="utf-8")
                print(f"ok: screenshot written to {out}")

            # 10. scroll sync: section-anchored for .md, proportional otherwise
            tall = SAMPLE + "\n\n" + "\n\n".join(
                f"## section {i:02d}\n\n" + "\n\n".join(
                    f"Filler paragraph {i:02d}.{j} — plenty of text so that both the "
                    f"editor and the preview overflow the viewport at test size."
                    for j in range(5)
                )
                for i in range(20)
            )
            tall_path = Path(tmp) / "tall.md"
            tall_path.write_text(tall, encoding="utf-8")
            app3 = NanoPeek(tall_path)
            async with app3.run_test(size=(120, 34)) as pilot3:
                editor3 = app3.query_one("#editor", TextArea)
                preview3 = app3.query_one("#preview", Markdown)
                for _ in range(16):  # render + heading matching settle
                    if app3._heading_pairs:
                        break
                    await pilot3.pause(0.25)
                assert editor3.max_scroll_y > 0, "editor does not overflow at test size"
                assert preview3.max_scroll_y > 0, "preview does not overflow at test size"
                assert len(app3._heading_pairs) >= 15, len(app3._heading_pairs)

                # cursor inside section 14 -> its heading anchors at the top
                sec_lines = [
                    tall[: pos].count("\n") for pos in
                    [tall.find(f"## section {i:02d}") for i in range(20)]
                ]
                target_src = sec_lines[14] + 3
                editor3.move_cursor((target_src, 0))
                editor3.scroll_to(y=editor3.max_scroll_y / 2, animate=False)
                await pilot3.pause(0.6)
                widget14 = dict(app3._heading_pairs)[sec_lines[14]]
                offset = app3._virtual_y(widget14) - preview3.scroll_y
                assert -20 <= offset <= 10, f"section 14 anchor off: {offset:.1f}"
                print(f"ok: editor cursor anchors preview section (offset {offset:.0f}px)")

                # bidirectional: scrolling the preview (e.g. mouse wheel)
                # moves the editor along — no focus guard, always together
                preview3.focus()
                await pilot3.pause(0.3)
                preview3.scroll_to(y=preview3.max_scroll_y / 2, animate=False)
                await pilot3.pause(0.6)
                expected_ed = (
                    preview3.scroll_offset.y / preview3.max_scroll_y * editor3.max_scroll_y
                )
                assert abs(editor3.scroll_y - expected_ed) <= max(6, editor3.max_scroll_y * 0.05), (
                    f"editor did not follow preview scroll: "
                    f"{editor3.scroll_y:.1f} != {expected_ed:.1f}"
                )
                print(f"ok: editor follows preview scroll (y={editor3.scroll_y:.1f})")

                # mouse wheel over the preview keeps both panes together
                from textual import events as _events

                region = preview3.region
                wx, wy = int(region.x + region.width // 2), int(region.y + region.height // 2)
                for _ in range(5):
                    ev = _events.MouseScrollDown(
                        widget=preview3, x=wx, y=wy, delta_x=0, delta_y=1,
                        button=0, shift=False, meta=False, ctrl=False,
                    )
                    pilot3.app.screen._forward_event(ev)
                    await pilot3.pause(0.02)
                await pilot3.pause(0.3)
                assert preview3.scroll_y > expected_ed - 5
                ratio_ed = editor3.scroll_y / editor3.max_scroll_y
                ratio_pv = preview3.scroll_y / preview3.max_scroll_y
                assert abs(ratio_ed - ratio_pv) <= 0.08, (
                    f"panes drifted after wheel: editor {ratio_ed:.2f} vs preview {ratio_pv:.2f}"
                )
                print("ok: mouse wheel on preview keeps panes locked together")

            # 10b. heading-less documents fall back to proportional sync
            plain = "\n\n".join(
                f"Plain paragraph {i:02d} with no headings at all, so the "
                f"anchor map is empty and sync stays proportional."
                for i in range(80)
            )
            plain_path = Path(tmp) / "plain.md"
            plain_path.write_text(plain, encoding="utf-8")
            app4 = NanoPeek(plain_path)
            async with app4.run_test(size=(120, 34)) as pilot4:
                editor4 = app4.query_one("#editor", TextArea)
                preview4 = app4.query_one("#preview", Markdown)
                await pilot4.pause(0.8)
                assert app4._heading_pairs == []
                editor4.scroll_to(y=editor4.max_scroll_y / 2, animate=False)
                await pilot4.pause(0.6)
                expected = (
                    editor4.scroll_offset.y / editor4.max_scroll_y * preview4.max_scroll_y
                )
                assert abs(preview4.scroll_y - expected) <= 3, (
                    f"proportional fallback broken: {preview4.scroll_y:.1f} != {expected:.1f}"
                )
                print("ok: heading-less docs use proportional fallback")

    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
