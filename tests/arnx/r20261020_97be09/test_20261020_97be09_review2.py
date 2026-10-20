import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from nanopeek import NanoPeek


def test_s6_last_section_interpolation_uses_own_length():
    app = NanoPeek(None)
    w0 = SimpleNamespace(y=0, region=SimpleNamespace(height=1))
    w1 = SimpleNamespace(y=20, region=SimpleNamespace(height=1))
    app._heading_pairs = [(0, w0), (10, w1)]
    app._heading_src = [(0, 1), (10, 1)]
    app._virtual_y = lambda w: w.y
    at_end = app._anchor_preview_y(100)
    mid = app._anchor_preview_y(50)
    # a cursor mid-way through a long final section must not be pinned
    # to the same position as one at its very end
    assert mid < at_end


async def _cancel_exit_save(pilot, app):
    await pilot.pause(0.2)
    app.editor.text = "# hi\n"
    await pilot.pause(0.3)
    await pilot.press("ctrl+x")
    await pilot.pause(0.2)
    await pilot.press("y")
    await pilot.pause(0.2)
    await pilot.press("escape")
    await pilot.pause(0.3)


async def test_s9_edits_refresh_after_cancelled_exit():
    app = NanoPeek(None)
    async with app.run_test(size=(100, 30)) as pilot:
        await _cancel_exit_save(pilot, app)
        app.editor.text = "# second\n"
        await pilot.pause(0.6)
        assert app._last_preview_key is not None
        assert app._last_preview_key[1] == "# second\n"


async def test_s10_not_in_exiting_state_after_cancel():
    app = NanoPeek(None)
    async with app.run_test(size=(100, 30)) as pilot:
        await _cancel_exit_save(pilot, app)
        assert not app._exiting


async def test_s11_preview_rebuilt_after_cancel():
    app = NanoPeek(None)
    async with app.run_test(size=(100, 30)) as pilot:
        await _cancel_exit_save(pilot, app)
        await pilot.pause(0.5)
        assert len(app.preview.children) > 0


async def test_s13_ctrl_x_with_editor_focused_exits():
    app = NanoPeek(None)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.2)
        assert app.editor.has_focus
        await pilot.press("ctrl+x")
        await pilot.pause(0.3)
        assert not app.is_running


async def test_s16_save_as_writes_file_and_switches_mode(tmp_path):
    app = NanoPeek(None)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.2)
        app.editor.text = 'name = "demo"\n'
        target = tmp_path / "saved.toml"
        app._after_save_as(str(target))
        await pilot.pause(0.4)
        assert target.read_text() == 'name = "demo"\n'
        assert app.preview_kind == "toml"
