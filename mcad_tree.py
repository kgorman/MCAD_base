#!/usr/bin/env python3
"""
mcad_tree.py - scaffold and check the canonical shop tree on a NAS share.

The tree is defined once, as data, in this file (DIRS and AREA_NOTES) and
described for humans in docs/CANONICAL_TREE.md, which is copied onto the share as
SCHEMA.md. `init` applies the definition to a root folder; `check` compares a
root against it. Both are safe to re-run for the life of the share: init only
adds what is missing and never overwrites or deletes anything it finds.

Standard library only. Python 3.9+.

Quick start:
    ./mcad_tree.py init /Volumes/MCAD/MCAD_base --dry-run   # show what would be created
    ./mcad_tree.py init /Volumes/MCAD/MCAD_base             # create it
    ./mcad_tree.py check /Volumes/MCAD/MCAD_base            # does the share still match?
    ./mcad_tree.py add-machine /Volumes/MCAD/MCAD_base haas-vf2
    ./mcad_tree.py add-printer /Volumes/MCAD/MCAD_base bambu-p1s
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, List, Optional

SCHEMA_VERSION = 1
MARKER = ".mcad-tree.json"
SCHEMA_DOC = "SCHEMA.md"
SCHEMA_SOURCE = Path(__file__).resolve().parent / "docs" / "CANONICAL_TREE.md"

# Every directory the scaffold owns, relative to the root.
DIRS = [
    "logs",
    "mirror",
    "released",
    "nc",
    "nc/_prove-out",
    "print",
    "print/queue",
    "print/builds",
    "print/archive",
    "library",
    "library/posts",
    "library/tools",
    "library/machines",
    "library/print-profiles",
    "library/templates",
    "library/standards",
]

# One README per area so the writer rule is visible to anyone browsing the share.
AREA_NOTES = {
    "mirror": (
        "Nightly mirror of the Fusion cloud. Written by fusion_sync only.\n"
        "Mutable: files here are overwritten when the cloud changes. Do not edit\n"
        "or save work here. This answers \"could we lose it\", not \"what did we ship\".\n"
    ),
    "released": (
        "Release records, one folder per part number, one subfolder per revision.\n"
        "Written by the release tool only. A revision folder is frozen once its\n"
        "SHA256SUMS exists: new revision, new folder. Obsolete revisions get an\n"
        "OBSOLETE marker and are never deleted. CURRENT is a one-line text file\n"
        "naming the current revision.\n"
    ),
    "nc": (
        "What the machines mount. Programs here are copies from released/ and are\n"
        "never edited in place. Unreleased programs go in _prove-out/ so nobody\n"
        "runs them by mistake.\n"
    ),
    "print": (
        "What the print farm reads. queue/ receives sliced files from a release;\n"
        "builds/ gets one folder per build actually run, with the exact file that\n"
        "ran and a build.json; archive/ holds old builds kept per retention policy.\n"
    ),
    "library": (
        "Shared assets: post processors, tool libraries, machine definitions,\n"
        "print profiles, templates, and the written standards. Changes here are\n"
        "controlled; treat it like released/, not like a scratch folder.\n"
    ),
    "logs": "Logs from the sync, release, and nightly verify jobs.\n",
}

ROOT_README = """\
# MCAD_base

Shop CAD archive, laid out per SCHEMA.md (schema version {schema}).

| Folder | What it is | Who writes |
|---|---|---|
| `mirror/` | nightly backup of the Fusion cloud | sync tool only |
| `released/` | immutable release records, by part number and revision | release tool only |
| `nc/` | programs the machines run, copied from `released/` | release tool only |
| `print/` | print queue and build records | release tool, farm, operators |
| `library/` | posts, tools, machines, print profiles, standards | admin |
| `logs/` | sync, release, and verify logs | tools |

Backup and release are different things. `mirror/` changes every night.
`released/` never changes after a revision is signed off.

This layout is maintained by `mcad_tree.py`. Run `mcad_tree.py check <this folder>`
to see whether it still matches the schema.
"""

# Things the NAS or a client OS drops on a share; never ours, never reported.
IGNORED = {"#recycle", "@eaDir", "#snapshot", "desktop.ini", "Thumbs.db"}
# Names the schema itself spells in capitals.
WELL_KNOWN = {"README.md", "SCHEMA.md", "CURRENT", "OBSOLETE", "SHA256SUMS", "CHANGELOG.md", "_CHANGELOG.txt"}
NAME_RE = re.compile(r"^_?[a-z0-9][a-z0-9._-]*$")
MAX_PATH = 200
# mirror/ carries the cloud's own names, so the naming rule is not applied there.
NAMED_AREAS = ["released", "nc", "print", "library", "logs"]


class TreeError(Exception):
    pass


def tool_docs() -> dict:
    """rel path -> text for every file the scaffold owns."""
    docs = {"README.md": ROOT_README.format(schema=SCHEMA_VERSION)}
    for area, note in AREA_NOTES.items():
        docs[f"{area}/README.md"] = f"# {area}/\n\n{note}\nSee ../SCHEMA.md for the full layout and rules.\n"
    if SCHEMA_SOURCE.exists():
        docs[SCHEMA_DOC] = SCHEMA_SOURCE.read_text(encoding="utf-8")
    return docs


def ignored(name: str) -> bool:
    return name.startswith(".") or name in IGNORED


def valid_name(name: str) -> bool:
    return name in WELL_KNOWN or bool(NAME_RE.match(name))


def read_marker(root: Path) -> Optional[dict]:
    path = root / MARKER
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TreeError(f"{path} is unreadable: {exc}")


def require_root(root: Path) -> None:
    # Never create the root itself: if the share is not mounted, a mkdir here
    # would quietly build the tree on the local disk instead.
    if not root.is_dir():
        raise TreeError(f"{root} does not exist or is not a folder. Is the share mounted?")


class Scaffold:
    def __init__(self, root: Path, dry_run: bool = False, out: Callable[[str], None] = print):
        self.root = root
        self.dry_run = dry_run
        self.out = out
        self.changed = 0

    def mkdir(self, rel: str) -> None:
        path = self.root / rel
        if path.is_dir():
            return
        if path.exists():
            raise TreeError(f"{path} exists and is not a folder")
        self.out(f"  mkdir   {rel}/")
        self.changed += 1
        if not self.dry_run:
            path.mkdir(parents=True)

    def write(self, rel: str, text: str, update: bool = False) -> None:
        path = self.root / rel
        if path.exists():
            if not update or path.read_text(encoding="utf-8") == text:
                return
            self.out(f"  update  {rel}")
        else:
            self.out(f"  write   {rel}")
        self.changed += 1
        if not self.dry_run:
            path.write_text(text, encoding="utf-8")


def init_tree(root: Path, dry_run: bool = False, update_docs: bool = False,
              out: Callable[[str], None] = print) -> int:
    """Create whatever is missing under root. Returns the number of changes."""
    require_root(root)
    marker = read_marker(root)
    if marker and marker.get("schema", 0) > SCHEMA_VERSION:
        raise TreeError(
            f"{root} is at schema {marker['schema']}, newer than this tool ({SCHEMA_VERSION}). Update the tool."
        )
    sc = Scaffold(root, dry_run, out)
    for rel in DIRS:
        sc.mkdir(rel)
    for rel, text in tool_docs().items():
        sc.write(rel, text, update=update_docs)
    if marker is None:
        sc.write(MARKER, json.dumps({
            "schema": SCHEMA_VERSION,
            "tool": "mcad_tree",
            "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        }, indent=2) + "\n")
    return sc.changed


def check_tree(root: Path) -> tuple:
    """Compare root against the schema. Returns (problems, notes)."""
    require_root(root)
    problems: List[str] = []
    notes: List[str] = []

    marker = read_marker(root)
    if marker is None:
        problems.append(f"no {MARKER}: this folder has not been initialized")
    elif marker.get("schema") != SCHEMA_VERSION:
        problems.append(f"schema is {marker.get('schema')}, this tool expects {SCHEMA_VERSION}")

    for rel in DIRS:
        if not (root / rel).is_dir():
            problems.append(f"missing folder: {rel}/")

    for rel, text in tool_docs().items():
        path = root / rel
        if not path.exists():
            problems.append(f"missing file: {rel}")
        elif path.read_text(encoding="utf-8") != text:
            notes.append(f"{rel} differs from this version of the tool (init --update-docs rewrites it)")

    managed = {rel.split("/")[0] for rel in DIRS} | {"README.md", SCHEMA_DOC}
    for entry in sorted(os.listdir(root)):
        if not ignored(entry) and entry not in managed:
            notes.append(f"unmanaged at the root: {entry}")

    for area in NAMED_AREAS:
        for dirpath, dirnames, filenames in os.walk(root / area):
            dirnames[:] = [d for d in dirnames if not ignored(d)]
            for name in dirnames + [f for f in filenames if not ignored(f)]:
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                if not valid_name(name):
                    problems.append(f"name breaks the naming rule: {rel}")
                if len(rel) >= MAX_PATH:
                    problems.append(f"path is {len(rel)} characters, limit is {MAX_PATH}: {rel}")

    return problems, notes


def add_dirs(root: Path, name: str, rels: List[str], dry_run: bool = False,
             out: Callable[[str], None] = print) -> int:
    require_root(root)
    if read_marker(root) is None:
        raise TreeError(f"{root} has not been initialized. Run init first.")
    if not NAME_RE.match(name) or name.startswith("_"):
        raise TreeError(f"'{name}' breaks the naming rule: lowercase letters, digits, '-', '_', '.' only")
    sc = Scaffold(root, dry_run, out)
    for rel in rels:
        sc.mkdir(rel.format(name=name))
    return sc.changed


# ---------- CLI ----------

def summarize(changed: int, dry_run: bool) -> None:
    if not changed:
        print("Nothing to do. The tree is already in place.")
    elif dry_run:
        print(f"{changed} change(s) would be made. Nothing was written.")
    else:
        print(f"{changed} change(s) made.")


def cmd_init(args: argparse.Namespace) -> None:
    root = Path(args.root)
    print(f"{'Would scaffold' if args.dry_run else 'Scaffolding'} {root} (schema {SCHEMA_VERSION})")
    summarize(init_tree(root, args.dry_run, args.update_docs), args.dry_run)


def cmd_check(args: argparse.Namespace) -> None:
    problems, notes = check_tree(Path(args.root))
    for line in problems:
        print(f"  PROBLEM  {line}")
    for line in notes:
        print(f"  note     {line}")
    print(f"{len(problems)} problem(s), {len(notes)} note(s).")
    if problems:
        sys.exit(1)


def cmd_add_machine(args: argparse.Namespace) -> None:
    rels = ["nc/{name}", "nc/_prove-out/{name}"]
    summarize(add_dirs(Path(args.root), args.name, rels, args.dry_run), args.dry_run)


def cmd_add_printer(args: argparse.Namespace) -> None:
    rels = ["library/print-profiles/{name}"]
    summarize(add_dirs(Path(args.root), args.name, rels, args.dry_run), args.dry_run)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Scaffold and check the canonical shop tree.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="create whatever is missing; never overwrites or deletes")
    s.add_argument("root", help="the share or folder to build the tree in")
    s.add_argument("--dry-run", action="store_true", help="show what would be created")
    s.add_argument("--update-docs", action="store_true", help="rewrite SCHEMA.md and the READMEs if they are out of date")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("check", help="compare a tree against the schema")
    s.add_argument("root")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("add-machine", help="add a CNC machine's folders under nc/")
    s.add_argument("root")
    s.add_argument("name", help="e.g. haas-vf2")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_add_machine)

    s = sub.add_parser("add-printer", help="add a printer's profile folder under library/print-profiles/")
    s.add_argument("root")
    s.add_argument("name", help="e.g. bambu-p1s")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_add_printer)

    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except TreeError as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
