#!/usr/bin/env python3
"""Config source viewer integration tests: python3 tests/config_test.py."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.text import Text
from textual.widgets import Static
from nanopeek import MAX_PREVIEW_LINES, NanoPeek, main as cli


YAML = "# keep this comment\nservices:\n  app:\n    image: alpine\n    command: echo café\n"
TOML = '# keep this comment\n[app]\nname = "café"\nports = [8000, 8001]\n'
DOCKER = '# syntax=docker/dockerfile:1\nFROM alpine AS build\nRUN echo hello \\\n    && echo world\nCMD ["sh"]\n'


def content(app: NanoPeek) -> Text:
    value = app.query_one("#json-static", Static).content
    assert isinstance(value, Text), value
    return value


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for filename, kind in (
            ("config.yaml", "yaml"), ("CONFIG.YML", "yaml"),
            ("compose.yaml", "yaml"), ("docker-compose.yml", "yaml"),
            ("pyproject.toml", "toml"), ("Dockerfile", "dockerfile"),
            ("Dockerfile.dev", "dockerfile"), ("build.Dockerfile", "dockerfile"),
            ("Containerfile", "dockerfile"), ("Containerfile.prod", "dockerfile"),
            ("readme.md", "markdown"), ("data.json", "json"),
        ):
            assert NanoPeek(root / filename).preview_kind == kind, filename
        for kind in ("yaml", "toml", "dockerfile"):
            with patch("nanopeek.NanoPeek") as app_class:
                assert cli(["unknown.txt", f"--{kind}"]) == 0
                assert app_class.call_args.kwargs["preview_kind"] == kind
        assert NanoPeek(root / "compose.yaml", preview_kind="markdown").preview_kind == "markdown"
        print("ok: file detection, filename variants and CLI overrides")

        for filename, kind, source, broken in (
            ("compose.yaml", "yaml", YAML, "services:\n  app: [oops\n"),
            ("config.toml", "toml", TOML, '[app]\nname = "oops\n'),
            ("Dockerfile", "dockerfile", DOCKER, None),
        ):
            path = root / filename
            path.write_text(source, encoding="utf-8")
            app = NanoPeek(path)
            async with app.run_test(size=(110, 32)) as pilot:
                await pilot.pause(0.4)
                pane = app.query_one("#preview-pane")
                assert app.preview_kind == kind
                assert app.json_view.display and not app.preview.display
                assert not pane.has_class("json-error")
                assert content(app).plain == source
                assert content(app).spans, "source must be highlighted"
                assert app.editor.text == source and path.read_text() == source
                assert kind in str(app.query_one("#status-left", Static).content)
                await pilot.press("ctrl+p")
                await pilot.pause()
                assert app.layout_mode == "preview" and app.focused is app.json_view
                await pilot.press("ctrl+p")
                await pilot.press("ctrl+p")
                if broken:
                    app.editor.text = broken
                    await pilot.pause(0.4)
                    assert pane.has_class("json-error")
                    error = app.query_one("#preview-error", Static).content
                    assert isinstance(error, Text) and f"Invalid {kind.upper()}" in error.plain
                    assert "line" in error.plain.lower(), error
                    app.editor.text = source
                    await pilot.pause(0.4)
                    assert not pane.has_class("json-error")
                    assert content(app).plain == source
                app.theme = "textual-light"
                await pilot.pause(0.4)
                assert content(app).plain == source and content(app).spans
                app.editor.text = ""
                await pilot.pause(0.4)
                assert not pane.has_class("json-error")
                assert "empty buffer" in str(app.query_one("#json-static", Static).content)
                app.editor.text = source
                await pilot.pause(0.4)
                await pilot.press("ctrl+x")
                await pilot.press("escape")
                await pilot.pause(0.4)
                assert app.is_running and content(app).plain == source
                assert app._do_save()
                assert path.read_text() == source
                await pilot.press("ctrl+x")
            print(f"ok: {kind} preview, validation/recovery, themes, layouts, empty buffer and save")

        path = root / "special.yaml"
        source = "# tags and aliases stay literal\nvalue: !Custom tagged\na: &a [*a]\n---\nsecond: document\n"
        path.write_text(source)
        app = NanoPeek(path)
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.4)
            assert not app.query_one("#preview-pane").has_class("json-error")
            assert content(app).plain == source
            # Changing the file type must invalidate the preview and preserve the buffer.
            app.editor.text = 'name = "demo"\n'
            target = root / "saved.toml"
            app._after_save_as(str(target))
            await pilot.pause(0.4)
            assert app.preview_kind == "toml"
            assert not app.query_one("#preview-pane").has_class("json-error")
            assert content(app).plain == target.read_text()
            assert "toml" in str(app.query_one("#status-left", Static).content)
            await pilot.press("ctrl+x")
        print("ok: YAML multi-document, custom tags, recursive aliases and Save As mode switching")

        path = root / "large.toml"
        source = "# a comment\n" * (MAX_PREVIEW_LINES + 20)
        path.write_text(source)
        app = NanoPeek(path)
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.5)
            assert "preview truncated" in content(app).plain
            assert content(app).plain.count("\n") < MAX_PREVIEW_LINES + 5
            assert app.editor.text == source and path.read_text() == source
            assert app.json_view.max_scroll_y > 0
            app.editor.scroll_to(y=app.editor.max_scroll_y / 2, animate=False)
            await pilot.pause(0.3)
            assert app.json_view.scroll_y > 0
            await pilot.press("ctrl+x")
        print("ok: large preview cap and scroll synchronization, source unchanged")
    print("ALL CONFIG VIEWER TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
