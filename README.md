# MCAD_base

Product data management (PDM) for mechanical CAD, kept in plain folders on
a NAS or any shared drive. The file system is the database: every record is
a plain file in the tree, with no server and no separate database, so the
store stays readable without these tools.

Every design has one folder holding its working files, its frozen released
revisions, and the record of what was built from them. The layout works
with any CAD or CAM system, for printed and machined parts.

It is built to help a shop meet ISO 9001: the records an auditor asks for
are files in the design's own folder. See [ISO alignment](#iso-alignment).

The tools do not set how a shop works. They check a store against the
layout and report what is missing; nothing blocks a save or rewrites your
files.

Because the store is only files, the shop backs it up, copies it, or zips
it with whatever it already uses, and controls access with the operating
system's own users and permissions.

The only dependency is Python 3.9 or newer. See
[Requirements](#requirements).

## Quick start

```sh
git clone https://github.com/kgorman/MCAD_base.git
cd MCAD_base
./mcad_tree.py init /path/to/store                    # the folder must already exist
mkdir -p "/path/to/store/Brackets/Motor Mount/wip"    # a design is just a folder
./mcad_tree.py check /path/to/store
```

Then save your CAD files into `wip/` and carry on working. On Windows the
commands differ slightly; see [Windows](#windows).

## Requirements

- **Python 3.9 or newer.** The tools use only the standard library, so
  there are no packages to install, no database, and no server.
- **macOS, Linux, or Windows.**
- **A folder for the store:** a local disk, a NAS share, or any shared
  drive the operating system can mount.
- **For the Fusion sync only:** an Autodesk account, internet access, and a
  web browser to sign in.

Getting Python:

- macOS: `xcode-select --install` installs Apple's command line tools,
  which include `python3`.
- Linux: most distributions ship `python3`; otherwise install it with the
  package manager.
- Windows: install it from [python.org](https://www.python.org/downloads/)
  or the Microsoft Store.

Development and testing so far have been on macOS. Linux uses the same
commands.

### Windows

```bat
git clone https://github.com/kgorman/MCAD_base.git
cd MCAD_base
py mcad_tree.py init D:\store
mkdir "D:\store\Brackets\Motor Mount\wip"
py mcad_tree.py check D:\store
```

- Run each tool with `py` in front instead of `./`. `py` is the launcher
  that comes with Python from python.org; with the Microsoft Store version
  use `python`.
- The store can be on a local drive or a mapped network drive.
- Run the tests the same way: `py tests\test_mcad_tree.py`.
- The Fusion sync's background schedule (`install-launchd`) is for macOS.
  On Windows, run `py fusion_sync.py sync` from Task Scheduler.

The tools have not been run on Windows yet. Known gaps are tracked in
[#8](https://github.com/kgorman/MCAD_base/issues/8) (the index writes
backslash paths) and
[#9](https://github.com/kgorman/MCAD_base/issues/9) (long paths, reserved
names, and token storage in the sync).

## The store

```
<root>/
  <any folders>/.../<design>/
    wip/              work in progress: where you work
    released/         rev-a/, rev-b/, CURRENT; frozen once signed off
    builds/           what actually ran
    design.json       optional: part number, description
    history.jsonl     ledger: released, built, synced, moved
    (anything else)   yours
  _index/parts.csv    generated: part number -> folder
  _outbox/<machine>/  flat copies for machines, only if needed
```

A folder is a design as soon as it holds `wip/`, `released/`, or `builds/`.
Arrange the folders above a design however the shop likes: by customer, by
project, by product.

Working files and releases are different things and both live in the
design's folder. `wip/` changes whenever the design does. A revision under
`released/` never changes after it is signed off. Machine files (G-code, NC
programs) are kept inside the revision, one folder per machine model.

The full layout, its rules, the file formats, and the ISO mapping are in
[docs/CANONICAL_TREE.md](docs/CANONICAL_TREE.md).

## Set up a store

```sh
./mcad_tree.py init /path/to/store --dry-run   # show what would be created
./mcad_tree.py init /path/to/store             # create it
```

`init` writes `README.md`, `SCHEMA.md`, and a schema version marker. It
creates no design folders; you make those, or a sync tool does.

- It never overwrites, moves, or deletes. `--update-docs` rewrites the two
  docs when the store's copies are out of date.
- It refuses to run if the root folder does not exist, so an unmounted share
  does not get a store built on the local disk in its place.

## Start a design

Make a folder for the design anywhere below the store root, make `wip/`
inside it, and work there with whatever CAD system the shop uses. No
command is needed; `check`, `verify`, and `index` pick the folder up.

Add a `design.json` to give the design a part number and a description.

## Check and index

```sh
./mcad_tree.py check /path/to/store    # find gaps; non-zero exit on problems
./mcad_tree.py verify /path/to/store   # check, plus re-hash every released file
./mcad_tree.py index /path/to/store    # writes _index/parts.csv
./mcad_tree.py add-machine /path/to/store haas-vf2
./mcad_tree.py upgrade /path/to/store  # move a store to a newer schema
./mcad_tree.py --version               # tool version and schema version
```

`check` finds gaps without hashing anything, so it is quick:

- a `CURRENT` that names no revision;
- a revision without `manifest.json`, `SHA256SUMS`, CAD, a reviewer, or an
  approver;
- a file the manifest or `SHA256SUMS` lists that is not there, or a file in
  a revision that `SHA256SUMS` does not list;
- a machine model the revision claims to be proven on with no G-code or
  NC program for it (a 3MF counts only if it has G-code inside);
- a build that does not say what ran, has no inspector or acceptor, or has
  rejected parts with no disposition;
- the first build on an unproven machine model accepted without an
  inspection record;
- names inside `released/` that break the naming rule (lowercase ASCII,
  digits, `-`, `_`, `.`, path under 200 characters). Names above the
  revision folder are the shop's own and are not policed;
- two folders claiming the same Fusion item (synced designs only).

`verify` does all of that and re-hashes every released file against its
`SHA256SUMS`, which reads the whole store.

## Versions

The layout has a schema version, stored in the store's `.mcad-tree.json`;
it is 2 today. The tools have their own version, shown by `--version`.

Adding an optional folder, file, or field does not change the schema
version and needs no upgrade. A change that would make a valid store
invalid raises it, and `mcad_tree.py upgrade` converts the store. An
upgrade never rewrites a released revision: each revision records the
schema it was released under and is checked by that version's rules.

The rules and the change log are in
[docs/CANONICAL_TREE.md](docs/CANONICAL_TREE.md#versions).

## Backups

The store is a folder of ordinary files, so back it up the way the shop
backs up any files: NAS snapshots, `rsync`, Time Machine, a cloud backup
service, or a zip of the whole tree. There is nothing to export or dump
first.

- A copy of the tree is a complete, working store. Point the tools at the
  copy and they run.
- One design folder can be copied or zipped on its own. Everything about
  the design is inside it.
- After a restore, `verify` re-hashes every released file against its
  `SHA256SUMS` and reports any that did not come back intact.

## Users and permissions

MCAD_base has no accounts of its own. Its users are the users of the host
operating system or the NAS, and the permissions model is the file
system's: whoever can read a folder can read the design, and whoever can
write to it can change it.

Set access with the tools the shop already has: share permissions, groups,
and read-only folders. A shop that wants released revisions locked makes
`released/` read-only to everyone but the person who releases.

## ISO alignment

The layout is aligned with three standards, so that working in it produces
the records a quality system needs:

- **ISO 9001, quality management.** Every revision is identified and names
  who reviewed and approved it. Released revisions are frozen and
  checksummed, so they are protected from unintended change. A build
  record ties a physical part to the revision and the exact file that made
  it, and records the inspection, who accepted it, and what happened to
  any rejected parts.
- **ISO 10007, configuration management.** The design folder is the
  configuration item, each released revision is a baseline, and
  `history.jsonl` is the status accounting.
- **ISO 19650, information management.** `wip/` is the standard's own name
  for information still being authored. A released revision is the
  published state, and obsolete revisions are the archive.

The clause-by-clause mapping is in
[docs/CANONICAL_TREE.md](docs/CANONICAL_TREE.md#mapping-to-the-standards).
`check` and `verify` report where a store falls short of it.

## CAD systems

Any CAD or CAM system that saves files works today: save into `wip/`. A
system with its own cloud or vault can also have a sync tool that fills the
tree for you. Fusion is the first.

### Autodesk Fusion

`fusion_sync.py` mirrors Fusion cloud hubs into the store, one way and
incrementally, so you keep designing in Fusion as before and the files
appear in the tree.

```sh
./fusion_sync.py init --client-id <APS_CLIENT_ID> --root /path/to/store
./fusion_sync.py auth
./fusion_sync.py sync --dry-run
./fusion_sync.py sync --formats native,step
```

- Set the store up with `mcad_tree.py init` first. The sync stops if the
  folder is not a store, so it never builds the tree on the local disk
  when a share is not mounted.
- The folders follow Fusion: hub, project, folder, design. A design is on
  disk where it is in the data panel.
- Each design's latest cloud export is kept in its `wip/`. The sync
  replaces only the files it wrote and leaves everything else there alone.
- Identity is the Fusion item id in `design.json`, not the path. A rename
  or move in the cloud moves the folder, with its releases and builds.
- A delete in the cloud marks the folder and removes nothing.
- An assembly that links to other designs is exported as one `.f3z`
  archive.

Setup, formats, and scheduling are in
[docs/FUSION_SYNC.md](docs/FUSION_SYNC.md). The sync has been tested
offline only; it has not yet run against a live Autodesk account.

## Compatible products

A product that reads or writes a store can call itself "MCAD_base 2
compatible" when what it writes passes `check`, it writes only where its
kind of tool may write, and everything it writes stays readable with the
tools in this repository alone. The definition is in
[docs/CANONICAL_TREE.md](docs/CANONICAL_TREE.md#mcad_base-compatible).

Building one with an AI coding agent? Point it at
[docs/AGENTS.md](docs/AGENTS.md) first. It lists the rules, what `check`
enforces, and how to test the result.

## What's here

| File | What it does |
|---|---|
| `mcad_tree.py` | Marks a folder as a store, checks it against the layout, writes the part-number index. |
| `fusion_sync.py` | Mirrors Fusion cloud hubs into the store. |
| `docs/CANONICAL_TREE.md` | The layout, its rules, the file formats, and the ISO mapping. Copied into the store as `SCHEMA.md`. |
| `docs/FUSION_SYNC.md` | Setting up and running the Fusion sync. |
| `docs/AGENTS.md` | What an AI coding agent needs to know to write compatible code. |
| `tests/` | Offline tests for both tools and for the two working together. |

## Tests

```sh
python3 tests/test_mcad_tree.py
python3 tests/test_fusion_sync.py
python3 tests/test_store.py
```

## License

Apache-2.0, for everything in this repository: the layout, `mcad_tree.py`,
and `fusion_sync.py`. See [LICENSE](LICENSE).

## Not built yet

- Release: build `released/<rev>/` from the working files, collect the
  reviewer, approver, and review records, write the manifest and
  `SHA256SUMS`, set `CURRENT`, mark the prior revision `OBSOLETE`, append
  to `history.jsonl`, copy to `_outbox/` if a machine needs it. Revisions
  are assembled by hand until then.
- Build logging: nothing writes `builds/` yet; `build.json` and
  `nonconformance.json` are filled in by hand.
- Running `verify` on a schedule.
- Assemblies whose files sit in more than one design folder.
- Fusion: a run of the sync against a live account, and an add-in that
  provides a Release command.
