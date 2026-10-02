#!/usr/bin/env python3
"""
mcad_tree.py - set up and check an MCAD_base file store.

The store is rooted on the Fusion cloud's own structure: hub, project, folder,
then one folder per design. Everything about a design lands in its folder:
the latest cloud export in wip/, frozen revisions in released/, records of
what actually ran in builds/, and any loose material beside them. The layout
and its rules are in docs/CANONICAL_TREE.md, which is copied into the store as
SCHEMA.md.

`init` marks a folder as a store. fusion_sync.py creates the hubs, projects,
and design folders. `check` finds what is missing or out of place, `verify`
also re-hashes every released file, and `index` writes a part-number lookup. All are safe to re-run; none overwrite or delete what
they did not write.

Standard library only. Python 3.9+.

Quick start:
    ./mcad_tree.py init /Volumes/MCAD/MCAD_base --dry-run   # show what would be created
    ./mcad_tree.py init /Volumes/MCAD/MCAD_base             # create it
    ./mcad_tree.py check /Volumes/MCAD/MCAD_base            # find gaps: missing files, records, sign-offs
    ./mcad_tree.py verify /Volumes/MCAD/MCAD_base           # check, plus re-hash every released file
    ./mcad_tree.py index /Volumes/MCAD/MCAD_base            # write _index/parts.csv
    ./mcad_tree.py add-machine /Volumes/MCAD/MCAD_base haas-vf2
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Set, Tuple

SCHEMA_VERSION = 2
MARKER = ".mcad-tree.json"
SCHEMA_DOC = "SCHEMA.md"
SCHEMA_SOURCE = Path(__file__).resolve().parent / "docs" / "CANONICAL_TREE.md"

# Inside a design folder.
DESIGN_FILE = "design.json"
HISTORY_FILE = "history.jsonl"
WIP_DIRNAME = "wip"
RELEASED_DIRNAME = "released"
BUILDS_DIRNAME = "builds"
CURRENT_FILE = "CURRENT"
DELETED_MARKER = "DELETED_IN_CLOUD"
MANIFEST_FILE = "manifest.json"
SUMS_FILE = "SHA256SUMS"
OBSOLETE_MARKER = "OBSOLETE"
# Inside a revision: machine files sit in build/<process>/<model>/ and cam/<model>/.
CAD_DIRNAME = "cad"
BUILD_DIRNAME = "build"
CAM_DIRNAME = "cam"
PROFILE_FILE = "profile.json"
GCODE_SUFFIXES = (".gcode", ".bgcode", ".gco")
BUILD_FILE = "build.json"
NONCONFORMANCE_FILE = "nonconformance.json"
BUILD_RESULTS = ("pending", "accepted", "rejected")
DISPOSITIONS = ("scrap", "rework", "use-as-is", "return")

# At the store root, beside the hubs.
INDEX_DIRNAME = "_index"
OUTBOX_DIRNAME = "_outbox"
PARTS_INDEX = "parts.csv"
INDEX_COLUMNS = ["part_number", "name", "kind", "hub", "project", "path",
                 "current_revision", "wip_version", "deleted_in_cloud"]

ROOT_README = """\
# MCAD_base

Shop CAD file store, laid out per SCHEMA.md (schema version {schema}).

The tree follows Fusion: hub, project, folder, then one folder per design.
Inside a design folder:

| Name | What it is | Who writes |
|---|---|---|
| `design.json` | identity: Fusion item id, part number, description | sync tool; you may add fields |
| `history.jsonl` | ledger: synced, released, built | tools, append-only |
| `wip/` | latest export from the Fusion cloud, overwritten when the cloud changes | sync tool only |
| `released/` | frozen revisions, one folder per revision | release tool only |
| `builds/` | records of what actually ran, who inspected and accepted it, and what was rejected | operators, farm software |
| anything else | photos, loose STLs, notes | you |

Backup and release are different things. `wip/` changes whenever the design
does. A revision under `released/` never changes after it is signed off.

Do not save work into `wip/`. Do not edit anything under `released/`.

This layout is maintained by `mcad_tree.py`. Run `mcad_tree.py check <this folder>`
to see whether the store still follows the schema.
"""

# Things the NAS or a client OS drops on a share; never ours, never reported.
IGNORED = {"#recycle", "@eaDir", "#snapshot", "desktop.ini", "Thumbs.db"}
# Names the schema itself spells in capitals.
WELL_KNOWN = {"README.md", "SCHEMA.md", "CURRENT", "OBSOLETE", "SHA256SUMS", "CHANGELOG.md"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
REV_RE = re.compile(r"^rev-[a-z0-9]+$")
MAX_PATH = 200


class TreeError(Exception):
    pass


def tool_docs() -> dict:
    """rel path -> text for every file the scaffold owns."""
    docs = {"README.md": ROOT_README.format(schema=SCHEMA_VERSION)}
    if SCHEMA_SOURCE.exists():
        docs[SCHEMA_DOC] = SCHEMA_SOURCE.read_text(encoding="utf-8")
    return docs


def ignored(name: str) -> bool:
    return name.startswith(".") or name in IGNORED


def valid_name(name: str) -> bool:
    return name in WELL_KNOWN or bool(NAME_RE.match(name))


def read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TreeError(f"{path} is unreadable: {exc}")
    if not isinstance(data, dict):
        raise TreeError(f"{path} is not a JSON object")
    return data


def require_root(root: Path) -> None:
    # Never create the root itself: if the share is not mounted, a mkdir here
    # would quietly build the store on the local disk instead.
    if not root.is_dir():
        raise TreeError(f"{root} does not exist or is not a folder. Is the share mounted?")


def require_store(root: Path) -> dict:
    require_root(root)
    marker = read_json(root / MARKER)
    if marker is None:
        raise TreeError(f"{root} has not been initialized. Run init first.")
    if marker.get("schema") != SCHEMA_VERSION:
        raise TreeError(f"{root} is at schema {marker.get('schema')}; this tool works on schema {SCHEMA_VERSION}.")
    return marker


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
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")


def init_tree(root: Path, dry_run: bool = False, update_docs: bool = False,
              out: Callable[[str], None] = print) -> int:
    """Mark root as a store and write its docs. Returns the number of changes."""
    require_root(root)
    marker = read_json(root / MARKER)
    if marker and marker.get("schema") != SCHEMA_VERSION:
        raise TreeError(
            f"{root} was built at schema {marker.get('schema')}; this tool builds schema {SCHEMA_VERSION}. "
            "There is no automatic migration."
        )
    sc = Scaffold(root, dry_run, out)
    for rel, text in tool_docs().items():
        sc.write(rel, text, update=update_docs)
    if marker is None:
        sc.write(MARKER, json.dumps({
            "schema": SCHEMA_VERSION,
            "tool": "mcad_tree",
            "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        }, indent=2) + "\n")
    return sc.changed


def find_designs(root: Path) -> Iterator[Path]:
    """Every folder holding a design.json. Design folders do not nest, so the walk stops there."""
    for dirpath, dirnames, filenames in os.walk(root):
        if DESIGN_FILE in filenames:
            dirnames[:] = []
            yield Path(dirpath)
            continue
        at_root = Path(dirpath) == root
        dirnames[:] = sorted(d for d in dirnames if not ignored(d) and not (at_root and d.startswith("_")))


def current_revision(design: Path) -> Optional[str]:
    path = design / RELEASED_DIRNAME / CURRENT_FILE
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def section(data: dict, key: str) -> dict:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def holds_gcode(path: Path) -> bool:
    """A G-code file, or a sliced 3MF with G-code inside. A project 3MF saved before slicing has none."""
    name = path.name.lower()
    if name.endswith(GCODE_SUFFIXES):
        return True
    if not name.endswith(".3mf"):
        return False
    try:
        with zipfile.ZipFile(path) as zf:
            return any(entry.lower().endswith(GCODE_SUFFIXES) for entry in zf.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def has_machine_files(revision: Path, model: str) -> bool:
    """A printer model needs G-code in build/<process>/<model>/; a machine tool needs a program in
    cam/<model>/, where extensions vary too much to police. A slicer profile alone is neither."""
    cam = revision / CAM_DIRNAME / model
    if cam.is_dir() and any(not ignored(name) and name != PROFILE_FILE for name in os.listdir(cam)):
        return True
    build = revision / BUILD_DIRNAME
    for process in os.listdir(build) if build.is_dir() else []:
        folder = build / process / model
        if folder.is_dir() and any(holds_gcode(folder / name) for name in os.listdir(folder) if not ignored(name)):
            return True
    return False


def revision_files(revision: Path) -> Set[str]:
    """Every file SHA256SUMS should cover, as paths relative to the revision with forward slashes."""
    files: Set[str] = set()
    for dirpath, dirnames, filenames in os.walk(revision):
        dirnames[:] = [d for d in dirnames if not ignored(d)]
        for name in filenames:
            rel = (Path(dirpath) / name).relative_to(revision).as_posix()
            if not ignored(name) and rel not in (SUMS_FILE, OBSOLETE_MARKER):
                files.add(rel)
    return files


def read_sums(revision: Path) -> Dict[str, str]:
    """SHA256SUMS in sha256sum's format: digest, two spaces (or space and '*'), path."""
    sums: Dict[str, str] = {}
    for line in (revision / SUMS_FILE).read_text(encoding="utf-8").splitlines():
        digest, _, name = line.strip().partition(" ")
        name = name.lstrip(" *")
        if digest and name:
            sums[name] = digest.lower()
    return sums


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_sums(revision: Path, rel: str, problems: List[str], hashes: bool) -> None:
    """SHA256SUMS and the folder list the same files; with hashes, every file still matches."""
    if not (revision / SUMS_FILE).exists():
        return  # already reported as missing
    listed = read_sums(revision)
    present = revision_files(revision)
    for name in sorted(set(listed) - present):
        problems.append(f"{SUMS_FILE} lists a file that is not there ({name}): {rel}")
    for name in sorted(present - set(listed)):
        problems.append(f"file is not in {SUMS_FILE} ({name}): {rel}")
    if hashes:
        for name in sorted(present & set(listed)):
            if file_digest(revision / name) != listed[name]:
                problems.append(f"file does not match its checksum ({name}): {rel}")


def proven_models(design: Path) -> Dict[str, Set[str]]:
    """Revision -> the machine models its manifest says it is proven on."""
    proven: Dict[str, Set[str]] = {}
    released = design / RELEASED_DIRNAME
    if not released.is_dir():
        return proven
    for entry in os.listdir(released):
        path = released / entry
        if not path.is_dir() or not REV_RE.match(entry):
            continue
        try:
            manifest = read_json(path / MANIFEST_FILE) or {}
        except TreeError:
            manifest = {}  # reported by check_released
        models = section(manifest, "process").get("proven_on")
        proven[entry] = {m for m in models if isinstance(m, str)} if isinstance(models, list) else set()
    return proven


def check_manifest(revision: Path, rel: str, problems: List[str]) -> None:
    """A revision names who reviewed and approved it, and holds what its manifest says it holds."""
    try:
        manifest = read_json(revision / MANIFEST_FILE)
    except TreeError as exc:
        problems.append(str(exc))
        return
    if manifest is None:
        return  # already reported as missing
    approval = section(manifest, "approval")
    for key in ("reviewed_by", "approved_by"):
        if not approval.get(key):
            problems.append(f"manifest has no approval.{key}: {rel}")
    record = approval.get("review_record")
    if record and not (revision / record).exists():
        problems.append(f"manifest cites a review record that is not there ({record}): {rel}")
    for name in sorted(section(manifest, "files")):
        if not (revision / name).is_file():
            problems.append(f"manifest lists a file that is not there ({name}): {rel}")
    models = section(manifest, "process").get("proven_on")
    for model in models if isinstance(models, list) else []:
        if not isinstance(model, str) or not has_machine_files(revision, model):
            problems.append(f"manifest says proven on '{model}' but the revision holds no G-code or NC program for it: {rel}")


def check_released(root: Path, design: Path, problems: List[str],
                   kind: str = "design", hashes: bool = False) -> None:
    released = design / RELEASED_DIRNAME
    if not released.is_dir():
        return

    def rel(path) -> str:
        return os.path.relpath(path, root)

    revisions = []
    for entry in sorted(os.listdir(released)):
        path = released / entry
        if ignored(entry) or entry == CURRENT_FILE:
            continue
        if not path.is_dir() or not REV_RE.match(entry):
            problems.append(f"not a revision folder (expected rev-<x>): {rel(path)}")
            continue
        revisions.append(entry)
        for required in (MANIFEST_FILE, SUMS_FILE):
            if not (path / required).exists():
                problems.append(f"revision is missing {required}: {rel(path)}")
        check_manifest(path, rel(path), problems)
        check_sums(path, rel(path), problems, hashes)
        cad = path / CAD_DIRNAME
        if kind == "design" and not (cad.is_dir() and any(not ignored(n) for n in os.listdir(cad))):
            problems.append(f"revision of a design has no CAD file under {CAD_DIRNAME}/: {rel(path)}")
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if not ignored(d)]
            for name in dirnames + [f for f in filenames if not ignored(f)]:
                full = rel(os.path.join(dirpath, name))
                if not valid_name(name):
                    problems.append(f"name breaks the naming rule: {full}")
                if len(full) >= MAX_PATH:
                    problems.append(f"path is {len(full)} characters, limit is {MAX_PATH}: {full}")
    current = current_revision(design)
    if revisions and current is None:
        problems.append(f"revisions exist but there is no {CURRENT_FILE}: {rel(released)}")
    elif current is not None and current not in revisions:
        problems.append(f"{CURRENT_FILE} names '{current}', which is not a revision: {rel(released)}")


def check_builds(root: Path, design: Path, problems: List[str], notes: List[str]) -> None:
    """Every build names what ran and on what, is inspected and accepted by someone, and anything
    rejected has a disposition. The first build on a model the revision is not proven on is a first
    article: it needs an inspection record before that model counts as proven."""
    builds = design / BUILDS_DIRNAME
    if not builds.is_dir():
        return
    proven = proven_models(design)
    # Folder names start with the date, so sorted order is the order the builds ran.
    for entry in sorted(os.listdir(builds)):
        path = builds / entry
        if ignored(entry) or not path.is_dir():
            continue
        rel = os.path.relpath(path, root)
        try:
            build = read_json(path / BUILD_FILE)
            nonconformance = read_json(path / NONCONFORMANCE_FILE)
        except TreeError as exc:
            problems.append(str(exc))
            continue
        if build is None:
            problems.append(f"build has no {BUILD_FILE}: {rel}")
            continue
        revision, model = (v if isinstance(v, str) else None
                           for v in (build.get("revision"), build.get("machine_model")))
        if not revision:
            problems.append(f"build does not name the revision it ran: {rel}")
        elif revision not in proven:
            problems.append(f"build names '{revision}', which is not a revision: {rel}")
        if not model:
            problems.append(f"build does not name the machine model it ran on: {rel}")
        first_article = revision in proven and bool(model) and model not in proven[revision]
        result = build.get("result")
        if result not in BUILD_RESULTS:
            problems.append(f"build result is '{result}', expected one of {', '.join(BUILD_RESULTS)}: {rel}")
            continue
        if result == "pending":
            if first_article:
                notes.append(f"build is awaiting first-article inspection, {revision} is not proven on {model}: {rel}")
            else:
                notes.append(f"build is awaiting inspection: {rel}")
            continue
        if result == "accepted":
            inspection = section(build, "inspection")
            if not inspection.get("inspected_by"):
                problems.append(f"accepted build has no inspection.inspected_by: {rel}")
            record = inspection.get("record")
            if record and not (path / record).exists():
                problems.append(f"build cites an inspection record that is not there ({record}): {rel}")
            if not build.get("accepted_by"):
                problems.append(f"accepted build has no accepted_by: {rel}")
            if first_article:
                if not record:
                    problems.append(f"first build of {revision} on {model} was accepted "
                                    f"without an inspection record: {rel}")
                proven[revision].add(model)
        quantity = section(build, "quantity")
        built, accepted = quantity.get("built"), quantity.get("accepted")
        short = isinstance(built, int) and isinstance(accepted, int) and accepted < built
        if result == "rejected" or short:
            if nonconformance is None:
                problems.append(f"build has rejected parts but no {NONCONFORMANCE_FILE}: {rel}")
                continue
            if not nonconformance.get("description"):
                problems.append(f"{NONCONFORMANCE_FILE} has no description: {rel}")
            if nonconformance.get("disposition") not in DISPOSITIONS:
                problems.append(f"{NONCONFORMANCE_FILE} disposition is '{nonconformance.get('disposition')}', "
                                f"expected one of {', '.join(DISPOSITIONS)}: {rel}")
            if not nonconformance.get("decided_by"):
                problems.append(f"{NONCONFORMANCE_FILE} has no decided_by: {rel}")


def check_tree(root: Path, hashes: bool = False) -> Tuple[List[str], List[str]]:
    """Compare root against the schema. Returns (problems, notes). With hashes, also re-hash every
    released file against its SHA256SUMS, which reads the whole store."""
    require_root(root)
    problems: List[str] = []
    notes: List[str] = []

    marker = read_json(root / MARKER)
    if marker is None:
        problems.append(f"no {MARKER}: this folder has not been initialized")
    elif marker.get("schema") != SCHEMA_VERSION:
        problems.append(f"schema is {marker.get('schema')}, this tool expects {SCHEMA_VERSION}")

    for rel, text in tool_docs().items():
        path = root / rel
        if not path.exists():
            problems.append(f"missing file: {rel}")
        elif path.read_text(encoding="utf-8") != text:
            notes.append(f"{rel} differs from this version of the tool (init --update-docs rewrites it)")

    # At the root: hubs are folders; the only files are the store's own docs.
    for entry in sorted(os.listdir(root)):
        if ignored(entry) or entry in ("README.md", SCHEMA_DOC):
            continue
        if not (root / entry).is_dir():
            notes.append(f"loose file at the root: {entry}")
        elif entry.startswith("_") and entry not in (INDEX_DIRNAME, OUTBOX_DIRNAME):
            notes.append(f"unknown folder at the root: {entry}")

    seen: Dict[str, str] = {}
    for design in find_designs(root):
        rel = os.path.relpath(design, root)
        try:
            info = read_json(design / DESIGN_FILE) or {}
        except TreeError as exc:
            problems.append(str(exc))
            continue
        item_id = info.get("fusion_item_id")
        if not item_id:
            problems.append(f"{DESIGN_FILE} has no fusion_item_id: {rel}")
        elif item_id in seen:
            problems.append(f"two folders claim the same Fusion item: {seen[item_id]} and {rel}")
        else:
            seen[item_id] = rel
        if (design / DELETED_MARKER).exists():
            notes.append(f"deleted in the cloud, kept here: {rel}")
        check_released(root, design, problems, info.get("kind") or "design", hashes)
        check_builds(root, design, problems, notes)

    return problems, notes


def build_index(root: Path, dry_run: bool = False, out: Callable[[str], None] = print) -> int:
    """Write _index/parts.csv: one row per design, so a part number finds its folder."""
    require_store(root)
    rows = []
    for design in find_designs(root):
        info = read_json(design / DESIGN_FILE) or {}
        rows.append({
            "part_number": info.get("part_number") or "",
            "name": info.get("name") or design.name,
            "kind": info.get("kind") or "",
            "hub": info.get("hub") or "",
            "project": info.get("project") or "",
            "path": os.path.relpath(design, root),
            "current_revision": current_revision(design) or "",
            "wip_version": (info.get("wip") or {}).get("version_number") or "",
            "deleted_in_cloud": "yes" if (design / DELETED_MARKER).exists() else "",
        })
    rows.sort(key=lambda r: (r["part_number"] == "", r["part_number"], r["path"]))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=INDEX_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    sc = Scaffold(root, dry_run, out)
    sc.write(f"{INDEX_DIRNAME}/{PARTS_INDEX}", buf.getvalue(), update=True)
    out(f"{len(rows)} design(s), {sum(1 for r in rows if r['part_number'])} with a part number.")
    return sc.changed


def add_machine(root: Path, name: str, dry_run: bool = False, out: Callable[[str], None] = print) -> int:
    """A flat, safely named folder a machine can mount. The release tool copies into it; it is never a file's home."""
    require_store(root)
    if not NAME_RE.match(name):
        raise TreeError(f"'{name}' breaks the naming rule: lowercase letters, digits, '-', '_', '.' only")
    sc = Scaffold(root, dry_run, out)
    sc.mkdir(f"{OUTBOX_DIRNAME}/{name}")
    return sc.changed


# ---------- CLI ----------

def summarize(changed: int, dry_run: bool) -> None:
    if not changed:
        print("Nothing to do.")
    elif dry_run:
        print(f"{changed} change(s) would be made. Nothing was written.")
    else:
        print(f"{changed} change(s) made.")


def cmd_init(args: argparse.Namespace) -> None:
    root = Path(args.root)
    print(f"{'Would set up' if args.dry_run else 'Setting up'} {root} (schema {SCHEMA_VERSION})")
    summarize(init_tree(root, args.dry_run, args.update_docs), args.dry_run)


def cmd_check(args: argparse.Namespace) -> None:
    root = Path(args.root)
    problems, notes = check_tree(root, hashes=args.command == "verify")
    for line in problems:
        print(f"  PROBLEM  {line}")
    for line in notes:
        print(f"  note     {line}")
    print(f"{sum(1 for _ in find_designs(root))} design(s), {len(problems)} problem(s), {len(notes)} note(s).")
    if problems:
        sys.exit(1)


def cmd_index(args: argparse.Namespace) -> None:
    summarize(build_index(Path(args.root), args.dry_run), args.dry_run)


def cmd_add_machine(args: argparse.Namespace) -> None:
    summarize(add_machine(Path(args.root), args.name, args.dry_run), args.dry_run)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Set up and check an MCAD_base file store.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="mark a folder as a store and write its docs; never overwrites or deletes")
    s.add_argument("root", help="the folder to use as the store root")
    s.add_argument("--dry-run", action="store_true", help="show what would be created")
    s.add_argument("--update-docs", action="store_true", help="rewrite SCHEMA.md and README.md if they are out of date")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("check", help="find gaps: missing files, records, and sign-offs; fast, hashes nothing")
    s.add_argument("root")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("verify", help="check, plus re-hash every released file against its SHA256SUMS; reads the whole store")
    s.add_argument("root")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("index", help="write _index/parts.csv, a part-number lookup")
    s.add_argument("root")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_index)

    s = sub.add_parser("add-machine", help="add a machine-facing folder under _outbox/")
    s.add_argument("root")
    s.add_argument("name", help="e.g. haas-vf2")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_add_machine)

    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except TreeError as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
