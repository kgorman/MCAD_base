#!/usr/bin/env python3
"""
mcad_tree.py - set up and check an MCAD_base file store.

The store is one folder per design, anywhere below the root. Everything about
a design lands in its folder: working files in wip/, frozen revisions in
released/, records of what actually ran in jobs/, and any loose material
beside them. The layout and its rules are in docs/CANONICAL_TREE.md, which is
copied into the store as SCHEMA.md.

`init` marks a folder as a store. People make design folders by hand; a sync
tool such as fusion_sync.py makes them from a CAD system's cloud. `check`
finds what is missing or out of place, `verify` also re-hashes every released
file, and `index` writes a part-number lookup. All are safe to re-run; none
overwrite or delete what they did not write.

Standard library only. Python 3.9+.

Quick start:
    ./mcad_tree.py init /path/to/store --dry-run   # show what would be created
    ./mcad_tree.py init /path/to/store             # create it
    ./mcad_tree.py check /path/to/store            # find gaps: missing files, records, sign-offs
    ./mcad_tree.py verify /path/to/store           # check, plus re-hash every released file
    ./mcad_tree.py index /path/to/store            # write _index/parts.csv
    ./mcad_tree.py add-machine /path/to/store haas-vf2
    ./mcad_tree.py upgrade /path/to/store --dry-run  # move a store to this tool's schema
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

# The version of these tools. It changes with every release of the repository.
__version__ = "0.1.2"
# The version of the layout. It changes only when a store that was valid would stop being valid;
# see "Versions" in docs/CANONICAL_TREE.md.
SCHEMA_VERSION = 2
# Schemas whose records check knows how to judge. A frozen revision keeps the schema it was
# released under, so this list only grows.
KNOWN_SCHEMAS = (2,)
# A record that does not state its schema is read as this one.
FIRST_RECORD_SCHEMA = 2
MARKER = ".mcad-tree.json"
SCHEMA_DOC = "SCHEMA.md"
SCHEMA_SOURCE = Path(__file__).resolve().parent / "docs" / "CANONICAL_TREE.md"

# Inside a design folder.
DESIGN_FILE = "design.json"
HISTORY_FILE = "history.jsonl"
WIP_DIRNAME = "wip"
RELEASED_DIRNAME = "released"
JOBS_DIRNAME = "jobs"
RESERVED_DIRNAMES = (WIP_DIRNAME, RELEASED_DIRNAME, JOBS_DIRNAME)
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
JOB_FILE = "job.json"
NONCONFORMANCE_FILE = "nonconformance.json"
JOB_RESULTS = ("pending", "accepted", "rejected")
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

One folder per design, anywhere below this folder. Arrange the folders above
a design however the shop likes. A folder is a design when it holds `wip/`,
`released/`, or `jobs/`. Inside a design folder:

| Name | What it is | Who writes |
|---|---|---|
| `wip/` | work in progress: the files being worked on | you; a sync tool replaces only the files it wrote |
| `released/` | frozen revisions, one folder per revision | whoever releases; nothing changes after sign-off |
| `jobs/` | records of what actually ran, who inspected and accepted it, and what was rejected | operators, shop software |
| `design.json` | optional: part number, description; for a synced design, its id in the CAD system | you; a sync tool keeps its own keys |
| `history.jsonl` | ledger: released, ran, synced | appended to, never rewritten |
| anything else | photos, loose STLs, notes | you |

Working files and releases are different things. `wip/` changes whenever the
design does. A revision under `released/` never changes after it is signed
off. Do not edit anything under `released/`.

To start a design, make a folder for it, make `wip/` inside it, and work
there with whatever CAD system the shop uses. For a design synced from a CAD
system's cloud, the sync also keeps the latest export in `wip/` and replaces
only its own files; do not edit those, because the next sync overwrites them.

Machines run released files, never `wip/`. The program for a machine is in
`released/<revision>/build/<process>/<model>/` or `released/<revision>/cam/<model>/`,
and `released/CURRENT` names the revision to run. SCHEMA.md says how to make
a release by hand and how to get one to a machine.

Run `mcad_tree.py check <this folder>` to see whether the store still follows
the schema.
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


def store_path(path, root: Path, sep: str = os.sep) -> str:
    """A path relative to the store root, with forward slashes on every platform, so the index and
    the messages are the same whichever machine wrote them."""
    return os.path.relpath(path, root).replace(sep, "/")


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
            "upgrade moves an older store forward; a newer store needs newer tools."
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


def holds_designs(path: Path) -> bool:
    return any(DESIGN_FILE in filenames for _, _, filenames in os.walk(path))


def find_designs(root: Path) -> Iterator[Path]:
    """Every design folder: one the sync tool made (it holds design.json), or one made by hand (it
    holds wip/, released/, or jobs/). Design folders do not nest, so the walk stops there."""
    for dirpath, dirnames, filenames in os.walk(root):
        at_root = Path(dirpath) == root
        reserved = [d for d in dirnames if d in RESERVED_DIRNAMES]
        # A cloud folder that happens to be called wip/ or jobs/ holds designs; it is not one.
        by_hand = not at_root and reserved and not any(holds_designs(Path(dirpath) / d) for d in reserved)
        if DESIGN_FILE in filenames or by_hand:
            dirnames[:] = []
            yield Path(dirpath)
            continue
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


def record_schema(record: dict, what: str, rel: str, problems: List[str]) -> Optional[int]:
    """The schema a record was written under, or None (and a problem) when this tool cannot judge it.
    A frozen revision is never rewritten, so it is judged by its own schema, not the store's."""
    schema = record.get("schema", FIRST_RECORD_SCHEMA)
    if schema in KNOWN_SCHEMAS:
        return schema
    problems.append(f"{what} is at schema {schema}, which this tool does not know "
                    f"(it knows {', '.join(str(s) for s in KNOWN_SCHEMAS)}): {rel}")
    return None


def check_manifest(revision: Path, rel: str, problems: List[str]) -> None:
    """A revision names who reviewed and approved it, and holds what its manifest says it holds."""
    try:
        manifest = read_json(revision / MANIFEST_FILE)
    except TreeError as exc:
        problems.append(str(exc))
        return
    if manifest is None:
        return  # already reported as missing
    if record_schema(manifest, MANIFEST_FILE, rel, problems) is None:
        return
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
        return store_path(path, root)

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


def check_jobs(root: Path, design: Path, problems: List[str], notes: List[str]) -> None:
    """Every job names what ran and on what, is inspected and accepted by someone, and anything
    rejected has a disposition. The first job on a model the revision is not proven on is a first
    article: it needs an inspection record before that model counts as proven."""
    jobs = design / JOBS_DIRNAME
    if not jobs.is_dir():
        return
    proven = proven_models(design)
    # Folder names start with the date, so sorted order is the order the jobs ran.
    for entry in sorted(os.listdir(jobs)):
        path = jobs / entry
        if ignored(entry) or not path.is_dir():
            continue
        rel = store_path(path, root)
        try:
            job = read_json(path / JOB_FILE)
            nonconformance = read_json(path / NONCONFORMANCE_FILE)
        except TreeError as exc:
            problems.append(str(exc))
            continue
        if job is None:
            problems.append(f"job has no {JOB_FILE}: {rel}")
            continue
        if record_schema(job, JOB_FILE, rel, problems) is None:
            continue
        revision, model = (v if isinstance(v, str) else None
                           for v in (job.get("revision"), job.get("machine_model")))
        if not revision:
            problems.append(f"job does not name the revision it ran: {rel}")
        elif revision not in proven:
            problems.append(f"job names '{revision}', which is not a revision: {rel}")
        if not model:
            problems.append(f"job does not name the machine model it ran on: {rel}")
        first_article = revision in proven and bool(model) and model not in proven[revision]
        result = job.get("result")
        if result not in JOB_RESULTS:
            problems.append(f"job result is '{result}', expected one of {', '.join(JOB_RESULTS)}: {rel}")
            continue
        if result == "pending":
            if first_article:
                notes.append(f"job is awaiting first-article inspection, {revision} is not proven on {model}: {rel}")
            else:
                notes.append(f"job is awaiting inspection: {rel}")
            continue
        if result == "accepted":
            inspection = section(job, "inspection")
            if not inspection.get("inspected_by"):
                problems.append(f"accepted job has no inspection.inspected_by: {rel}")
            record = inspection.get("record")
            if record and not (path / record).exists():
                problems.append(f"job cites an inspection record that is not there ({record}): {rel}")
            if not job.get("accepted_by"):
                problems.append(f"accepted job has no accepted_by: {rel}")
            if first_article:
                if not record:
                    problems.append(f"first job of {revision} on {model} was accepted "
                                    f"without an inspection record: {rel}")
                proven[revision].add(model)
        quantity = section(job, "quantity")
        made, accepted = quantity.get("made"), quantity.get("accepted")
        short = isinstance(made, int) and isinstance(accepted, int) and accepted < made
        if result == "rejected" or short:
            if nonconformance is None:
                problems.append(f"job has rejected parts but no {NONCONFORMANCE_FILE}: {rel}")
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
        problems.append(f"schema is {marker.get('schema')}, this tool expects {SCHEMA_VERSION} "
                        "(upgrade moves an older store forward)")

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
        rel = store_path(design, root)
        try:
            info = read_json(design / DESIGN_FILE) or {}
        except TreeError as exc:
            problems.append(str(exc))
            continue
        # Only a design synced from Fusion has an item id; one made by hand is known by its path.
        item_id = info.get("fusion_item_id")
        if not item_id:
            pass
        elif item_id in seen:
            problems.append(f"two folders claim the same Fusion item: {seen[item_id]} and {rel}")
        else:
            seen[item_id] = rel
        if (design / DELETED_MARKER).exists():
            notes.append(f"deleted in the cloud, kept here: {rel}")
        check_released(root, design, problems, info.get("kind") or "design", hashes)
        check_jobs(root, design, problems, notes)

    return problems, notes


# From-version -> the step that converts a store to the next version. A step takes (root, dry_run,
# out) and must not touch a frozen revision; upgrade_tree checks that afterwards. Empty until a
# schema follows 2.
UPGRADES: Dict[int, Callable[[Path, bool, Callable[[str], None]], None]] = {}


def frozen_files(root: Path) -> List[Tuple[str, str]]:
    """Every file in every frozen revision as (revision/path, digest), wherever its design folder
    sits. An upgrade may move a design folder; it may not change what a revision holds."""
    files: List[Tuple[str, str]] = []
    for design in find_designs(root):
        released = design / RELEASED_DIRNAME
        for entry in os.listdir(released) if released.is_dir() else []:
            revision = released / entry
            if revision.is_dir() and (revision / SUMS_FILE).exists():
                for name in revision_files(revision) | {SUMS_FILE}:
                    files.append((f"{entry}/{name}", file_digest(revision / name)))
    return sorted(files)


def upgrade_tree(root: Path, dry_run: bool = False, out: Callable[[str], None] = print) -> int:
    """Move a store from an older schema to this tool's. Returns the number of steps run. The marker
    is rewritten last, and only after every frozen revision is shown to be unchanged."""
    require_root(root)
    marker = read_json(root / MARKER)
    if marker is None:
        raise TreeError(f"{root} has not been initialized. Run init first.")
    have = marker.get("schema")
    if have == SCHEMA_VERSION:
        return 0
    if not isinstance(have, int) or have > SCHEMA_VERSION:
        raise TreeError(f"{root} is at schema {have}; this tool works on schema {SCHEMA_VERSION}. "
                        "Use a newer version of the tools.")
    steps = list(range(have, SCHEMA_VERSION))
    for version in steps:
        if version not in UPGRADES:
            raise TreeError(f"There is no upgrade from schema {version} to schema {version + 1}.")
    before = frozen_files(root)
    for version in steps:
        out(f"  schema {version} -> {version + 1}")
        UPGRADES[version](root, dry_run, out)
    if dry_run:
        return len(steps)
    if frozen_files(root) != before:
        raise TreeError(f"The upgrade changed a frozen revision. The store is still marked schema {have}; "
                        "restore it from backup.")
    marker.update({
        "schema": SCHEMA_VERSION,
        "upgraded_from": have,
        "upgraded_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    (root / MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    sc = Scaffold(root, dry_run, out)
    for rel, text in tool_docs().items():
        sc.write(rel, text, update=True)
    return len(steps)


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
            "path": store_path(design, root),
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
    """A flat, safely named folder a machine can mount. Released files are copied into it; it is never a file's home."""
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


def cmd_upgrade(args: argparse.Namespace) -> None:
    root = Path(args.root)
    steps = upgrade_tree(root, args.dry_run)
    if not steps:
        print(f"{root} is already at schema {SCHEMA_VERSION}. Nothing to do.")
    elif args.dry_run:
        print(f"{steps} step(s) would run. Nothing was written.")
    else:
        print(f"{root} is now at schema {SCHEMA_VERSION}.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Set up and check an MCAD_base file store.")
    p.add_argument("--version", action="version", version=f"mcad_tree {__version__} (schema {SCHEMA_VERSION})")
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

    s = sub.add_parser("upgrade", help="move a store from an older schema to this tool's; never changes a released revision")
    s.add_argument("root")
    s.add_argument("--dry-run", action="store_true", help="show the steps without writing")
    s.set_defaults(func=cmd_upgrade)

    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except TreeError as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
