#!/usr/bin/env python3
"""nanopeek — a nano-style terminal editor with a live rendered preview.

Usage:
    nanopeek [FILE]

The left pane is a plain editor with nano-style keybindings (^O write out,
^X exit, ^K cut line, ^U uncut, ...). The right pane renders live: for
Markdown files it shows the rendered document (headings, emphasis, lists,
tables, fenced code, block quotes, rules, links); for .json files it is a
formatter/validator showing pretty-printed, syntax-highlighted JSON (or a
parse error pointing at the offending line and column). YAML and TOML
get validated source previews; Dockerfiles get highlighted source views.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from bisect import bisect_right
from pathlib import Path
from typing import Optional

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

try:  # textual dependency; used for source-line -> heading mapping
    import markdown_it
except ImportError:  # pragma: no cover
    markdown_it = None

from rich.text import Text
from rich.syntax import Syntax
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, HorizontalGroup, Middle, Vertical, VerticalScroll
from textual.filter import NoColor
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Markdown, Static, TextArea

__version__ = "0.1.1"

DEBOUNCE = 0.2  # seconds of idle typing before the preview re-renders
COPY_NOTIFY_DEBOUNCE = 0.5  # wait for the mouse to settle before "Copied" toast

# Performance guards for large documents: the preview renders at most this
# many lines (the buffer itself is never modified); soft wrap and the
# blinking cursor are disabled when a single line is enormous (minified
# JSON etc.); tree-sitter highlighting is disabled for huge buffers
# (it re-parses the document on every keystroke). Markdown gets a much
# higher cap than JSON so section-anchored scroll sync stays meaningful
# on long documents (a markdown block is cheap next to a JSON token).
MAX_PREVIEW_LINES = 2_000       # JSON formatter panes
MAX_MD_PREVIEW_LINES = 12_000   # rendered Markdown pane
SOFT_WRAP_DISABLE_CHARS = 10_000
SOFT_WRAP_REENABLE_CHARS = 2_000
LARGE_BUFFER_CHARS = 400_000
LARGE_BUFFER_REENABLE_CHARS = 300_000

HELPBAR_1 = (
    " [b]^G[/b] Help      [b]^O[/b] Write Out   [b]^W[/b] Where Is   "
    "[b]^K[/b] Cut Line    [b]^U[/b] Uncut"
)
HELPBAR_2 = (
    " [b]^X[/b] Exit      [b]^S[/b] Save        [b]^Y[/b]/[b]^V[/b] Page      "
    "[b]^P[/b] Preview     [b]Tab[/b] Panes"
)

HELP_TEXT = """\
[b]nanopeek — nano-style editor with live document previews[/b]

[b]Editing[/b]
  ^K / ^U          cut line / uncut (consecutive ^K cuts append)
  ^A / ^E          move to beginning / end of line
  ^Y / ^V          previous page / next page
  Tab / Shift+Tab  switch between editor and preview panes

[b]File[/b]
  ^S               save (prompts for a name for a new buffer)
  ^O               write out (always prompts for the file name)
  ^X               exit (asks to save a modified buffer)

[b]Search[/b]
  ^W               where is; Enter jumps to the next match, Esc closes

[b]Preview[/b]
  ^P               cycle view: split / full rendered page / editor only
  .json/.jsonl/.jsonc files  the pane becomes a JSON formatter/validator
                   (pretty-printed, highlighted, live error position;
                   .jsonl/.ndjson show one pretty record each, and
                   comments/trailing commas in .jsonc are tolerated)
  (the two panes scroll together — mouse wheel, keys, either side)
  .yaml/.yml/.toml  highlighted source with live syntax validation
  Dockerfile / Containerfile (including variants)  highlighted source
                   Docker Compose files use the YAML viewer

[b]Display modes[/b]
  Alt+T            cycle theme (dark, light, flexoki, nord, ...)
  Alt+D            toggle dark <-> light theme
  Alt+C            cycle color mode: truecolor / ansi / mono
  Alt+A            toggle ASCII-only output (for legacy terminals)

[b]Misc[/b]
  ^G               this help
  select with mouse (or Shift+arrows) — auto-copied to the clipboard
  Ctrl+Shift+X/C/V cut / copy / paste (plain ^X/^C/^V are nano keys)
  Ctrl+Shift+P     Textual command palette
  Esc / ^C         close dialogs without doing anything
"""


# Keys nano owns; TextArea bindings touching any of these are removed.
NANO_KEYS = {"ctrl+x", "ctrl+c", "ctrl+v", "ctrl+k", "ctrl+w", "ctrl+u", "ctrl+y"}

# Alt+T cycles through these (all names must exist in textual's built-ins).
THEME_CYCLE = [
    "textual-dark", "textual-light", "flexoki", "nord", "gruvbox",
    "dracula", "solarized-light", "solarized-dark", "tokyo-night",
    "catppuccin-mocha", "rose-pine", "monokai",
]
# Themes that have a real dark/light counterpart for Alt+D.
THEME_PAIRS = {
    "textual-dark": "textual-light", "textual-light": "textual-dark",
    "solarized-dark": "solarized-light", "solarized-light": "solarized-dark",
    "catppuccin-mocha": "catppuccin-latte", "catppuccin-macchiato": "catppuccin-latte",
    "catppuccin-frappe": "catppuccin-latte", "catppuccin-latte": "catppuccin-mocha",
    "rose-pine": "rose-pine-dawn", "rose-pine-dawn": "rose-pine",
    "rose-pine-moon": "rose-pine-dawn", "ansi-dark": "ansi-light",
    "ansi-light": "ansi-dark",
}
COLOR_MODES = ("truecolor", "ansi", "mono")

# ^P cycles the layout: split (edit + preview) -> full-screen rendered
# Markdown -> editor only -> back to split.
LAYOUT_MODES = ("split", "preview", "edit")

# Width-preserving 1:1 replacements so ASCII mode never breaks layout.
_ASCII_REPLACEMENTS = {    "…": ".", "·": "-", "•": "*", "●": "*", "◦": "o", "◆": "*", "◇": "o",
    "■": "#", "□": ".", "▲": "^", "▼": "v", "►": ">", "◄": "<",
    "─": "-", "━": "=", "│": "|", "┃": "|", "┌": "+", "┐": "+", "└": "+", "┘": "+",
    "├": "+", "┤": "+", "┬": "+", "┴": "+", "┼": "+", "╭": "+", "╮": "+", "╰": "+", "╯": "+",
    "═": "=", "║": "|", "╔": "+", "╗": "+", "╚": "+", "╝": "+", "╠": "+", "╣": "+",
    "╦": "+", "╩": "+", "╬": "+",
    "█": "#", "▀": "#", "▄": "#", "▌": "|", "▐": "|", "░": ".", "▒": ":", "▓": "#",
    "←": "<", "→": ">", "↑": "^", "↓": "v", "↔": "-", "↵": "^",
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
    "×": "x", "÷": "/", "✓": "+", "✔": "+", "✗": "x", "✘": "x",
    "⌂": "h", "ℓ": "l", "⏎": "^", "⚠": "!", "\u00a0": " ",
}
ASCII_TABLE = str.maketrans(_ASCII_REPLACEMENTS)

# JSON syntax tokens for the formatter's highlighting (keys, strings,
# numbers, booleans, null; everything in between is punctuation).
_JSON_TOKENS = re.compile(
    r'"(?:\\.|[^"\\])*"\s*:|"(?:\\.|[^"\\])*"'
    r"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null"
)

JSON_SUFFIXES = {".json"}
JSONL_SUFFIXES = {".jsonl", ".ndjson"}  # one JSON value per line
JSONC_SUFFIXES = {".jsonc"}  # JSON with // and /* */ comments, trailing commas
SOURCE_SUFFIXES = {".yaml": "yaml", ".yml": "yaml", ".toml": "toml"}

_MD_PARSER = markdown_it.MarkdownIt() if markdown_it is not None else None


def _strip_jsonc(text: str) -> str:
    """Blank out JSONC extras, preserving error positions.

    Replaces // and /* */ comments (outside strings) and trailing commas
    with spaces, keeping newlines, so a later parse error still reports
    the line and column of the original buffer.
    """
    out = list(text)
    n = len(text)
    commas: list[int] = []
    i = 0
    in_string = False
    escape = False
    while i < n:
        ch = out[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == "/" and i + 1 < n and out[i + 1] == "/":
            while i < n and out[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if ch == "/" and i + 1 < n and out[i + 1] == "*":
            opened = i
            out[i] = out[i + 1] = " "
            i += 2
            while i < n and not (out[i] == "*" and i + 1 < n and out[i + 1] == "/"):
                if out[i] != "\n":
                    out[i] = " "
                i += 1
            if i < n:  # blank the closing */
                out[i] = out[i + 1] = " "
                i += 2
            else:  # never closed: keep it, so the parse fails at the /*
                out[opened:] = text[opened:]
            continue
        if ch == ",":
            commas.append(i)
        i += 1
    for pos in commas:  # trailing commas: last item before } or ]
        j = pos + 1
        while j < n and out[j] in " \t\r\n":
            j += 1
        if j < n and out[j] in "}]":
            out[pos] = " "
    return "".join(out)


class FocusableMarkdown(Markdown):
    """Markdown viewer that accepts focus, so Tab can switch panes."""

    can_focus = True


class NanoTextArea(TextArea):
    """A TextArea with nano key semantics.

    Stock TextArea grabs several ctrl keys for editing/clipboard shortcuts
    (it binds e.g. "ctrl+x,super+x" = cut). In nano those keys mean other
    things (^X exit, ^W search, ^K cut line, ...), so every binding that
    touches a nano key is dropped and the nano keys are re-bound here,
    where they reliably fire while the editor has focus. Clipboard
    operations move to Ctrl+Shift+X/C/V.
    """

    BINDINGS = [
        binding
        for binding in TextArea.BINDINGS
        if not {key.strip() for key in binding.key.split(",")} & NANO_KEYS
    ] + [
        Binding("ctrl+shift+x", "cut", "Cut", show=False),
        Binding("ctrl+shift+c", "copy", "Copy", show=False),
        Binding("ctrl+shift+v", "paste", "Paste", show=False),
        # nano keys — every action resolves on the app
        Binding("ctrl+x", "app.exit", show=False),
        Binding("ctrl+c", "app.exit", show=False),
        Binding("ctrl+s", "app.save", show=False),
        Binding("ctrl+o", "app.write_out", show=False),
        Binding("ctrl+w", "app.where_is", show=False),
        Binding("ctrl+k", "app.cut_line", show=False),
        Binding("ctrl+u", "app.uncut", show=False),
        Binding("ctrl+y", "app.page_up", show=False),
        Binding("ctrl+v", "app.page_down", show=False),
        Binding("ctrl+p", "app.toggle_preview", show=False),
        Binding("ctrl+g", "app.help", show=False),
        # display modes
        Binding("alt+t", "app.cycle_theme", show=False),
        Binding("alt+d", "app.toggle_dark", show=False),
        Binding("alt+c", "app.cycle_color", show=False),
        Binding("alt+a", "app.toggle_ascii", show=False),
    ]


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape,enter,ctrl+g,ctrl+x,ctrl+c", "dismiss(None)", "Close", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Middle():
            yield Static(HELP_TEXT, id="help-text", classes="dialog")


class ConfirmExitScreen(ModalScreen[Optional[str]]):
    """nano's "Save modified buffer?" — returns 'save', 'discard' or None."""

    BINDINGS = [
        Binding("escape,ctrl+c,ctrl+x,ctrl+s,ctrl+o", "dismiss(None)", "Cancel", priority=True),
        Binding("y", "dismiss('save')", "Save", show=False),
        Binding("n", "dismiss('discard')", "Don't save", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Middle():
            with Vertical(classes="dialog"):
                yield Static("Save modified buffer?", id="confirm-text")
                with HorizontalGroup(classes="buttons"):
                    yield Button("Yes", variant="primary", id="yes")
                    yield Button("No", variant="error", id="no")
                    yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        choice = {"yes": "save", "no": "discard"}.get(event.button.id)
        self.dismiss(choice)


class SaveScreen(ModalScreen[Optional[str]]):
    """Prompt for a file name (nano's Write Out prompt)."""

    BINDINGS = [
        Binding("escape,ctrl+c,ctrl+x,ctrl+s,ctrl+o", "dismiss(None)", "Cancel", priority=True),
    ]

    def __init__(self, default: str = "") -> None:
        super().__init__()
        self.default = default

    def compose(self) -> ComposeResult:
        with Middle():
            with Vertical(classes="dialog"):
                yield Static("File name to write:", id="save-label")
                yield Input(value=self.default, id="save-input")
                yield Static("Enter to confirm · Esc to cancel", id="save-hint")

    def on_mount(self) -> None:
        self.query_one("#save-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.dismiss(event.value.strip())


class SearchScreen(ModalScreen[None]):
    """nano's "Where Is" — stays open; every Enter jumps to the next match."""

    BINDINGS = [
        Binding("escape,ctrl+c,ctrl+x,ctrl+w", "close", "Cancel", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Middle():
            with Vertical(classes="dialog"):
                yield Static("Where is (Enter for next match):", id="search-label")
                yield Input(id="search-input")
                yield Static("Enter next · Esc to cancel", id="search-hint")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.app.find_and_jump(event.value)

    def action_close(self) -> None:
        self.dismiss(None)


class NanoPeek(App[None]):
    TITLE = "nanopeek"

    CSS = """
    #main { height: 1fr; }
    #main.no-preview #preview-pane, #main.no-preview #vdivider { display: none; }
    #main.preview-only #editor, #main.preview-only #vdivider { display: none; }

    NanoTextArea#editor {
        width: 1fr; height: 1fr;
        border: none;
        background: $surface;
        padding: 0 1;
    }
    #vdivider { width: 1; height: 1fr; background: $panel; }
    #preview-pane { width: 1fr; height: 100%; }

    Markdown#preview {
        width: 1fr; height: 100%;
        padding: 0 2;
        background: $surface;
        overflow-y: auto;  /* Markdown defaults to hidden, which blocks all scrolling */
    }

    #json-view, #preview-error { display: none; }
    #preview-pane.json Markdown#preview { display: none; }
    #preview-pane.json #json-view { display: block; }
    #preview-pane.json-error Markdown#preview,
    #preview-pane.json-error #json-view { display: none; }
    #preview-pane.json-error #preview-error { display: block; }

    #json-view { background: $surface; padding: 0 2; }
    #json-static { width: 1fr; }
    #preview-error { padding: 0 2; color: $text; background: $surface; }

    #status { height: 1; background: $panel; }
    #status-left, #status-center, #status-right {
        height: 1; background: $panel; padding: 0 1;
    }
    #status-left, #status-right { width: auto; }
    #status-center { width: 1fr; text-align: center; text-style: bold; }
    #status-right  { text-align: right; color: $text-muted; }
    #helpbar { height: 2; background: $panel; color: $text-muted; padding: 0 1; }

    HelpScreen, ConfirmExitScreen, SaveScreen, SearchScreen {
        align: center middle;
        background: $background 60%;
    }
    .dialog {
        width: 64; max-width: 90%;
        height: auto;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    .dialog#help-text { width: 76; padding: 1 2; }
    #confirm-text { padding: 0 1; }
    .buttons { align-horizontal: center; height: auto; margin-top: 1; }
    #save-label, #search-label { padding: 0 1; }
    #save-input, #search-input { width: 1fr; margin: 0 1; }
    #save-hint, #search-hint { color: $text-muted; padding: 0 1; margin-top: 1; }
    """

    BINDINGS = [
        # Relocate Textual's command palette off ctrl+p so ^P toggles the
        # preview (the palette binding is only auto-added if no binding
        # with the command_palette action exists).
        Binding("ctrl+shift+p", "command_palette", "Command Palette", show=False),
        Binding("ctrl+g", "help", "Get Help"),
        Binding("ctrl+x", "exit", "Exit"),
        Binding("ctrl+c", "exit", "Exit", priority=True),
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+o", "write_out", "Write Out"),
        Binding("ctrl+w", "where_is", "Where Is"),
        Binding("ctrl+k", "cut_line", "Cut Line"),
        Binding("ctrl+u", "uncut", "Uncut"),
        Binding("ctrl+p", "toggle_preview", "Preview"),
        Binding("ctrl+y", "page_up", "Prev Page", show=False),
        Binding("ctrl+v", "page_down", "Next Page", show=False),
        Binding("ctrl+a", "line_start", "Line Start", show=False),
        Binding("ctrl+e", "line_end", "Line End", show=False),
        Binding("alt+t", "cycle_theme", "Theme"),
        Binding("alt+d", "toggle_dark", "Dark/Light"),
        Binding("alt+c", "cycle_color", "Colors"),
        Binding("alt+a", "toggle_ascii", "ASCII"),
    ]

    def __init__(
        self,
        path: Path | None = None,
        theme: str | None = None,
        color: str = "truecolor",
        ascii_mode: bool = False,
        preview_kind: str | None = None,
    ) -> None:
        super().__init__()
        self.path = path
        self.dirty = False
        self.layout_mode = "split"
        self._forced_preview_kind = preview_kind
        self._refresh_timer = None
        self._cut_buffer: list[str] = []
        self._last_cut_at = 0.0
        self._exit_after_save = False
        self._init_theme = theme
        self.color_mode = color
        self.ascii_mode = False
        self._theme_before_ansi: str | None = None
        self._init_ascii = ascii_mode
        self._copy_notify_timer = None
        self._scroll_syncing = False
        self._last_preview_key: tuple | None = None
        self._word_count = 0
        self._longest_line = 0
        self._language_backup: str | None = None
        self._heading_src: list[tuple[int, int]] = []
        self._heading_pairs: list[tuple[int, object]] = []
        self._match_retries = 0
        self._exiting = False
        if path is not None:
            try:
                self.initial_text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                self.notify(f"Could not read {path}: {exc}", severity="error")
                self.initial_text = ""
        else:
            self.initial_text = ""

    # ------------------------------------------------------------------ UI

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            try:
                editor = NanoTextArea(
                    self.initial_text,
                    language="markdown",
                    soft_wrap=True,
                    show_line_numbers=False,
                    id="editor",
                )
            except Exception:  # tree-sitter not available — plain editing still works
                editor = NanoTextArea(
                    self.initial_text,
                    soft_wrap=True,
                    show_line_numbers=False,
                    id="editor",
                )
            yield editor
            yield Static("", id="vdivider")
            with Vertical(id="preview-pane"):
                yield FocusableMarkdown("", id="preview")
                with VerticalScroll(id="json-view"):
                    yield Static("", id="json-static", markup=False)
                yield Static("", id="preview-error")
        with Horizontal(id="status"):
            yield Static("nanopeek", id="status-left")
            yield Static("New Buffer", id="status-center")
            yield Static("", id="status-right")
        yield Static(HELPBAR_1 + "\n" + HELPBAR_2, id="helpbar", markup=True)

    def on_mount(self) -> None:
        self.editor = self.query_one("#editor", NanoTextArea)
        self.preview = self.query_one("#preview", Markdown)
        self.json_view = self.query_one("#json-view", VerticalScroll)
        self._apply_preview_kind()
        self._language_backup = None
        self._recompute_stats()
        self._update_soft_wrap(notify=False)
        self.editor.focus()
        self.update_status()
        self.schedule_preview_refresh(delay=0.01)
        # Live scroll sync: watch the editor's scroll_y reactive from the app
        # so the preview follows plain scrolling (arrow keys past the
        # viewport, ^Y/^V, mouse wheel) — not just post-render refreshes.
        self.watch(self.editor, "scroll_y", self._sync_from_editor, init=False)
        # The lock is bidirectional: scrolling the preview (mouse wheel,
        # keys) moves the editor along, and vice versa — always, in both
        # Markdown and JSON modes.
        self.watch(self.preview, "scroll_y", self._sync_from_right, init=False)
        self.watch(self.json_view, "scroll_y", self._sync_from_right, init=False)
        # Auto-copy: any selection (mouse drag, shift+arrows, ^K-style
        # selections) is copied to the system clipboard as it changes.
        self.watch(self.editor, "selection", self._auto_copy_selection, init=False)
        # Re-render the JSON formatter's colors when the theme changes.
        self.watch(self, "theme", self._theme_changed, init=False)
        # Apply requested display modes.
        if self._init_theme:
            self.theme = self._init_theme
        self._apply_color_mode(self.color_mode)
        if self._init_ascii:
            self._set_ascii_mode(True)

    # ------------------------------------------------------- editor events

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self.dirty = True
        self._recompute_stats()
        self._update_soft_wrap()
        self.update_status()
        self.schedule_preview_refresh()

    def on_text_area_cursor_moved(self, event: TextArea.CursorMoved) -> None:
        self.update_status()

    # -------------------------------------------------------------- status

    @property
    def file_label(self) -> str:
        if self.path is None:
            return "New Buffer"
        return str(self.path)

    @property
    def preview_visible(self) -> bool:
        """Is the rendered preview shown (split or full-screen)?"""
        return self.layout_mode != "edit"

    @property
    def preview_kind(self) -> str:
        """Choose the preview from an explicit override or the file name."""
        if self._forced_preview_kind:
            return self._forced_preview_kind
        if self.path is not None and (
            self.path.suffix.lower() in JSON_SUFFIXES | JSONL_SUFFIXES | JSONC_SUFFIXES
        ):
            return "json"
        if self.path is not None:
            suffix = self.path.suffix.lower()
            if suffix in SOURCE_SUFFIXES:
                return SOURCE_SUFFIXES[suffix]
            name = self.path.name.lower()
            if any(
                name == base or name.startswith(base + ".") or name.endswith("." + base)
                for base in ("dockerfile", "containerfile")
            ):
                return "dockerfile"
        return "markdown"

    @property
    def json_lines_mode(self) -> bool:
        """Parse the buffer line-by-line (JSON Lines) instead of as one document?"""
        return self.path is not None and self.path.suffix.lower() in JSONL_SUFFIXES

    def _apply_preview_kind(self) -> None:
        """Show the right pane for the current file type and set highlighting."""
        pane = self.query_one("#preview-pane")
        # JSON and source viewers share the same scrollable text pane.
        pane.set_class(self.preview_kind != "markdown", "json")
        pane.remove_class("json-error")
        self._last_preview_key = None
        self._heading_src = []
        self._heading_pairs = []
        self._language_backup = None
        for language in ((self.preview_kind,), (None,)):
            try:
                self.editor.language = language[0]
                break
            except Exception:
                continue  # tree-sitter grammar missing — plain editing still works

    def _display_path(self, available: int) -> str:
        """File label for the status bar, never hiding the file name.

        Shows the full path when it fits; otherwise abbreviates $HOME to
        ~, then drops leading directories (…/dir/file.md) so that the
        basename is always fully visible.
        """
        label = self.file_label + (" *" if self.dirty else "")
        if self.path is None:
            return label
        home = str(Path.home())
        if label.startswith(home):
            label = "~" + label[len(home):]
        if len(label) <= available:
            return label
        parts = label.split("/")
        for drop in range(1, len(parts)):
            candidate = "\u2026/" + "/".join(parts[drop:])
            if len(candidate) <= available:
                return candidate
        base = parts[-1]
        if len(base) + 1 >= available:  # even the basename is too long
            return "\u2026" + base[-(available - 1):]
        return base

    def _recompute_stats(self) -> None:
        """One O(buffer) pass for the status bar and perf heuristics."""
        lines = self.editor.text.split("\n")
        self._word_count = sum(len(line.split()) for line in lines)
        self._longest_line = max((len(line) for line in lines), default=0)

    def _update_soft_wrap(self, notify: bool = True) -> None:
        """Keep the editor responsive on very large buffers.

        - A single enormous line (minified JSON, base64...) makes soft
          wrapping and the blinking cursor re-render megabytes per frame:
          both are switched off, and the text scrolls horizontally.
        - tree-sitter highlighting re-parses the whole document on every
          keystroke, which dominates on multi-hundred-KB buffers: it is
          switched off above LARGE_BUFFER_CHARS.
        Both are re-enabled (with hysteresis) once the buffer is small
        again.
        """
        longest = self._longest_line
        huge = len(self.editor.text) > LARGE_BUFFER_CHARS or longest > SOFT_WRAP_DISABLE_CHARS
        small = (
            len(self.editor.text) < LARGE_BUFFER_REENABLE_CHARS
            and longest < SOFT_WRAP_REENABLE_CHARS
        )
        if self.editor.soft_wrap and longest > SOFT_WRAP_DISABLE_CHARS:
            self.editor.soft_wrap = False
            if notify:
                self.notify("Soft wrap off (very long lines) — use ←/→ to scroll")
        elif not self.editor.soft_wrap and longest < SOFT_WRAP_REENABLE_CHARS:
            self.editor.soft_wrap = True
            if notify:
                self.notify("Soft wrap on")
        if self.editor.language is not None and huge:
            self._language_backup = self.editor.language
            self.editor.language = None
            if notify:
                self.notify("Syntax highlighting off (large file)")
        elif (
            self.editor.language is None
            and getattr(self, "_language_backup", None)
            and small
        ):
            self.editor.language = self._language_backup
            self._language_backup = None
            if notify:
                self.notify("Syntax highlighting on")
        if self.editor.cursor_blink and longest > SOFT_WRAP_DISABLE_CHARS:
            self.editor.cursor_blink = False
        elif not self.editor.cursor_blink and longest < SOFT_WRAP_REENABLE_CHARS:
            self.editor.cursor_blink = True

    def update_status(self) -> None:
        row, col = self.editor.cursor_location
        words = self._word_count
        modified = "Modified · " if self.dirty else ""
        hidden = {
            "edit": " · preview off (^P)",
            "preview": " · full preview (^P)",
        }.get(self.layout_mode, "")
        modes = []
        if self.color_mode != "truecolor":
            modes.append(self.color_mode)
        if self.ascii_mode:
            modes.append("ascii")
        mode_bits = f" · {' · '.join(modes)}" if modes else ""
        left = (
            f"nanopeek · {self.theme}"
            + (
                " · jsonl" if self.preview_kind == "json" and self.json_lines_mode
                else " · jsonc" if self.preview_kind == "json" and self.path is not None and self.path.suffix.lower() in JSONC_SUFFIXES
                else f" · {self.preview_kind}" if self.preview_kind != "markdown"
                else ""
            )
        )
        right = f"{modified}Ln {row + 1}, Col {col + 1} · {words} words{mode_bits}{hidden}"
        total = self.size.width if self.size else 80
        available = max(total - len(left) - len(right) - 2, 10)
        self.query_one("#status-left", Static).update(left)
        self.query_one("#status-center", Static).update(self._display_path(available))
        self.query_one("#status-right", Static).update(right)

    def on_resize(self) -> None:
        if hasattr(self, "editor"):
            self.update_status()

    # ------------------------------------------------------------- preview

    def schedule_preview_refresh(self, delay: float = DEBOUNCE) -> None:
        if not self.preview_visible or self._exiting:
            return
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
        self._refresh_timer = self.set_timer(delay, self._refresh_preview_now)

    def _refresh_preview_now(self) -> None:
        if self._exiting:
            return
        self.run_worker(self._update_preview(self.editor.text), exclusive=True)

    async def _update_preview(self, text: str) -> None:
        if self._exiting:
            return
        # Skip identical re-renders (scroll sync, repeated refreshes).
        key = (self.preview_kind, text, self.theme)
        if key == self._last_preview_key:
            return
        self._last_preview_key = key
        if self.preview_kind == "json":
            self._update_json_preview(text)
            return
        if self.preview_kind != "markdown":
            self._update_source_preview(text)
            return
        self._heading_src = self._build_heading_map(text)
        self._match_retries = 0
        if text and text.count("\n") + 1 > MAX_MD_PREVIEW_LINES:
            lines = text.split("\n")
            text = "\n".join(lines[:MAX_MD_PREVIEW_LINES])
            text += "\n\n*(preview truncated — the buffer is larger than the preview limit)*"
        try:
            await self.preview.update(text or "*(empty buffer — start typing Markdown)*")
        except Exception:
            pass
        self._match_heading_widgets()
        self._sync_from_editor()

    def _highlight_json(self, formatted: str) -> Text:
        """Colorize pretty-printed JSON using the active theme.

        Keys take the theme's primary color (bold), strings its success
        green, numbers the warning orange, booleans the accent color and
        null the error red; punctuation is dimmed so values stand out.
        """
        theme = self.current_theme
        styles = {
            "key": f"bold {getattr(theme, 'primary', 'cyan')}",
            "string": str(getattr(theme, "success", "green")),
            "number": str(getattr(theme, "warning", "yellow")),
            "bool": str(getattr(theme, "accent", "magenta")),
            "null": f"{getattr(theme, 'error', 'red')} italic",
            "punct": "dim",
        }
        out = Text()
        pos = 0
        for match in _JSON_TOKENS.finditer(formatted):
            out.append(formatted[pos : match.start()], styles["punct"])
            token = match.group()
            if token.lstrip().endswith(":"):
                style = styles["key"]
            elif token.startswith('"'):
                style = styles["string"]
            elif token in ("true", "false"):
                style = styles["bool"]
            elif token == "null":
                style = styles["null"]
            else:
                style = styles["number"]
            out.append(token, style)
            pos = match.end()
        out.append(formatted[pos:], styles["punct"])
        return out

    def _parse_json_records(self, text: str) -> tuple[list, str | None]:
        """Parse a stream of concatenated JSON values (JSON Lines, NDJSON,
        or several objects run together), skipping whitespace between them.
        """
        decoder = json.JSONDecoder()
        records: list = []
        idx, length = 0, len(text)
        while idx < length:
            while idx < length and text[idx] in " \t\r\n":
                idx += 1
            if idx >= length:
                break
            try:
                value, end = decoder.raw_decode(text, idx)
            except json.JSONDecodeError as exc:
                return [], f"{exc.msg}\n\nline {exc.lineno}, column {exc.colno}"
            records.append(value)
            idx = end
        return records, None

    def _show_json_error(self, message: str) -> None:
        self.query_one("#preview-pane").add_class("json-error")
        self.query_one("#preview-error", Static).update(f"[b]Invalid JSON[/b]\n\n{message}")

    def _render_json_records(self, records: list) -> None:
        parts = [json.dumps(record, indent=2, ensure_ascii=False) for record in records]
        self._set_json_static("\n\n".join(parts))

    def _set_json_static(self, formatted: str) -> None:
        """Show formatted JSON in the pane, capped for large documents."""
        total_lines = formatted.count("\n") + 1
        if total_lines > MAX_PREVIEW_LINES:
            formatted = "\n".join(formatted.split("\n")[:MAX_PREVIEW_LINES])
            formatted += (
                f"\n\n… ({total_lines - MAX_PREVIEW_LINES:,} more lines — "
                "preview truncated for speed; the buffer is untouched)"
            )
        self.query_one("#json-static", Static).update(self._highlight_json(formatted))

    def _update_json_preview(self, text: str) -> None:
        """Pretty-print and syntax-highlight the buffer as JSON (live).

        Tries the text as-is first (strict), then with JSONC comments and
        trailing commas blanked out — so .jsonc files work and commented
        .json files still get a view. .jsonl/.ndjson buffers parse
        record-by-record instead of as one document.
        """
        try:
            self._render_json_preview(text)
        except RecursionError:  # the json module recurses once per nesting level
            self._show_json_error("nested too deeply to display")

    def _render_json_preview(self, text: str) -> None:
        pane = self.query_one("#preview-pane")
        pane.remove_class("json-error")
        if not text.strip():
            self.query_one("#json-static", Static).update("(empty buffer — start typing JSON)")
            return
        candidates = [text]
        stripped = _strip_jsonc(text)
        if stripped != text:
            candidates.append(stripped)
        if self.json_lines_mode:
            error = None
            for candidate in candidates:
                records, error = self._parse_json_records(candidate)
                if error is None:
                    self._render_json_records(records)
                    return
            self._show_json_error(error)
            return
        parsed = None
        last_exc: json.JSONDecodeError | None = None
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                break
            except json.JSONDecodeError as exc:
                last_exc = exc
        if parsed is None:
            for candidate in candidates:  # multi-value file: view as records
                records, error = self._parse_json_records(candidate)
                if error is None:
                    self._render_json_records(records)
                    return
            assert last_exc is not None
            self._show_json_error(f"{last_exc.msg}\n\nline {last_exc.lineno}, column {last_exc.colno}")
            return
        formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
        self._set_json_static(formatted)

    def _update_source_preview(self, text: str) -> None:
        """Validate config syntax and highlight the original, unmodified source.

        YAML composition checks syntax and aliases without constructing objects
        or expanding aliases. Dockerfiles are displayed, never executed.
        """
        kind = self.preview_kind
        pane = self.query_one("#preview-pane")
        pane.remove_class("json-error")
        try:
            if kind == "yaml":
                for _ in yaml.compose_all(text, Loader=yaml.SafeLoader):
                    pass
            elif kind == "toml":
                tomllib.loads(text)
        except (yaml.YAMLError, tomllib.TOMLDecodeError, RecursionError) as exc:
            pane.add_class("json-error")
            # Parser messages may contain source text: render it literally.
            self.query_one("#preview-error", Static).update(
                Text(f"Invalid {kind.upper()}\n\n{exc}")
            )
            return
        if not text.strip():
            self.query_one("#json-static", Static).update(f"(empty buffer — start typing {kind})")
            return
        lines = text.split("\n")
        visible = "\n".join(lines[:MAX_PREVIEW_LINES])
        syntax = Syntax(
            visible, kind,
            theme="monokai" if self.current_theme.dark else "friendly",
            background_color="default",
        )
        highlighted = syntax.highlight(visible)
        if len(lines) > MAX_PREVIEW_LINES:
            highlighted.append(
                f"\n\n… ({len(lines) - MAX_PREVIEW_LINES:,} more lines — "
                "preview truncated for speed; the buffer is untouched)",
                style="dim",
            )
        self.query_one("#json-static", Static).update(highlighted)

    def _right_widget(self):
        """The scrollable widget currently shown on the right side."""
        return self.json_view if self.preview_kind != "markdown" else self.preview

    # ------------------------------------------------- markdown scroll sync

    def _build_heading_map(self, text: str) -> list[tuple[int, int]]:
        """Source lines of every heading: [(line, level), ...] in order."""
        if _MD_PARSER is None:
            return []
        headings = []
        try:
            tokens = _MD_PARSER.parse(text)
        except Exception:
            return []
        for i, token in enumerate(tokens):
            if token.type == "heading_open" and token.map:
                headings.append((token.map[0], int(token.tag[1:])))
        return headings

    def _match_heading_widgets(self) -> None:
        """Pair parsed headings with the preview's rendered heading widgets.

        Both lists are in document order, so a sequential zip aligns them;
        the preview's line cap simply leaves late headings unpaired. The
        Markdown widget mounts its blocks over several refresh cycles, so
        matching retries briefly when no heading widgets exist yet and
        also re-runs lazily from _anchor_preview_y.
        """
        if self.preview_kind != "markdown" or not self._heading_src:
            self._heading_pairs = []
            return
        if self._exiting:
            return
        widgets = [
            w for w in self.preview.children
            if re.fullmatch(r"MarkdownH[1-6]", type(w).__name__)
        ]
        if not widgets:
            if self._match_retries < 8:
                self._match_retries += 1
                self.call_after_refresh(self._match_heading_widgets)
            return
        self._match_retries = 0
        self._heading_pairs = [
            (src_line, widget)
            for (src_line, _level), widget in zip(self._heading_src, widgets)
        ]

    def _virtual_y(self, widget) -> float:
        """A child widget's position inside the preview's scroll content."""
        return widget.region.y - self.preview.region.y + self.preview.scroll_y

    def _anchor_preview_y(self, source_line: int) -> float | None:
        """Preview scroll position matching a source line, or None.

        Scrolls section-aware: the source line's heading widget is the
        anchor, and the position is interpolated between that heading and
        the next one — so top-heavy prose and compact code blocks no
        longer drift apart from their source.
        """
        pairs = self._heading_pairs
        if not pairs and self._heading_src:
            self._match_heading_widgets()  # widgets may have mounted since
            pairs = self._heading_pairs
        if not pairs:
            return None
        lines = [src for src, _ in pairs]
        idx = bisect_right(lines, source_line) - 1
        if idx < 0:
            idx = 0
        src, widget = pairs[idx]
        wv = self._virtual_y(widget)
        if idx + 1 < len(pairs):
            nsrc, nwidget = pairs[idx + 1]
            nv = self._virtual_y(nwidget)
            span = max(1, nsrc - src)
            frac = min(max((source_line - src) / span, 0.0), 1.0)
        else:
            # Last rendered section: it runs to the next heading the preview
            # left unrendered (or the end of the buffer) in the source, and to
            # the end of the rendered content on the right.
            later = [line for line, _level in self._heading_src if line > src]
            end_src = later[0] if later else self._buffer_line_count()
            end_v = self._preview_content_height()
            if end_src is not None and end_v is not None and end_src > src and end_v > wv:
                frac = min(max((source_line - src) / (end_src - src), 0.0), 1.0)
                return wv + (end_v - wv) * frac
            # Not laid out yet: extend at the previous section's rows per line.
            if idx > 0:
                psrc, pwidget = pairs[idx - 1]
                per_line = max(0.0, (wv - self._virtual_y(pwidget)) / max(1, src - psrc))
            else:
                per_line = 1.0
            return wv + max(0, source_line - src) * per_line
        return wv + (nv - wv) * frac

    def _buffer_line_count(self) -> int | None:
        try:
            return self.editor.text.count("\n") + 1
        except Exception:  # not mounted yet
            return None

    def _preview_content_height(self) -> float | None:
        try:
            return float(self.preview.virtual_size.height)
        except Exception:  # not mounted yet
            return None

    def _sync_from_editor(self) -> None:
        """Editor scrolled → move the right pane to the same content."""
        if self._scroll_syncing:
            return
        right = self._right_widget()
        if right.max_scroll_y <= 0:
            return
        if self.preview_kind == "markdown":
            target = self._anchor_preview_y(self.editor.cursor_location[0])
            if target is None:
                if self.editor.max_scroll_y <= 0:
                    return
                target = (
                    self.editor.scroll_offset.y / self.editor.max_scroll_y * right.max_scroll_y
                )
        else:
            if self.editor.max_scroll_y <= 0:
                return
            target = (
                self.editor.scroll_offset.y / self.editor.max_scroll_y * right.max_scroll_y
            )
        self._scroll_syncing = True
        try:
            target = min(max(target, 0.0), right.max_scroll_y)
            if abs(right.scroll_y - target) >= 1.0:
                right.scroll_to(y=target, animate=False)
        finally:
            self._scroll_syncing = False

    def _sync_from_right(self) -> None:
        """Right pane scrolled (mouse wheel, keys) → move the editor along."""
        if self._scroll_syncing:
            return
        right = self._right_widget()
        ed_max, right_max = self.editor.max_scroll_y, right.max_scroll_y
        if ed_max <= 0 or right_max <= 0:
            return
        self._scroll_syncing = True
        try:
            target = right.scroll_offset.y / right_max * ed_max
            if abs(self.editor.scroll_y - target) >= 1.0:
                self.editor.scroll_to(y=target, animate=False)
        finally:
            self._scroll_syncing = False

    # -------------------------------------------------------------- saving

    def _write_to(self, target: Path) -> bool:
        try:
            target.write_text(self.editor.text, encoding="utf-8")
        except OSError as exc:
            self.notify(f"Error writing {target}: {exc}", severity="error")
            return False
        self.dirty = False
        lines = self.editor.text.count("\n") + 1
        self.notify(f"Wrote {lines} lines to {target}")
        self.update_status()
        return True

    def _do_save(self) -> bool:
        assert self.path is not None
        return self._write_to(self.path)

    def action_save(self) -> None:
        if self.path is None:
            self._prompt_save_as(exit_after=False)
        else:
            self._do_save()
            if self._exit_after_save:
                self.exit()

    def action_write_out(self) -> None:
        self._prompt_save_as(exit_after=False)

    def _prompt_save_as(self, exit_after: bool) -> None:
        self._exit_after_save = exit_after
        default = str(self.path) if self.path is not None else ""
        self.push_screen(SaveScreen(default), self._after_save_as)

    def _after_save_as(self, result: str | None) -> None:
        if result is None:
            self._exit_after_save = False
            if self._exiting:  # cancelled the save that ^X asked for: keep editing
                self._resume_after_exit_cancel()
            return
        candidate = Path(result).expanduser()
        if self._write_to(candidate):
            self.path = candidate  # only adopt the name after a successful write
            self._apply_preview_kind()  # saving under another file type switches the preview
            self._update_soft_wrap(notify=False)
            self.update_status()
            self.schedule_preview_refresh(delay=0.01)
            if self._exit_after_save:
                self.exit()
                return
        else:
            if self._exiting:
                self._resume_after_exit_cancel()
        self._exit_after_save = False

    # --------------------------------------------------------------- exit

    def _begin_shutdown(self) -> None:
        """Drop expensive preview state so exit returns promptly.

        Unmounting hundreds of rendered preview blocks and draining queued
        re-renders is what made ^X freeze for seconds on large documents;
        cancel all of it up front. If the user cancels the exit, the
        preview is simply rebuilt from the buffer.
        """
        self._exiting = True
        for timer in (self._refresh_timer, self._copy_notify_timer):
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass
        try:
            self.workers.cancel_node(self)
        except Exception:
            pass
        # The source pane contains one reusable Static. Keep it mounted so
        # cancelling an exit can render JSON/config previews again.
        for pane in (self.preview,):
            try:
                pane.remove_children()
            except Exception:
                pass
        self._heading_pairs = []

    def _resume_after_exit_cancel(self) -> None:
        self._exiting = False
        self._last_preview_key = None  # force a full preview rebuild
        if self.preview_visible:
            self.schedule_preview_refresh(delay=0.05)

    def action_exit(self) -> None:
        self._begin_shutdown()
        if self.dirty:
            self.push_screen(ConfirmExitScreen(), self._handle_exit_choice)
        else:
            self.exit()

    def _handle_exit_choice(self, choice: str | None) -> None:
        if choice == "discard":
            self.exit()
        elif choice == "save":
            if self.path is None:
                self._prompt_save_as(exit_after=True)
            elif self._do_save():
                self.exit()
            else:
                self._resume_after_exit_cancel()
        else:
            self._resume_after_exit_cancel()

    # ------------------------------------------------------------ editing

    def action_cut_line(self) -> None:
        lines = self.editor.text.split("\n")
        row = self.editor.cursor_location[0]
        if not lines or row >= len(lines):
            return
        now = time.monotonic()
        if now - self._last_cut_at > 1.0:  # nano: consecutive ^K cuts append
            self._cut_buffer = []
        self._last_cut_at = now
        self._cut_buffer.append(lines[row])
        self.editor.action_delete_line()

    def action_uncut(self) -> None:
        if not self._cut_buffer:
            return
        row = self.editor.cursor_location[0]
        self.editor.insert("\n".join(self._cut_buffer) + "\n", (row, 0))

    def action_page_up(self) -> None:
        self.editor.action_cursor_page_up()

    def action_page_down(self) -> None:
        self.editor.action_cursor_page_down()

    def action_line_start(self) -> None:
        self.editor.action_cursor_line_start()

    def action_line_end(self) -> None:
        self.editor.action_cursor_line_end()

    # ------------------------------------------------------------- search

    def action_where_is(self) -> None:
        self.push_screen(SearchScreen())

    def find_and_jump(self, needle: str) -> None:
        """Move the cursor to the next occurrence of needle (wrapping)."""
        if not needle:
            return
        text = self.editor.text
        row, col = self.editor.cursor_location
        # A match at the cursor counts; after a jump the cursor sits at the end
        # of the selected match, so searching again moves on to the next one.
        start = sum(len(line) + 1 for line in text.split("\n")[:row]) + col
        pos = text.find(needle, start)
        wrapped = False
        if pos == -1:
            pos = text.find(needle)
            wrapped = True
        if pos == -1:
            self.notify(f'"{needle}" not found', severity="warning")
            return
        if wrapped:
            self.notify("Search wrapped to top")
        prefix = text[:pos]
        match_row = prefix.count("\n")
        match_col = pos - (prefix.rfind("\n") + 1)
        self.editor.move_cursor((match_row, match_col))
        try:
            self.editor.selection = (
                (match_row, match_col),
                (match_row, match_col + len(needle)),
            )
        except Exception:
            pass

    # ------------------------------------------------------------- display

    def action_cycle_theme(self) -> None:
        current = self.theme if self.theme in THEME_CYCLE else THEME_CYCLE[0]
        nxt = THEME_CYCLE[(THEME_CYCLE.index(current) + 1) % len(THEME_CYCLE)]
        self.theme = nxt
        self.notify(f"Theme: {nxt}")
        self.update_status()

    def action_toggle_dark(self) -> None:
        if self.theme in THEME_PAIRS:
            target = THEME_PAIRS[self.theme]
        else:
            target = "textual-light" if self.current_theme.dark else "textual-dark"
        self.theme = target
        self.notify(f"Theme: {target}")
        self.update_status()

    def action_cycle_color(self) -> None:
        nxt = COLOR_MODES[(COLOR_MODES.index(self.color_mode) + 1) % len(COLOR_MODES)]
        self._apply_color_mode(nxt)

    def _apply_color_mode(self, mode: str) -> None:
        self.color_mode = mode
        if mode == "ansi":
            if self.theme not in ("ansi-dark", "ansi-light"):
                self._theme_before_ansi = self.theme
            target = "ansi-light" if not self.current_theme.dark else "ansi-dark"
            self.theme = target
            self.ansi_color = True  # keep native 16-color ANSI codes
        else:
            if self._theme_before_ansi and self.theme in ("ansi-dark", "ansi-light"):
                self.theme = self._theme_before_ansi
            self._theme_before_ansi = None
            self.ansi_color = False
        has_no_color = any(isinstance(f, NoColor) for f in self._filters)
        if mode == "mono" and not has_no_color:
            self._filters.append(NoColor())
        elif mode != "mono" and has_no_color:
            self._filters = [f for f in self._filters if not isinstance(f, NoColor)]
        self.refresh()
        self.notify(f"Colors: {mode}")
        self.update_status()

    def action_toggle_ascii(self) -> None:
        self._set_ascii_mode(not self.ascii_mode)

    def _set_ascii_mode(self, on: bool) -> None:
        """Transliterate everything the app draws to plain ASCII.

        Hooked at the driver's single write() choke point, so the preview's
        bullets, table borders etc. are covered as well as our own chrome.
        Replacements are width-preserving 1:1 so layouts stay aligned.
        """
        self.ascii_mode = on
        driver = self._driver
        if driver is not None:
            try:
                if on:
                    if getattr(driver, "_plain_write", None) is None:
                        plain = driver.write
                        driver._plain_write = plain
                        driver.write = lambda data, _plain=plain: _plain(
                            data.translate(ASCII_TABLE)
                        )
                else:
                    plain = getattr(driver, "_plain_write", None)
                    if plain is not None:
                        driver.write = plain
                        driver._plain_write = None
            except Exception:
                pass
        self.notify(f"ASCII output: {'on' if on else 'off'}")
        self.update_status()

    # ------------------------------------------------------------ dialogs

    def _theme_changed(self) -> None:
        if self.preview_kind != "markdown":
            self.schedule_preview_refresh(delay=0.01)

    def _auto_copy_selection(self) -> None:
        """Copy the current selection to the clipboard whenever it changes.

        Fires continuously while the mouse drag grows the selection (each
        copy is cheap and leaves the latest text on the clipboard); the
        "Copied" toast is debounced until the mouse settles.
        """
        text = self.editor.selected_text
        if not text:
            return
        try:
            self.copy_to_clipboard(text)
        except Exception:
            return
        if self._copy_notify_timer is not None:
            self._copy_notify_timer.stop()
        self._copy_notify_timer = self.set_timer(
            COPY_NOTIFY_DEBOUNCE, self._notify_copied
        )

    def _notify_copied(self) -> None:
        text = self.editor.selected_text
        if text:
            self.notify(f"Copied {len(text)} characters")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_toggle_preview(self) -> None:
        """Cycle ^P: split -> full-screen rendered Markdown -> editor only."""
        self.layout_mode = LAYOUT_MODES[
            (LAYOUT_MODES.index(self.layout_mode) + 1) % len(LAYOUT_MODES)
        ]
        main = self.query_one("#main")
        main.remove_class("no-preview", "preview-only")
        if self.layout_mode == "preview":
            main.add_class("preview-only")
        elif self.layout_mode == "edit":
            main.add_class("no-preview")
        if self.preview_visible:
            self.schedule_preview_refresh(delay=0.01)
        focus_target = (
            self.json_view if self.layout_mode == "preview" and self.preview_kind != "markdown"
            else self.preview if self.layout_mode == "preview"
            else self.editor
        )
        focus_target.focus()
        self.notify(f"View: {self.layout_mode}")
        self.update_status()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nanopeek",
        description="nano-style terminal editor with Markdown, JSON, YAML, TOML and Dockerfile previews",
    )
    parser.add_argument("file", nargs="?", help="file to open (created on save if missing)")
    parser.add_argument("--theme", metavar="NAME", help="theme to start with (see --themes)")
    parser.add_argument("--themes", action="store_true", help="list available themes and exit")
    parser.add_argument(
        "--color", choices=COLOR_MODES, default="truecolor",
        help="color mode: truecolor (default), ansi (16 colors), mono (no color)",
    )
    parser.add_argument("--ascii", action="store_true", help="ASCII-only output for legacy terminals")
    parser.add_argument("--json", dest="preview_kind", action="store_const", const="json",
                        help="force the JSON formatter preview (default: by file extension)")
    parser.add_argument("--markdown", dest="preview_kind", action="store_const", const="markdown",
                        help="force the Markdown preview (default: by file extension)")
    for kind in ("yaml", "toml", "dockerfile"):
        parser.add_argument(f"--{kind}", dest="preview_kind", action="store_const", const=kind,
                            help=f"force the {kind} source preview")
    parser.add_argument("--version", action="version", version=f"nanopeek {__version__}")
    args = parser.parse_args(argv)

    try:
        from textual.theme import BUILTIN_THEMES
    except ImportError:
        BUILTIN_THEMES = {}
    if args.themes:
        for name in sorted(BUILTIN_THEMES):
            print(name)
        return 0
    if args.theme is not None and args.theme not in BUILTIN_THEMES:
        print(f"nanopeek: unknown theme {args.theme!r} (try --themes)", file=sys.stderr)
        return 2

    path = Path(args.file).expanduser() if args.file else None
    if path is not None and path.is_dir():
        print(f"nanopeek: {path} is a directory", file=sys.stderr)
        return 1

    theme = args.theme or ("ansi-dark" if args.color == "ansi" else None)
    NanoPeek(
        path,
        theme=theme,
        color=args.color,
        ascii_mode=args.ascii,
        preview_kind=args.preview_kind,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
