#!/usr/bin/env python3
"""Tests for the JSON formatter preview mode.

Run:  python3 tests/json_test.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from textual.widgets import Static, TextArea

from nanopeek import NanoPeek, _strip_jsonc

PASS = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"ok: {msg}")


# 13. unit: _strip_jsonc preserves strings/positions, drops comments+commas
_raw = ('{\n  // line comment\n  "url": "http://x/a,]", /* block\n comment */'
        ' "list": [1, 2,],\n  "tail": "ends with // not a comment",\n}\n')
_clean = _strip_jsonc(_raw)
assert json.loads(_clean), "stripped text must be valid JSON"
_parsed = json.loads(_clean)
assert _parsed["url"] == "http://x/a,]", _parsed
assert _parsed["tail"] == "ends with // not a comment"
assert _clean.splitlines()[1].strip() == "", "comment line must become blank"
assert len(_clean) == len(_raw) and _clean.count("\n") == _raw.count("\n"), (
    "positions must be preserved for error reporting"
)
ok("_strip_jsonc: comments/trailing commas out, strings + positions intact")


VALID = '{"name": "nanopeek", "tags": [1, 2], "nested": {"deep": true, "n": null}}'


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        # 1. .json file opens in JSON mode with the formatter pane
        jf = Path(tmp) / "config.json"
        jf.write_text(VALID, encoding="utf-8")
        app = NanoPeek(jf)
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.6)
            ed = app.query_one("#editor", TextArea)
            pane = app.query_one("#preview-pane")
            assert app.preview_kind == "json"
            assert pane.has_class("json") and not pane.has_class("json-error")
            assert ed.language == "json", ed.language
            err = str(app.query_one("#preview-error", Static).content)
            assert err == "", err
            ok(".json file -> json mode, formatted pane, json highlighting")

            # 2. typing invalid JSON shows a live error with position
            ed.text = '{"a": 1, oops}'
            await pilot.pause(0.6)
            assert pane.has_class("json-error"), "invalid JSON did not show error"
            err = str(app.query_one("#preview-error", Static).content)
            assert "Invalid JSON" in err and "line 1" in err, err
            ok("invalid JSON -> error panel with line/column")

            # 3. fixing it restores the formatted view
            ed.text = '{"a": 1, "b": "oops"}'
            await pilot.pause(0.6)
            assert not pane.has_class("json-error")
            ok("fixed JSON -> back to formatted view")

            # 3b. the formatter is syntax-colored from the active theme
            from rich.text import Text as RichText
            content = app.query_one("#json-static", Static).content
            assert isinstance(content, RichText), type(content)
            spans = content.spans
            assert len(spans) >= 6, f"expected colored spans, got {len(spans)}"
            plain = content.plain
            assert '"a"' in plain and '"oops"' in plain and "1" in plain
            colors = {str(s.style) for s in spans}
            assert any("bold" in c or "#" in c for c in colors), colors
            # key, string, number each got distinct styles
            def style_of(sub):
                idx = plain.find(sub)
                hits = [str(s.style) for s in spans if s.start <= idx < s.end]
                return hits[0] if hits else None
            assert style_of('"a"') != style_of('"oops"') != style_of(" 1".strip())
            ok("formatter colors keys/strings/numbers distinctly")

            # 3c. changing the theme recolors the JSON
            old_key_style = style_of('"a"')
            app.theme = "nord"
            await pilot.pause(0.6)
            content2 = app.query_one("#json-static", Static).content
            def style_of2(sub):
                idx = content2.plain.find(sub)
                hits = [str(s.style) for s in content2.spans if s.start <= idx < s.end]
                return hits[0] if hits else None
            assert style_of2('"a"') != old_key_style, "theme change did not recolor"
            app.theme = "textual-dark"
            await pilot.pause(0.6)
            ok("theme change recolors the JSON formatter")

            # 4. unicode stays readable (ensure_ascii=False)
            ed.text = '{"k": "caf\\u00e9"}'
            await pilot.pause(0.6)
            assert not pane.has_class("json-error")
            ok("unicode values render without escapes")

            # 5b. scroll lock is bidirectional in json mode too
            ed.text = "[\n" + ",\n".join(
                f'  {{"item_{i}": {i}}}' for i in range(80)
            ) + "\n]"
            await pilot.pause(0.6)
            jv = app.query_one("#json-view")
            assert jv.max_scroll_y > 0
            assert ed.max_scroll_y > 0
            jv.scroll_to(y=jv.max_scroll_y / 2, animate=False)
            await pilot.pause(0.3)
            ratio_ed = ed.scroll_y / ed.max_scroll_y
            ratio_pv = jv.scroll_y / jv.max_scroll_y
            assert abs(ratio_ed - ratio_pv) <= 0.05, (
                f"json panes drifted: editor {ratio_ed:.2f} vs view {ratio_pv:.2f}"
            )
            ed.scroll_to(y=ed.max_scroll_y / 4, animate=False)
            await pilot.pause(0.3)
            ratio_ed = ed.scroll_y / ed.max_scroll_y
            ratio_pv = jv.scroll_y / jv.max_scroll_y
            assert abs(ratio_ed - ratio_pv) <= 0.05, (
                f"json panes drifted after editor scroll: {ratio_ed:.2f} vs {ratio_pv:.2f}"
            )
            ok("json panes scroll together in both directions")

            # 5. ^P full-screen preview focuses the JSON view
            await pilot.press("ctrl+p")
            await pilot.pause(0.3)
            assert app.layout_mode == "preview"
            assert app.query_one("#json-view").has_focus
            await pilot.press("ctrl+p")
            await pilot.press("ctrl+p")
            await pilot.pause(0.2)
            assert app.layout_mode == "split"
            ok("^P full preview works in json mode")

        # 6. .md file stays in markdown mode
        mf = Path(tmp) / "note.md"
        mf.write_text("# hi\n", encoding="utf-8")
        app2 = NanoPeek(mf)
        async with app2.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.5)
            assert app2.preview_kind == "markdown"
            assert not app2.query_one("#preview-pane").has_class("json")
            assert app2.query_one("#editor", TextArea).language == "markdown"
            ok(".md file -> markdown mode unchanged")

        # 7. forced flags override the extension
        app3 = NanoPeek(mf, preview_kind="json")
        async with app3.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.5)
            assert app3.preview_kind == "json"
            assert app3.query_one("#preview-pane").has_class("json")
            ok("--json forces the formatter for any file")

        # 8. saving an md buffer as .json switches modes on the fly
        app4 = NanoPeek(None)
        async with app4.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.3)
            assert app4.preview_kind == "markdown"
            app4.query_one("#editor", TextArea).text = '{"written": true}'
            await pilot.pause(0.1)
            await pilot.press("ctrl+o")
            await pilot.pause(0.2)
            app4.screen.query_one("#save-input").value = str(Path(tmp) / "switched.json")
            await pilot.press("enter")
            await pilot.pause(0.6)
            assert app4.preview_kind == "json"
            assert app4.query_one("#preview-pane").has_class("json")
            assert not app4.query_one("#preview-pane").has_class("json-error")
            ok("save-as .json switches the preview to JSON formatter")

        # 9. JSONL: one record per line, blank lines skipped
        lf = Path(tmp) / "log.jsonl"
        lf.write_text(
            '{"a": 1}\n\n{"b": [true, null]}\n{"c": "x"}\n',
            encoding="utf-8",
        )
        app5 = NanoPeek(lf)
        async with app5.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.6)
            pane5 = app5.query_one("#preview-pane")
            assert app5.preview_kind == "json" and app5.json_lines_mode
            assert pane5.has_class("json") and not pane5.has_class("json-error")
            plain = str(app5.query_one("#json-static", Static).content.plain)
            for key in ('"a"', '"b"', '"c"'):
                assert key in plain, key
            assert "[\n    true,\n    null\n  ]" in plain, "records not pretty-printed"
            status = str(app5.query_one("#status-left", Static).content)
            assert "jsonl" in status, status
            ok(".jsonl -> per-line records, pretty-printed, blank lines skipped")

            # 10. broken record reports the file line number
            app5.query_one("#editor", TextArea).text = '{"a": 1}\n{oops}\n{"c": 3}\n'
            await pilot.pause(0.6)
            assert pane5.has_class("json-error")
            err = str(app5.query_one("#preview-error", Static).content)
            assert "line 2" in err, err
            ok("broken jsonl record -> error names the line")

        # 11. .ndjson is detected the same way
        nf = Path(tmp) / "stream.ndjson"
        nf.write_text('{"n": 1}\n{"n": 2}\n', encoding="utf-8")
        app6 = NanoPeek(nf)
        async with app6.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.5)
            assert app6.json_lines_mode
            assert not app6.query_one("#preview-pane").has_class("json-error")
            ok(".ndjson detected as JSON Lines")

        # 12. multi-object .json falls back to the records view
        mf2 = Path(tmp) / "multi.json"
        mf2.write_text('{"x": 1}{"x": 2}', encoding="utf-8")
        app7 = NanoPeek(mf2)
        async with app7.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.5)
            assert not app7.query_one("#preview-pane").has_class("json-error")
            plain = str(app7.query_one("#json-static", Static).content.plain)
            assert plain.count('"x"') == 2, plain
            ok("multi-object .json falls back to per-record view")

        # 14. .jsonc file: comments and trailing commas render fine
        cf = Path(tmp) / "settings.jsonc"
        cf.write_text(
            "{\n  // editor settings\n  \"theme\": \"nord\", /* picked */\n"
            "  \"numbers\": [1, 2,],\n}\n",
            encoding="utf-8",
        )
        app8 = NanoPeek(cf)
        async with app8.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.6)
            pane8 = app8.query_one("#preview-pane")
            assert app8.preview_kind == "json"
            assert pane8.has_class("json") and not pane8.has_class("json-error")
            plain = str(app8.query_one("#json-static", Static).content.plain)
            assert '"theme"' in plain and '"nord"' in plain and "1," in plain
            assert "//" not in plain, "comments must not reach the formatter"
            status = str(app8.query_one("#status-left", Static).content)
            assert "jsonc" in status, status
            ok(".jsonc -> comments stripped, trailing commas tolerated, badge shown")

            # 15. error position still matches the original buffer lines
            app8.query_one("#editor", TextArea).text = (
                "{\n  \"a\": 1,\n  // note\n  {oops\n}\n"
            )
            await pilot.pause(0.6)
            assert pane8.has_class("json-error")
            err = str(app8.query_one("#preview-error", Static).content)
            assert "line 4" in err, err
            ok("jsonc error position matches the original line")

        # 16. commented .json also gets the lenient fallback
        jf2 = Path(tmp) / "commented.json"
        jf2.write_text('{\n  "a": 1, // why not\n}\n', encoding="utf-8")
        app9 = NanoPeek(jf2)
        async with app9.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.6)
            assert not app9.query_one("#preview-pane").has_class("json-error")
            plain = str(app9.query_one("#json-static", Static).content.plain)
            assert '"a"' in plain
            ok("commented .json falls back to comment-stripped view")

        # 17. large documents stay responsive: preview capped, editor trimmed
        big = "[" + ",\n".join(
            f'  {{"id": {i}, "name": "item-{i}-with-a-rather-long-name-to-add-bulk", '
            f'"tags": ["alpha", "beta", "gamma"]}}' for i in range(8000)
        ) + "\n]"
        assert len(big) > 400_000, len(big)
        bf = Path(tmp) / "big.json"
        bf.write_text(big, encoding="utf-8")
        app10 = NanoPeek(bf)
        async with app10.run_test(size=(110, 32)) as pilot:
            await pilot.pause(1.5)
            pane10 = app10.query_one("#preview-pane")
            ed10 = app10.query_one("#editor", TextArea)
            assert not pane10.has_class("json-error")
            plain = str(app10.query_one("#json-static", Static).content.plain)
            assert "preview truncated" in plain, "large doc must be capped"
            assert plain.count("\n") < 2200, "rendered pane exceeds the cap"
            assert bf.read_text() == big, "buffer/file must be untouched"
            assert ed10.language is None, "highlighting off for huge buffer"
            ok("large json: preview capped, file untouched, highlighting off")

            # minified: one giant line disables wrap and blink too
            minified = json.dumps([{"i": i} for i in range(4000)], separators=(",", ":"))
            mf3 = Path(tmp) / "mini.json"
            mf3.write_text(minified, encoding="utf-8")
            app11 = NanoPeek(mf3)
            async with app11.run_test(size=(110, 32)) as pilot11:
                await pilot11.pause(1.0)
                ed11 = app11.query_one("#editor", TextArea)
                assert ed11.soft_wrap is False, "soft wrap off for giant line"
                assert ed11.cursor_blink is False, "cursor blink off for giant line"
                assert ed11.language is None
                plain11 = str(app11.query_one("#json-static", Static).content.plain)
                assert "preview truncated" in plain11
                # features return when the buffer becomes small again
                ed11.text = '{"small": true}\n'
                await pilot11.pause(0.5)
                assert ed11.soft_wrap and ed11.cursor_blink
                assert ed11.language == "json", "highlighting restored"
                ok("minified json: wrap/blink/highlight off, all restored on shrink")

    print(f"\nALL JSON MODE TESTS PASSED ({PASS} checks)")


if __name__ == "__main__":
    asyncio.run(main())
