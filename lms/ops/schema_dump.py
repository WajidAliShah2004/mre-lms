#!/usr/bin/env python3
"""Print the parts of OpenClaw's config schema a patch is about to touch.

    openclaw config schema > /tmp/oc-schema.json
    ./ops/schema_dump.py /tmp/oc-schema.json channels.telegram bindings commands

One line per setting: path, type/enum/default, the first words of its
description. Read-only.

WHY THIS IS A FILE AND NOT A PASTE
----------------------------------
D-021 and D-056 both came from writing OpenClaw config from documents instead
of from the schema the installed version actually ships. So every patch starts
by reading the schema. The first attempt at that for Phase 7b was a 25-line
heredoc pasted over RustDesk, which arrived interleaved and left the shell
stuck inside an unterminated heredoc (Oct 6). Anything longer than a line goes
in the repo.
"""

from __future__ import annotations

import json
import sys

KEYS = ("type", "enum", "default", "const", "required")


def resolve(node: dict, root: dict) -> dict:
    """Follow a local "$ref": "#/a/b" pointer, once per hop."""
    seen = set()
    while isinstance(node, dict) and "$ref" in node and node["$ref"] not in seen:
        ref = node["$ref"]
        seen.add(ref)
        if not ref.startswith("#/"):
            break
        target = root
        for part in ref[2:].split("/"):
            target = target.get(part.replace("~1", "/").replace("~0", "~"), {})
        node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
    return node


def wanted(path: str, prefixes: list[str]) -> bool:
    return any(path == p or path.startswith(p + ".") or path.startswith(p + "[")
               for p in prefixes)


def on_the_way(path: str, prefixes: list[str]) -> bool:
    return not path or any(p.startswith(path + ".") or p == path for p in prefixes)


def walk(node, path: str, root: dict, prefixes: list[str], out: list, depth=0):
    if not isinstance(node, dict) or depth > 40:
        return
    node = resolve(node, root)
    if not (wanted(path, prefixes) or on_the_way(path, prefixes)):
        return
    if path and wanted(path, prefixes):
        info = {k: node[k] for k in KEYS if k in node}
        desc = " ".join((node.get("description") or "").split())[:140]
        out.append(f"{path}  {json.dumps(info)}  {desc}".rstrip())
    for k, v in (node.get("properties") or {}).items():
        walk(v, f"{path}.{k}" if path else k, root, prefixes, out, depth + 1)
    if isinstance(node.get("items"), dict):
        walk(node["items"], f"{path}[]", root, prefixes, out, depth + 1)
    if isinstance(node.get("additionalProperties"), dict):
        walk(node["additionalProperties"], f"{path}.*", root, prefixes, out, depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        for v in node.get(key) or []:
            walk(v, path, root, prefixes, out, depth + 1)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    with open(sys.argv[1], encoding="utf-8") as fh:
        text = fh.read()
    root = json.loads(text[text.index("{"):])      # tolerate a CLI banner
    out: list[str] = []
    walk(root, "", root, sys.argv[2:], out)
    if not out:
        # Not the shape expected; say what it IS rather than print nothing.
        print("no matching settings. Top-level keys:", list(root)[:30])
        props = root.get("properties") or {}
        print("properties:", list(props)[:60])
        return 1
    for line in dict.fromkeys(out):                 # de-dupe, keep order
        print(line)
    print(f"-- {len(dict.fromkeys(out))} settings", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
