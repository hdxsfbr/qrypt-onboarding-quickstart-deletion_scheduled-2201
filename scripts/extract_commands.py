#!/usr/bin/env python3
"""List the documented, testable command blocks of one or more Markdown pages.

A fenced block is testable when its info string carries a `test` attribute:

    ```sh {test="true"}      run by tests/test_docs_commands.py under sh   (kind "sh")
    ```powershell {test="pwsh"}   run by `scripts/ci_checks.sh docs-pwsh`  (kind "pwsh")

Output is a JSON list in page order (files in the order given), one object per block:

    file    the path as given
    index   0-based position among the blocks printed for that file
    kind    "sh" or "pwsh"
    lang    the fence language word ("sh", "powershell", ...), possibly empty
    line    1-based line number of the opening fence
    tab     name of the enclosing {{% qs-tab name="..." %}} region, or null
    group   id of the enclosing {{< qs-tabs id="..." >}} group, or null
    code    the block body, with the fence indentation removed, ending in a newline

Usage: scripts/extract_commands.py FILE... [--kind sh|pwsh]
"""
import argparse
import json
import re
import sys
from pathlib import Path

FENCE_OPEN = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
ATTRS = re.compile(r"""([A-Za-z_][\w.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'}]+))""")
SHORTCODE = re.compile(r"\{\{[%<]\s*(/?)\s*(qs-tabs|qs-tab)\b(.*?)[%>]\}\}")
KINDS = {"true": "sh", "pwsh": "pwsh"}


def parse_info(info):
    """('sh {test="true"}') -> ('sh', {'test': 'true'}). The attribute block is optional."""
    info = info.strip()
    brace = info.find("{")
    head, tail = (info, "") if brace < 0 else (info[:brace], info[brace:])
    words = head.replace(",", " ").split()
    attrs = {m.group(1): next(g for g in m.groups()[1:] if g is not None)
             for m in ATTRS.finditer(tail)}
    return (words[0] if words else ""), attrs


def extract(text, path=""):
    """All testable blocks of one Markdown text, in page order."""
    blocks, tab_stack, group_stack = [], [], []
    fence = None  # (char, length, indent, start_line, kind, lang, body lines)
    for number, line in enumerate(text.splitlines(), 1):
        if fence:
            char, length, indent, start, kind, lang, body, tab, group = fence
            stripped = line.strip()
            if stripped and set(stripped) == {char} and len(stripped) >= length \
                    and len(line) - len(line.lstrip(" ")) <= 3:
                if kind:
                    code = "".join(f"{b}\n" for b in body)
                    blocks.append({"kind": kind, "lang": lang, "line": start, "tab": tab,
                                   "group": group, "code": code})
                fence = None
            else:
                body.append(line[indent:] if line[:indent].strip() == "" else line.lstrip())
            continue
        opened = FENCE_OPEN.match(line)
        if opened and not (opened.group(2)[0] == "`" and "`" in opened.group(3)):
            lang, attrs = parse_info(opened.group(3))
            kind = KINDS.get(attrs.get("test"))
            fence = (opened.group(2)[0], len(opened.group(2)), len(opened.group(1)), number,
                     kind, lang, [], tab_stack[-1] if tab_stack else None,
                     group_stack[-1] if group_stack else None)
            continue
        for shortcode in SHORTCODE.finditer(line):
            closing, name, args = shortcode.groups()
            stack, key = (tab_stack, "name") if name == "qs-tab" else (group_stack, "id")
            if closing:
                if stack:
                    stack.pop()
            else:
                value = {m.group(1): next(g for g in m.groups()[1:] if g is not None)
                         for m in ATTRS.finditer(args)}.get(key, "")
                stack.append(value)
    if fence and fence[4]:
        raise ValueError(f"{path}:{fence[3]}: unterminated {{test}} fence")
    for index, block in enumerate(blocks):
        block["index"] = index
    return blocks


def extract_files(paths, kind=None):
    out = []
    for path in paths:
        index = 0
        for block in extract(Path(path).read_text(encoding="utf-8"), str(path)):
            if kind and block["kind"] != kind:
                continue
            out.append({"file": str(path), **block, "index": index})
            index += 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", metavar="FILE")
    ap.add_argument("--kind", choices=sorted(set(KINDS.values())), help="only this kind")
    args = ap.parse_args(argv)
    try:
        blocks = extract_files(args.files, args.kind)
    except (OSError, ValueError) as exc:
        print(f"extract_commands: {exc}", file=sys.stderr)
        return 1
    json.dump(blocks, sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
