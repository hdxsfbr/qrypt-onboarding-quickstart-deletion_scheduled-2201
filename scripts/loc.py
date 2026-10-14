#!/usr/bin/env python3
"""Count quickstart lines of code under the brief's rule: exclude comments and imports.

Counted: every non-blank physical line that is not a comment, not part of a
module docstring, and not an import (`import ...`, `from ... import ...`, or a
line whose only statement is a `require(...)`).

Usage:
  scripts/loc.py FILE [FILE ...] [--max 25] [--max-width N] [--show]
Exit code 1 if any file exceeds --max, or (with --max-width) has any physical line
longer than N characters. The width check counts every line, comments included.
"""
import argparse
import pathlib
import re
import sys

IMPORT = re.compile(r"^\s*(import\s|from\s+\S+\s+import\s|(const|let|var)\s+.+=\s*require\()")


def counted_lines(path):
    text = pathlib.Path(path).read_text()
    lines = text.splitlines()
    py = path.endswith(".py")
    out, in_block, in_doc = [], False, False
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if in_block:
            in_block = "*/" not in s
            continue
        if in_doc:
            in_doc = not (s.endswith('"""') or s.endswith("'''"))
            continue
        if not s or s.startswith(("//", "#")):
            continue
        if not py and s.startswith("/*"):
            in_block = "*/" not in s
            continue
        if py and not out and s.startswith(('"""', "'''")):
            in_doc = not (len(s) > 3 and s.endswith(s[:3]))
            continue
        if IMPORT.match(line):
            continue
        out.append((i, line))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--max", type=int, default=25)
    ap.add_argument("--max-width", type=int, default=None, metavar="N",
                    help="also fail on any physical line longer than N characters (all lines)")
    ap.add_argument("--show", action="store_true", help="print the counted lines")
    a = ap.parse_args()
    worst, too_wide = 0, 0
    for f in a.files:
        counted = counted_lines(f)
        worst = max(worst, len(counted))
        flag = "OK " if len(counted) <= a.max else "OVER"
        print(f"{flag} {len(counted):3d} / {a.max}  {f}")
        if a.show:
            for n, line in counted:
                print(f"    {n:3d}: {line}")
        if a.max_width is not None:
            for n, line in enumerate(pathlib.Path(f).read_text().splitlines(), 1):
                if len(line) > a.max_width:
                    too_wide += 1
                    print(f"WIDE {len(line):3d} > {a.max_width}  {f}:{n}")
    return 0 if worst <= a.max and not too_wide else 1


if __name__ == "__main__":
    sys.exit(main())
