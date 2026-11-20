#!/usr/bin/env python3
"""Release a revision of a design in an MCAD_base store.

This is the one implementation of the release procedure that SCHEMA.md describes
for doing by hand, in the same write order, so that an interrupted run leaves
exactly the evidence an interrupted person would: a revision folder without
SHA256SUMS, which `check` reports as incomplete. The result is the same files
either way, and `check` cannot tell the difference.

Use it from the command line through `mcad_tree.py release`, or import it:

    from mcad_release import create_release, FileSpec, Approval
    create_release(root, "Hub/Project/Design",
                   files=[FileSpec(src="/tmp/x.step", dest="cad/x.step")],
                   part_number="bm-0042", reason="Initial release", released_by="me",
                   approval=Approval(reviewed_by="me", approved_by="me"))

Every failure is a ReleaseError with a stable ``code`` a caller can branch on,
and a ``to_payload()`` for machine-readable output. The codes:

    not_found, invalid_input, invalid_name, path_traversal,
    revision_frozen, release_in_progress, release_structure_invalid,
    os_permission_denied, io_error, internal_error

Two of these are worth telling apart: ``revision_frozen`` and the other rule
codes mean "MCAD_base says no"; ``os_permission_denied`` means the filesystem
said no, which is the real security boundary.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import shutil
import socket
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple, Union

from mcad_tree import (
    BUILD_DIRNAME,
    CAD_DIRNAME,
    CAM_DIRNAME,
    CURRENT_FILE,
    DESIGN_FILE,
    HISTORY_FILE,
    MANIFEST_FILE,
    MAX_PATH,
    OBSOLETE_MARKER,
    OUTBOX_DIRNAME,
    PROFILE_FILE,
    RELEASED_DIRNAME,
    REV_RE,
    SCHEMA_VERSION,
    SUMS_FILE,
    WIP_DIRNAME,
    TreeError,
    check_tree,
    current_revision,
    file_digest,
    holds_gcode,
    ignored,
    read_json,
    require_store,
    revision_files,
    store_path,
    valid_name,
)

LOCK_FILENAME = ".mcad-release.lock"
VERSIONS_DIRNAME = "_versions"  # the sync's prior copies inside wip/; never released

# Where a loose file in wip/ goes in the revision, by extension. Files already under one of
# the revision's own folders (cad/, build/, cam/, ...) keep their place.
CAD_SUFFIXES = (".f3d", ".f3z", ".step", ".stp", ".iges", ".igs", ".sldprt", ".sldasm", ".ipt",
                ".iam", ".x_t", ".x_b", ".3dm", ".scad", ".fcstd", ".prt", ".catpart")
MESH_SUFFIXES = (".stl", ".3mf", ".obj", ".ply", ".amf")
DRAWING_SUFFIXES = (".pdf", ".dwg", ".dxf", ".svg")
REVISION_DIRNAMES = (CAD_DIRNAME, BUILD_DIRNAME, CAM_DIRNAME, "mesh", "drawings", "docs", "reviews", "src")


# ---------- errors ----------

class ReleaseError(TreeError):
    """Base for every failure this module raises on purpose."""

    code = "internal_error"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.details = details

    def to_payload(self) -> dict:
        return {"ok": False, "code": self.code, "message": str(self), "details": self.details}


class NotFoundError(ReleaseError):
    code = "not_found"


class InvalidInputError(ReleaseError):
    code = "invalid_input"


class NamingRuleError(ReleaseError):
    code = "invalid_name"


class PathTraversalError(ReleaseError):
    code = "path_traversal"


class RevisionFrozenError(ReleaseError):
    code = "revision_frozen"


class ReleaseInProgressError(ReleaseError):
    code = "release_in_progress"


class ReleaseStructureError(ReleaseError):
    """The release's own content breaks a structural rule: a design's revision needs a file
    under cad/, and a model claimed as proven on needs G-code or an NC program for it."""

    code = "release_structure_invalid"


class OSPermissionError(ReleaseError):
    """The operating system denied the operation; the real security boundary."""

    code = "os_permission_denied"


class StoreIOError(ReleaseError):
    code = "io_error"


class InternalError(ReleaseError):
    """This module's own bug: what it just wrote failed its own check."""

    code = "internal_error"


@contextlib.contextmanager
def translate_os_errors(path: Path) -> Iterator[None]:
    """The one place PermissionError/OSError become ReleaseErrors."""
    try:
        yield
    except ReleaseError:
        raise
    except PermissionError as exc:
        raise OSPermissionError(f"the operating system denied access to {path}",
                                path=str(path), errno=exc.errno) from None
    except OSError as exc:
        raise StoreIOError(f"I/O error at {path}: {exc.strerror}", path=str(path), errno=exc.errno) from None


# ---------- inputs ----------

@dataclass
class FileSpec:
    """One file to release: ``src`` anywhere readable, ``dest`` relative to the revision folder."""

    src: str
    dest: str
    metadata: Optional[dict] = None


@dataclass
class Approval:
    reviewed_by: str
    approved_by: str
    reviewed_at: Optional[str] = None
    approved_at: Optional[str] = None
    review_record: Optional[str] = None  # a dest among the files being released


@dataclass
class Process:
    primary: Optional[str] = None
    material: Optional[str] = None
    color: Optional[str] = None
    proven_on: List[str] = field(default_factory=list)
    post_processing: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


def _coerce(value, cls):
    if value is None or isinstance(value, cls):
        return value
    if isinstance(value, dict):
        return cls(**value)
    raise InvalidInputError(f"expected a {cls.__name__} or a dict, got {type(value).__name__}")


# ---------- paths ----------

def safe_join(root: Path, relative: str) -> Path:
    """Resolve ``relative`` under ``root``, refusing anything that would escape it."""
    if not relative or relative.strip() != relative:
        raise PathTraversalError(f"empty or whitespace-padded path: {relative!r}")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise PathTraversalError(f"absolute paths are not allowed: {relative!r}")
    if any(part in ("..", "") for part in candidate.parts):
        raise PathTraversalError(f"path escapes the store: {relative!r}")
    resolved_root = root.resolve()
    resolved = (root / candidate).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise PathTraversalError(f"path escapes the store: {relative!r}")
    return root / candidate


def validate_dest(dest: str) -> List[str]:
    """A release file's destination, relative to its revision folder: no traversal, and every
    segment obeys the naming rule. Returns the segments."""
    if not dest or dest.strip() != dest:
        raise PathTraversalError(f"empty or whitespace-padded destination: {dest!r}")
    candidate = Path(dest)
    if candidate.is_absolute() or any(part in ("..", "") for part in candidate.parts):
        raise PathTraversalError(f"destination escapes the revision folder: {dest!r}")
    segments = list(candidate.parts)
    for segment in segments:
        if not valid_name(segment):
            raise NamingRuleError(f"name breaks the naming rule: {segment!r} (in {dest!r})")
    return segments


def check_path_length(root: Path, path: Path) -> None:
    full = store_path(path, root)
    if len(full) >= MAX_PATH:
        raise NamingRuleError(f"path is {len(full)} characters, limit is {MAX_PATH}: {full}", path=full)


def resolve_design(root: Path, design: str) -> Path:
    path = safe_join(root, design)
    if not path.is_dir():
        raise NotFoundError(f"no design folder at {design!r}")
    return path


def slug(name: str) -> str:
    """A file name that obeys the naming rule: lowercase, spaces and odd characters to ``-``."""
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    out = []
    for ch in stem.lower():
        out.append(ch if ch.isalnum() or ch in "._-" else "-")
    cleaned = "".join(out).strip("-.")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned or "file"
    return f"{cleaned}.{suffix.lower()}" if suffix else cleaned


# ---------- choosing files ----------

def place_wip_file(rel: str) -> Optional[str]:
    """Where a file in wip/ goes in the revision, or None if this tool cannot tell.

    A file already under one of the revision's folders keeps its path, with each segment
    made to obey the naming rule. A loose file is routed by extension: CAD to cad/, meshes
    to mesh/, drawings to drawings/. G-code and NC programs need a machine model, which a
    loose file cannot name, so they come back None.
    """
    parts = Path(rel).parts
    if len(parts) > 1 and parts[0] in REVISION_DIRNAMES:
        return "/".join([parts[0]] + [slug(p) for p in parts[1:]])
    name = slug(parts[-1])
    suffix = Path(name).suffix
    if suffix in CAD_SUFFIXES:
        return f"{CAD_DIRNAME}/{name}"
    if suffix in MESH_SUFFIXES:
        return f"mesh/{name}"
    if suffix in DRAWING_SUFFIXES:
        return f"drawings/{name}"
    return None


def wip_files(design_path: Path, skip_unknown: bool = False) -> Tuple[List[FileSpec], List[str]]:
    """Every releasable file in the design's wip/, with its destination, plus the files this
    tool could not place (empty when skip_unknown is False and nothing was unplaceable;
    raises when skip_unknown is False and something was)."""
    wip = design_path / WIP_DIRNAME
    if not wip.is_dir():
        raise NotFoundError(f"no {WIP_DIRNAME}/ in {design_path.name}")
    specs: List[FileSpec] = []
    unplaced: List[str] = []
    seen: Dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(wip):
        dirnames[:] = sorted(d for d in dirnames if not ignored(d) and d != VERSIONS_DIRNAME)
        for filename in sorted(filenames):
            if ignored(filename):
                continue
            src = Path(dirpath) / filename
            rel = store_path(src, wip)
            dest = place_wip_file(rel)
            if dest is None:
                unplaced.append(rel)
                continue
            if dest in seen:
                raise InvalidInputError(
                    f"two files in {WIP_DIRNAME}/ would both become {dest!r}: {seen[dest]!r} and {rel!r}")
            seen[dest] = rel
            specs.append(FileSpec(src=str(src), dest=dest, metadata=_default_metadata(dest)))
    if unplaced and not skip_unknown:
        raise InvalidInputError(
            "these files in wip/ have no place in a revision; name a destination for each "
            "(a G-code file goes in build/<process>/<model>/, an NC program in cam/<model>/), "
            "or skip them: " + ", ".join(unplaced),
            unplaced=unplaced,
        )
    if not specs:
        raise InvalidInputError(f"nothing to release in {WIP_DIRNAME}/")
    specs.sort(key=lambda spec: spec.dest)
    return specs, unplaced


def _default_metadata(dest: str) -> dict:
    suffix = Path(dest).suffix.lstrip(".")
    return {"format": suffix} if suffix else {}


# ---------- revision names and the lock ----------

def next_revision_name(released_dir: Path) -> str:
    """rev-a, rev-b, ... rev-z, rev-aa, rev-ab, ...: the first name not already used."""
    existing = set()
    if released_dir.is_dir():
        existing = {entry for entry in os.listdir(released_dir) if REV_RE.match(entry)}
    n = 1
    while True:
        letters, k = "", n
        while k > 0:
            k, rem = divmod(k - 1, 26)
            letters = chr(ord("a") + rem) + letters
        name = f"rev-{letters}"
        if name not in existing:
            return name
        n += 1


def _lock_path(released_dir: Path) -> Path:
    return released_dir / LOCK_FILENAME


def read_lock(released_dir: Path) -> Optional[dict]:
    path = _lock_path(released_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"raw": True}


def _acquire_lock(released_dir: Path, revision: str) -> None:
    with translate_os_errors(released_dir):
        released_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(released_dir)
    with translate_os_errors(lock_path):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise ReleaseInProgressError("a release is already in progress for this design",
                                         lock=read_lock(released_dir)) from None
        with os.fdopen(fd, "w") as fh:
            json.dump({"pid": os.getpid(), "host": socket.gethostname(), "started_at": _now_iso(),
                       "operation": "release", "revision": revision}, fh)


def _release_lock(released_dir: Path) -> None:
    try:
        _lock_path(released_dir).unlink()
    except FileNotFoundError:
        pass


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------- the small operations ----------

def release_status(root: Path, design: str) -> dict:
    """Whether a release is in progress for a design, and any leftover unfrozen revision."""
    design_path = resolve_design(root, design)
    released_dir = design_path / RELEASED_DIRNAME
    lock = read_lock(released_dir)
    incomplete = []
    if released_dir.is_dir():
        for entry in sorted(os.listdir(released_dir)):
            path = released_dir / entry
            if path.is_dir() and REV_RE.match(entry) and not (path / SUMS_FILE).exists():
                incomplete.append(entry)
    return {"design": design, "locked": lock is not None, "lock": lock,
            "incomplete_revisions": incomplete, "current_revision": current_revision(design_path)}


def clear_release_lock(root: Path, design: str, force: bool = False) -> dict:
    """Remove a stale lock. Needs force=True: only after confirming no release is running."""
    if not force:
        raise InvalidInputError("clearing a release lock needs force=True; confirm no release is running first")
    design_path = resolve_design(root, design)
    released_dir = design_path / RELEASED_DIRNAME
    lock = read_lock(released_dir)
    with translate_os_errors(_lock_path(released_dir)):
        _release_lock(released_dir)
    return {"ok": True, "cleared": lock is not None, "lock": lock}


def abandon_release(root: Path, design: str, revision: str) -> dict:
    """Discard an unfrozen (no SHA256SUMS) leftover revision folder. Refuses a frozen one."""
    design_path = resolve_design(root, design)
    if not REV_RE.match(revision):
        raise InvalidInputError(f"revision name {revision!r} must match rev-<lowercase letters/digits>")
    revision_dir = design_path / RELEASED_DIRNAME / revision
    if not revision_dir.is_dir():
        raise NotFoundError(f"no revision folder {revision!r} in {design!r}")
    if (revision_dir / SUMS_FILE).exists():
        raise RevisionFrozenError(f"{revision} is frozen; a frozen revision cannot be abandoned")
    with translate_os_errors(revision_dir):
        shutil.rmtree(revision_dir)
    return {"ok": True, "abandoned": revision, "path": store_path(revision_dir, root)}


# ---------- planning a release ----------

@dataclass
class ReleasePlan:
    design_path: Path
    released_dir: Path
    revision: str
    revision_dir: Path
    resumed_incomplete_revision: bool
    manifest: dict
    prior_current: Optional[str]
    files: List[FileSpec]


def models_in(files: Sequence[FileSpec]) -> List[str]:
    """The machine models the files being released hold a program for."""
    found = []
    for spec in files:
        parts = Path(spec.dest).parts
        model = None
        if len(parts) >= 3 and parts[0] == CAM_DIRNAME and parts[-1] != PROFILE_FILE:
            model = parts[1]
        elif len(parts) >= 4 and parts[0] == BUILD_DIRNAME:
            src = Path(spec.src)
            if src.is_file() and holds_gcode(src):
                model = parts[2]
        if model and model not in found:
            found.append(model)
    return found


def _structural_check(files: Sequence[FileSpec], process: Optional[Process]) -> None:
    """The release's own content against MCAD_base's structural rules, reading only each
    file's src, so this runs before anything is written."""
    dests = [f.dest for f in files]
    if not any(d == CAD_DIRNAME or d.startswith(f"{CAD_DIRNAME}/") for d in dests):
        raise ReleaseStructureError(f"a design's revision needs at least one file under {CAD_DIRNAME}/")
    have = models_in(files)
    for model in (process.proven_on if process else []):
        if model not in have:
            raise ReleaseStructureError(
                f"manifest would say proven on {model!r} but no G-code or NC program for it "
                "is among the files being released", model=model)


def plan_release(
    root: Path,
    design: str,
    files: Sequence[Union[FileSpec, dict]],
    part_number: str,
    reason: str,
    released_by: str,
    approval: Union[Approval, dict],
    revision: Optional[str] = None,
    description: Optional[str] = None,
    process: Optional[Union[Process, dict]] = None,
    supersedes: Optional[str] = None,
    source: Optional[dict] = None,
    retention: Optional[dict] = None,
    shop_extension: Optional[Tuple[str, object]] = None,
) -> ReleasePlan:
    """Validate everything and build the manifest, writing nothing."""
    require_store(root)
    specs = [_coerce(f, FileSpec) for f in files]
    approval = _coerce(approval, Approval)
    process = _coerce(process, Process)
    if not specs:
        raise InvalidInputError("a release needs at least one file")
    for name, value in (("part_number", part_number), ("reason", reason), ("released_by", released_by)):
        if not value or not str(value).strip():
            raise InvalidInputError(f"{name} is required")
    if not approval.reviewed_by or not approval.approved_by:
        raise InvalidInputError("approval needs reviewed_by and approved_by; they may be the releaser")

    design_path = resolve_design(root, design)
    released_dir = design_path / RELEASED_DIRNAME

    if revision is None:
        revision_name = next_revision_name(released_dir)
    else:
        if not REV_RE.match(revision):
            raise InvalidInputError(f"revision name {revision!r} must match rev-<lowercase letters/digits>")
        revision_name = revision
    revision_dir = released_dir / revision_name
    resumed = False
    if revision_dir.is_dir():
        if (revision_dir / SUMS_FILE).exists():
            raise RevisionFrozenError(f"{revision_name} is already released and frozen", revision=revision_name)
        resumed = True

    seen = set()
    for spec in specs:
        validate_dest(spec.dest)
        if spec.dest in seen:
            raise InvalidInputError(f"duplicate destination in this release: {spec.dest!r}")
        seen.add(spec.dest)
        check_path_length(root, revision_dir / spec.dest)
        if not Path(spec.src).is_file():
            raise NotFoundError(f"source file does not exist or is not a file: {spec.src!r}")
    if approval.review_record and approval.review_record not in seen:
        raise InvalidInputError(f"review_record {approval.review_record!r} is not among the files being released")
    if supersedes is not None and not REV_RE.match(supersedes):
        raise InvalidInputError(f"supersedes must name a revision, got {supersedes!r}")

    _structural_check(specs, process)

    manifest: dict = {
        "schema": SCHEMA_VERSION,
        "part_number": part_number,
        "revision": revision_name,
        "released_at": _now_iso(),
        "released_by": released_by,
        "reason": reason,
        "approval": {"reviewed_by": approval.reviewed_by, "approved_by": approval.approved_by},
    }
    if approval.reviewed_at:
        manifest["approval"]["reviewed_at"] = approval.reviewed_at
    if approval.approved_at:
        manifest["approval"]["approved_at"] = approval.approved_at
    if approval.review_record:
        manifest["approval"]["review_record"] = approval.review_record
    if description:
        manifest["description"] = description
    if source:
        manifest["source"] = source
    if process and process.as_dict():
        manifest["process"] = process.as_dict()
    if supersedes:
        manifest["supersedes"] = supersedes
    if retention:
        manifest["retention"] = retention
    if shop_extension:
        key, value = shop_extension
        manifest[key] = value
    manifest["files"] = {spec.dest: (spec.metadata or {}) for spec in specs}

    prior = current_revision(design_path)
    return ReleasePlan(design_path, released_dir, revision_name, revision_dir, resumed, manifest,
                       prior if prior != revision_name else None, specs)


def plan_summary(plan: ReleasePlan, root: Path, dry_run: bool) -> dict:
    return {
        "ok": True,
        "dry_run": dry_run,
        "revision": plan.revision,
        "path": store_path(plan.revision_dir, root),
        "resumed_incomplete_revision": plan.resumed_incomplete_revision,
        "would_obsolete" if dry_run else "obsoleted": plan.prior_current,
        "files": [spec.dest for spec in plan.files],
        "manifest": plan.manifest,
    }


# ---------- writing a release ----------

def _make_read_only(revision_dir: Path) -> None:
    """Step 8 of the by-hand procedure; best effort, since some shares ignore mode bits."""
    for dirpath, dirnames, filenames in os.walk(revision_dir, topdown=False):
        for name in filenames + dirnames:
            with contextlib.suppress(OSError):
                path = Path(dirpath) / name
                path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    with contextlib.suppress(OSError):
        revision_dir.chmod(revision_dir.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _mark_obsolete(prior_dir: Path) -> None:
    """The one file that may be added to a frozen revision; the folder may be read-only."""
    marker = prior_dir / OBSOLETE_MARKER
    try:
        marker.write_text("", encoding="utf-8")
    except PermissionError:
        mode = prior_dir.stat().st_mode
        prior_dir.chmod(mode | stat.S_IWUSR)
        try:
            marker.write_text("", encoding="utf-8")
        finally:
            prior_dir.chmod(mode)


def _outbox_copies(root: Path, revision_dir: Path) -> List[str]:
    """Copy each machine's files to _outbox/<model>/ where that folder exists."""
    updated = []
    candidates: List[Tuple[str, Path]] = []
    cam_dir = revision_dir / CAM_DIRNAME
    if cam_dir.is_dir():
        candidates += [(m, cam_dir / m) for m in sorted(os.listdir(cam_dir)) if not ignored(m)]
    build_dir = revision_dir / BUILD_DIRNAME
    if build_dir.is_dir():
        for process_name in sorted(os.listdir(build_dir)):
            process_dir = build_dir / process_name
            if process_dir.is_dir():
                candidates += [(m, process_dir / m) for m in sorted(os.listdir(process_dir)) if not ignored(m)]
    for model, model_dir in candidates:
        outbox_dir = root / OUTBOX_DIRNAME / model
        if not model_dir.is_dir() or not outbox_dir.is_dir():
            continue
        for name in sorted(os.listdir(model_dir)):
            if ignored(name):
                continue
            shutil.copy2(model_dir / name, outbox_dir / name)
            updated.append(f"{model}/{name}")
    return updated


def write_release(root: Path, plan: ReleasePlan) -> dict:
    """Carry out a plan, in the by-hand order: files, manifest, SHA256SUMS, CURRENT, OBSOLETE,
    history, outbox, read-only, then the store's own check as a self-test."""
    _acquire_lock(plan.released_dir, plan.revision)
    outbox_updated: List[str] = []
    try:
        with translate_os_errors(plan.revision_dir):
            if plan.resumed_incomplete_revision:
                shutil.rmtree(plan.revision_dir)
            plan.revision_dir.mkdir(parents=True)
            for spec in plan.files:
                dest_path = plan.revision_dir / spec.dest
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(spec.src, dest_path)
            (plan.revision_dir / MANIFEST_FILE).write_text(json.dumps(plan.manifest, indent=2) + "\n",
                                                           encoding="utf-8")
            sums = [f"{file_digest(plan.revision_dir / name)}  {name}\n"
                    for name in sorted(revision_files(plan.revision_dir))]
            (plan.revision_dir / SUMS_FILE).write_text("".join(sums), encoding="utf-8")

            current_tmp = plan.released_dir / f"{CURRENT_FILE}.tmp"
            current_tmp.write_text(plan.revision, encoding="utf-8")
            os.replace(current_tmp, plan.released_dir / CURRENT_FILE)

            if plan.prior_current:
                _mark_obsolete(plan.released_dir / plan.prior_current)

            with (plan.design_path / HISTORY_FILE).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": _now_iso(), "event": "released", "revision": plan.revision}) + "\n")
                if plan.prior_current:
                    fh.write(json.dumps({"at": _now_iso(), "event": "obsoleted",
                                         "revision": plan.prior_current}) + "\n")

            outbox_updated = _outbox_copies(root, plan.revision_dir)
            _make_read_only(plan.revision_dir)

        problems, _notes = check_tree(root)
        rel = store_path(plan.revision_dir, root)
        mine = [p for p in problems if rel in p]
        if mine:
            raise InternalError("the release just written fails the store's own check", problems=mine)
    finally:
        _release_lock(plan.released_dir)

    result = plan_summary(plan, root, dry_run=False)
    result["outbox_updated"] = outbox_updated
    return result


def create_release(
    root: Path,
    design: str,
    files: Sequence[Union[FileSpec, dict]],
    part_number: str,
    reason: str,
    released_by: str,
    approval: Union[Approval, dict],
    revision: Optional[str] = None,
    description: Optional[str] = None,
    process: Optional[Union[Process, dict]] = None,
    supersedes: Optional[str] = None,
    source: Optional[dict] = None,
    retention: Optional[dict] = None,
    shop_extension: Optional[Tuple[str, object]] = None,
    dry_run: bool = False,
) -> dict:
    """Release a new, frozen revision of a design. See the module docstring."""
    plan = plan_release(root, design, files, part_number, reason, released_by, approval,
                        revision=revision, description=description, process=process,
                        supersedes=supersedes, source=source, retention=retention,
                        shop_extension=shop_extension)
    if dry_run:
        return plan_summary(plan, root, dry_run=True)
    return write_release(root, plan)


def release_from_wip(
    root: Path,
    design: str,
    reason: str,
    released_by: str,
    approval: Union[Approval, dict],
    part_number: Optional[str] = None,
    description: Optional[str] = None,
    revision: Optional[str] = None,
    extra_files: Sequence[Union[FileSpec, dict]] = (),
    proven_on: Optional[Sequence[str]] = None,
    process: Optional[Union[Process, dict]] = None,
    skip_unknown: bool = False,
    dry_run: bool = False,
) -> dict:
    """Release what is in the design's wip/: the by-hand procedure's usual case.

    Files are placed by ``place_wip_file``. ``part_number`` and ``description`` fall back to
    design.json. ``proven_on`` defaults to every machine model the files hold a program for.
    A design synced from Fusion gets a ``source`` block from design.json.
    """
    design_path = resolve_design(root, design)
    specs, unplaced = wip_files(design_path, skip_unknown=skip_unknown)
    specs += [_coerce(f, FileSpec) for f in extra_files]

    info = read_json(design_path / DESIGN_FILE) or {}
    part_number = part_number or info.get("part_number")
    if not part_number:
        raise InvalidInputError("no part number: pass one, or put part_number in the design's design.json")
    description = description or info.get("description")

    process = _coerce(process, Process) or Process()
    if proven_on is not None:
        process.proven_on = list(proven_on)
    elif not process.proven_on:
        process.proven_on = models_in(specs)

    source = None
    if info.get("fusion_item_id"):
        wip = info.get("wip") or {}
        source = {k: v for k, v in {
            "hub": info.get("hub"), "project": info.get("project"),
            "fusion_item_id": info.get("fusion_item_id"),
            "fusion_version_id": wip.get("version_id"),
            "fusion_version_number": wip.get("version_number"),
        }.items() if v is not None}

    result = create_release(root, design, specs, part_number, reason, released_by, approval,
                            revision=revision, description=description, process=process,
                            source=source, dry_run=dry_run)
    if unplaced:
        result["skipped"] = unplaced
    return result
