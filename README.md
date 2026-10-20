# nanopeek

A [`nano`](https://www.nano-editor.org/)-style terminal editor with a
**live, properly rendered preview** — edit on the left, watch it render on
the right. For Markdown files the right pane shows the rendered document;
for `.json` files it becomes a live **JSON formatter and validator**.
YAML, TOML, and Dockerfiles get a **syntax-highlighted source preview**;
YAML and TOML also show live syntax errors.

![nanopeek screenshot](https://raw.githubusercontent.com/grobelr/nanopeek/main/docs/screenshot.png)

## What it does

- **Split view** — raw text on the left, rendered result on the right:
  for Markdown, headings, **bold**/*italic*, lists (incl. task lists), tables,
  fenced code blocks with syntax highlighting, block quotes, horizontal
  rules, links; for `.json`, a pretty-printed, syntax-highlighted formatter
  that doubles as a live validator (parse errors show the offending line and
  column as you type).
- **JSON mode is automatic** — open any `.json`, `.jsonl`, `.ndjson` or
  `.jsonc` file (or force it with `--json`; `--markdown` forces the
  Markdown view). The editor switches to JSON syntax highlighting too, and
  saving a buffer out as one of these switches the preview on the fly.
  JSON Lines files render as one pretty-printed, colored record each;
  JSONC comments (`//`, `/* */`) and trailing commas are tolerated —
  commented `.json` files get the same leniency. Broken input shows the
  exact line and column.

![JSON formatter screenshot](https://raw.githubusercontent.com/grobelr/nanopeek/main/docs/screenshot-json.png)
- **Configuration viewers** — `.yaml`/`.yml` (including Docker Compose)
  and `.toml` files display highlighted source with live syntax validation.
  Comments, ordering, spacing, and YAML document separators stay intact.
  Dockerfiles and Containerfiles are highlighted too, including names such
  as `Dockerfile.dev`, `Containerfile.prod`, and `build.Dockerfile`.
  Dockerfile viewing does not run builds or validate Docker instructions;
  Compose validation checks YAML syntax, not the Compose schema.
  These previews show at most 2,000 lines without changing the buffer.
- **Live updates** — the preview re-renders ~200 ms after you stop typing.
  The two panes scroll **together, in both directions** (mouse wheel,
  keys, either side). For Markdown the sync is **section-aware**: the
  preview anchors to the rendered heading matching your cursor's source
  section (via markdown-it), so long documents with code blocks and
  tables stay aligned instead of drifting; heading-less documents and
  the JSON views fall back to proportional scrolling.
- **Scales to large files** — on big buffers the app trims itself
  automatically: the JSON formatter renders at most 2,000 lines and the
  Markdown preview at most 12,000 (with a notice; your file is never
  touched), and for enormous single lines (minified JSON) or
  multi-hundred-KB buffers it turns off soft wrap, cursor blink and
  tree-sitter highlighting — everything comes back once the document is
  small again.
- **nano keybindings** — feel right at home: `^O` write out, `^S` save, `^X`
  exit (asks to save a modified buffer), `^K`/`^U` cut line/uncut, `^W` where
  is, `^Y`/`^V` page up/down, `^A`/`^E` line start/end, `^G` help.
- **Markdown syntax highlighting in the editor** via tree-sitter.
- **Auto-copy on select** — highlighting text with the mouse (or
  Shift+arrows) copies it to the system clipboard immediately.
- **Toggleable preview** — `^P` cycles between the split view, a
  full-screen rendered Markdown page (read mode), and editor-only.
- **Display modes** — cycle 12 themes including dark and light (`Alt+T`),
  flip dark/light (`Alt+D`), switch color depth between truecolor, ANSI
  (16 colors) and mono (`Alt+C`), and force ASCII-only output for legacy
  terminals (`Alt+A`). All also available as CLI flags.

## Install

Requires Python 3.9+. nanopeek is on [PyPI](https://pypi.org/project/nanopeek/):

    pipx install nanopeek

or with [uv](https://docs.astral.sh/uv/):

    uv tool install nanopeek    # install the command
    uvx nanopeek README.md      # or run it once without installing

`pip install nanopeek` works too. To install from a clone, for development:

    pip install -e .

That installs the `nanopeek` command and its dependencies, including Textual,
tree-sitter grammars, PyYAML, and (on Python older than 3.11) `tomli`.
YAML and TOML editor highlighting uses their installed tree-sitter grammars;
the preview remains highlighted if an editor grammar is unavailable.

## Usage

    nanopeek                # new, unsaved buffer
    nanopeek note.md        # open (or create on save) a file
    nanopeek --theme nord   # start with a specific theme (--themes lists all)
    nanopeek --color ansi   # 16-color mode (truecolor | ansi | mono)
    nanopeek --ascii        # ASCII-only output for legacy terminals
    nanopeek data.json      # JSON formatter/validator preview
    nanopeek events.jsonl   # JSON Lines: one pretty record per line
    nanopeek notes.txt --json  # force the JSON view for any file
    nanopeek compose.yaml     # YAML source preview and syntax validation
    nanopeek pyproject.toml   # TOML source preview and syntax validation
    nanopeek Dockerfile       # Dockerfile source preview
    nanopeek config --yaml    # force YAML for a file without an extension
    nanopeek config --toml    # force TOML
    nanopeek build --dockerfile  # force Dockerfile highlighting

| Key             | Action                                       |
|-----------------|----------------------------------------------|
| `^G`            | Get help                                     |
| `^O`            | Write out (save, always prompting for name)  |
| `^S`            | Save                                         |
| `^X`            | Exit (prompts if the buffer is modified)     |
| `^W`            | Where is — Enter jumps to next match         |
| `^K` / `^U`     | Cut line (consecutive cuts append) / uncut   |
| `^Y` / `^V`     | Previous / next page                         |
| `^A` / `^E`     | Beginning / end of line                      |
| `^P`            | Cycle view: split / full rendered page / editor only |
| `Alt+T` / `Alt+D` | Cycle themes / toggle dark-light           |
| `Alt+C`         | Cycle color mode: truecolor / ansi / mono    |
| `Alt+A`         | Toggle ASCII-only output                     |
| `Tab`           | Switch focus between editor and preview      |
| `Ctrl+Shift+X/C/V` | Cut / copy / paste in the editor          |
| `Ctrl+Shift+P`  | Textual command palette                      |

## Development

Run the headless smoke tests (no TTY needed):

    python3 tests/smoke_test.py

Run the exhaustive keyboard and save/exit-flow tests (32 checks — every
keybinding in both focus panes, and every save / no-save / exit path
verified against the real filesystem, content and mtime):

    python3 tests/keyboard_test.py

Run the JSON formatter-mode tests:

    python3 tests/json_test.py

Run the YAML, TOML, and Dockerfile viewer tests:

    python3 tests/config_test.py

Regenerate the screenshot:

    python3 scripts/screenshot.py

## License

MIT — see [LICENSE](https://github.com/grobelr/nanopeek/blob/main/LICENSE).
