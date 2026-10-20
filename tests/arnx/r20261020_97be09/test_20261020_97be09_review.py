import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from textual.widgets import Static
from nanopeek import NanoPeek, _strip_jsonc


async def test_s2_cancel_saveas_from_exit_resumes():
    app = NanoPeek(None)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.2)
        app.editor.text = "# hi\n"
        await pilot.pause(0.3)
        await pilot.press("ctrl+x")
        await pilot.pause(0.2)
        await pilot.press("y")
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert app.is_running
        assert app._exiting is False


async def test_s2_preview_updates_after_cancel():
    app = NanoPeek(None)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.2)
        app.editor.text = "# hi\n"
        await pilot.pause(0.3)
        await pilot.press("ctrl+x")
        await pilot.pause(0.2)
        await pilot.press("y")
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.3)
        app.editor.text = "# changed\n"
        await pilot.pause(0.6)
        assert len(app.preview.children) > 0


async def test_s1_deep_json_does_not_kill_app(tmp_path):
    p = tmp_path / "d.json"
    p.write_text("[" * 2000 + "0" + "]" * 2000)
    app = NanoPeek(p)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.8)
        assert app.is_running


def test_s3_no_pep604_in_class_bases():
    import re
    src = (Path(__file__).resolve().parent.parent.parent.parent / "nanopeek.py").read_text()
    assert not re.search(r"class \w+\(ModalScreen\[[^\]]*\|", src)


def test_s4_unterminated_block_comment_is_error():
    import json
    out = _strip_jsonc("{} /* unfinished")
    try:
        json.loads(out)
        ok = True
    except Exception:
        ok = False
    assert not ok


async def test_s4_jsonc_unterminated_shows_error(tmp_path):
    p = tmp_path / "a.jsonc"
    p.write_text("{} /* unfinished comment")
    app = NanoPeek(p)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.6)
        assert app.query_one("#preview-pane").has_class("json-error")


async def test_s5_json_error_reports_original_line(tmp_path):
    p = tmp_path / "e.json"
    p.write_text('{\n "a": 1, // c\n oops\n}\n')
    app = NanoPeek(p)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.6)
        err = str(app.query_one("#preview-error", Static).content)
        assert "line 3" in err


async def test_s7_single_heading_long_doc_follows_cursor(tmp_path):
    p = tmp_path / "l.md"
    p.write_text("# Title\n\n" + "\n\n".join(f"Paragraph {i} text." for i in range(300)))
    app = NanoPeek(p)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(1.0)
        last = app.editor.text.count("\n")
        app.editor.move_cursor((last, 0))
        await pilot.pause(0.8)
        assert app.preview.max_scroll_y > 0
        assert app.preview.scroll_y > app.preview.max_scroll_y * 0.5


async def test_s8_single_match_under_cursor_no_wrap_notice():
    app = NanoPeek(None)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.2)
        app.editor.text = "xxx needle yyy"
        await pilot.pause(0.1)
        app.editor.move_cursor((0, 4))
        notes = []
        app.notify = lambda m, **k: notes.append(m)
        app.find_and_jump("needle")
        assert not any("wrapped" in n for n in notes)


async def test_s12_adjacent_matches_visited():
    app = NanoPeek(None)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.2)
        app.editor.text = "foofoo"
        await pilot.pause(0.1)
        app.editor.move_cursor((0, 0))
        seen = []
        for _ in range(3):
            app.find_and_jump("foo")
            seen.append(app.editor.selection.start)
        assert (0, 3) in seen or (0, 3) in [tuple(s) for s in seen]
