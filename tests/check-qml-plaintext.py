#!/usr/bin/env python3
"""Enforce the marketplace reviewer's QML plain-text rule.

Every `Text` / `Label` block must declare `textFormat: Text.PlainText`.
Qt's default format is `AutoText`, which interprets an HTML subset in the
string it renders, and several strings in this plugin come from outside the
plugin: session titles written by Hermes, task titles from a kanban board,
model ids from a provider, workspace paths, and subprocess output. A `<`
in any of them is markup to Qt, not a character.

The marketplace maintainers treat this as their most-cited manual finding,
and it cost at least one plugin a full review round (omacom/omarchy-plugin-marketplace#7330).
`Text.PlainText` on every one of these items removes the class outright.

This walks brace depth rather than counting `Text {` lines against
`textFormat:` lines: a count cannot tell which block is missing the
declaration, and it silently passes when a block adds a second `textFormat`
to compensate. Reported offenders carry a line number and the `text:`
expression, so the fix is obvious without opening the file.

Exit 0 when clean, 1 when any block is unmarked, 2 on a usage error.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# A block opener: `Text {`, `Label {`, or `component Name: Text {`.
OPENER = re.compile(r"^(?P<indent>\s*)(?:component\s+\w+\s*:\s*)?(?P<kind>Text|Label)\s*\{")
TEXT_EXPR = re.compile(r"^\s*text\s*:")


def find_unmarked_blocks(source: str) -> list[tuple[int, str]]:
    """Return (line_number, first_text_line) for every unmarked Text/Label block.

    Braces inside strings and comments would fool a naive counter, so those are
    stripped before the depth walk. The text expression is captured so the
    report can quote it without the reader opening the file.
    """
    lines = source.split("\n")
    offenders: list[tuple[int, str]] = []
    index = 0

    while index < len(lines):
        match = OPENER.match(lines[index])
        if not match:
            index += 1
            continue

        start = index
        depth = 0
        marked = False
        text_line = ""

        while index < len(lines):
            stripped = strip_strings_and_comments(lines[index])
            depth += stripped.count("{") - stripped.count("}")
            if "textFormat:" in stripped:
                marked = True
            if not text_line and TEXT_EXPR.match(lines[index]):
                text_line = lines[index].strip()
            if depth <= 0 and index > start:
                break
            if depth <= 0 and index == start:
                break
            index += 1

        if not marked:
            offenders.append((start + 1, text_line))
        index += 1

    return offenders


def strip_strings_and_comments(line: str) -> str:
    """Remove string literals and line comments so braces inside them do not count."""
    out = []
    i = 0
    quote = ""
    while i < len(line):
        char = line[i]
        if quote:
            if char == "\\":
                i += 2
                continue
            if char == quote:
                quote = ""
            i += 1
            continue
        if char in "\"'":
            quote = char
            i += 1
            continue
        if line.startswith("//", i):
            break
        out.append(char)
        i += 1
    return "".join(out)


def main(argv: list[str]) -> int:
    roots = [Path(arg) for arg in argv[1:]] or [Path(".")]
    files: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix == ".qml":
            files.append(root)
        elif root.is_dir():
            files.extend(sorted(root.glob("*.qml")))
        else:
            print(f"check-qml-plaintext: no .qml found at {root}", file=sys.stderr)
            return 2

    failures = 0
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"check-qml-plaintext: cannot read {path}: {exc}", file=sys.stderr)
            return 2
        for line_number, text_line in find_unmarked_blocks(source):
            failures += 1
            print(f"{path}:{line_number}: Text/Label block without textFormat: Text.PlainText")
            if text_line:
                print(f"    {text_line[:140]}")

    if failures:
        print(
            f"\n{failures} block(s) render external content through Qt's default AutoText.",
            file=sys.stderr,
        )
        print("Add `textFormat: Text.PlainText` to each block above.", file=sys.stderr)
        return 1

    print(f"ok: {len(files)} file(s), every Text/Label declares textFormat")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
