#!/usr/bin/env python3
"""Capture Markdown and JSON screenshots with neutral sample-file labels.

Writes docs/screenshot{,-json}.{svg,png} (PNG requires inkscape
or ImageMagick's convert on PATH).

Run:  python3 scripts/screenshot.py
      python3 scripts/screenshot.py --json
"""
from __future__ import annotations

import asyncio
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from nanopeek import NanoPeek


async def capture(sample: str, basename: str) -> None:
    app = NanoPeek(REPO / sample)
    # The constructor loads the real file; display only its portable name.
    app.path = Path(sample)
    async with app.run_test(size=(120, 34)):
        await asyncio.sleep(1.0)
        out = REPO / "docs" / f"{basename}.svg"
        out.parent.mkdir(exist_ok=True)
        out.write_text(app.export_screenshot(), encoding="utf-8")
        print(f"wrote {out}")

    png = out.with_suffix(".png")
    for cmd in (
        ["inkscape", str(out), "--export-type=png", "--export-filename", str(png), "-w", "1400"],
        ["convert", "-density", "120", str(out), "-resize", "1400x", str(png)],
    ):
        if shutil.which(cmd[0]):
            try:
                result = subprocess.run(cmd, capture_output=True, timeout=20)
            except subprocess.TimeoutExpired:
                continue
            if result.returncode == 0:
                print(f"wrote {png}")
                return
    print("no SVG->PNG converter found (inkscape/convert); SVG only", file=sys.stderr)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="capture the JSON sample")
    args = parser.parse_args()
    # Documentation screenshots should retain colors even in a NO_COLOR shell.
    os.environ.pop("NO_COLOR", None)
    if args.json:
        await capture("sample.json", "screenshot-json")
    else:
        await capture("sample.md", "screenshot")


if __name__ == "__main__":
    asyncio.run(main())
