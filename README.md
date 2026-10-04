# MCAD_base

An open-source folder standard for manufacturing. It sets how a shop that
makes parts by CNC machining and additive manufacturing keeps its design
files, in plain folders on a NAS or any shared drive. The file system is
the database: every record is a plain file in
the tree, with no server and no separate database, so the store stays
readable without these tools.

Every design has one folder holding its working CAD files, its frozen
released revisions, the G-code and NC programs its machines run, and the
record of what was made from them. The layout works with any CAD or CAM
system, and printers and CNC machines take their files straight from a
released revision. For Autodesk Fusion, the included sync tool copies
every design from the cloud into the store. See
[docs/FUSION_SYNC.md](docs/FUSION_SYNC.md).

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

## The workflow

A design goes around one loop, and every turn leaves files in its folder.

1. **Design.** Work in `wip/`. Save as often as you like; nothing here is
   a record. A Fusion sync fills `wip/` for you. See
   [Start a design](#start-a-design).
2. **Release.** When the design is good, copy it into `released/rev-a/`
   with its drawings and the program for each machine model, write the
   manifest naming who reviewed and approved it, and write the checksums.
   The folder is frozen from then on, and `released/CURRENT` names it.
   See [Release a revision](#release-a-revision).
3. **Run.** A printer or CNC machine takes its file from the current
   revision, never from `wip/`. See
   [Get a release to a printer or CNC machine](#get-a-release-to-a-printer-or-cnc-machine).
4. **Record.** Each run gets a folder under `jobs/` with a copy of the
   exact file that ran and a `job.json` saying what, on which machine, who
   inspected it, and whether it was accepted. See
   [Record a job](#record-a-job).
5. **Change.** When the design changes, keep working in `wip/`. When the
   change is good, release `rev-b` the same way, point `CURRENT` at it,
   and drop an `OBSOLETE` marker in `rev-a`. Nothing in `rev-a` is edited
   or deleted; the jobs that ran it still point at it.
6. **Check.** Run `check` whenever you like and `verify` on a schedule.
   They report what is missing and change nothing. See
   [Check and index](#check-and-index).

After one turn of the loop a design looks like this:

```
Motor Mount/
  design.json
  history.jsonl                  synced, released, ran
  wip/
    motor-mount.f3d              the latest working version
  released/
    CURRENT                      "rev-a"
    rev-a/
      manifest.json
      SHA256SUMS
      cad/motor-mount.f3d
      cad/motor-mount.step
      build/fdm/bambu-p1s/motor-mount_rev-a.3mf
  jobs/
    2026-10-02_p1s-01_j0001/
      motor-mount_rev-a.3mf      the exact file that ran
      job.json
```

Three writers, three areas: you or a sync tool write `wip/`, whoever
releases writes `released/`, whoever runs the machine writes `jobs/`.
None of them touches the other two.

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
[#9](https://github.com/kgorman/MCAD_base/issues/9) (long paths and token
storage in the sync).

## The store

```
<root>/
  <any folders>/.../<design>/
    wip/              work in progress: where you work
    released/         rev-a/, rev-b/, CURRENT; frozen once signed off
    jobs/             what actually ran
    design.json       optional: part number, description
    history.jsonl     ledger: released, ran, synced, moved
    (anything else)   yours
  _index/parts.csv    generated: part number -> folder
  _outbox/<machine>/  flat copies for machines, only if needed
```

A folder is a design as soon as it holds `wip/`, `released/`, or `jobs/`.
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

Make a folder for the design anywhere below the store root, make `wip/`,
`released/`, and `jobs/` inside it, and work in `wip/` with whatever CAD
system the shop uses. No command is needed; `check`, `verify`, and `index`
pick the folder up. The empty `released/` and `jobs/` show where releases
and job records go.

With a Fusion sync you make nothing: each design in the cloud gets its
folder, its three subfolders, and its latest version in `wip/` on every
run. See [Autodesk Fusion](#autodesk-fusion).

Add a `design.json` to give the design a part number and a description.

## Release a revision

A release is made by hand, with a file manager and a terminal:

1. Make `released/rev-a/` in the design folder.
2. Copy in the CAD, drawings, and the program for each machine model, with
   lowercase names and no spaces.
3. Write a short `manifest.json` naming who reviewed and approved it.
4. Write `SHA256SUMS` over the folder. The revision is now frozen.
5. Put the revision name in `released/CURRENT`.

The full steps, with the manifest and the checksum command, are in
[docs/CANONICAL_TREE.md](docs/CANONICAL_TREE.md#releasing-a-revision-by-hand).
`verify` confirms the result.

### What stays in `wip/` and what goes in the release

`wip/` holds what you are working on now. The release holds everything that
made the part, copied at the moment it was approved. A source file is in
both: the copy in `wip/` keeps changing, the copy in the release does not.

| | In `wip/` | In `released/rev-a/` |
|---|---|---|
| CAD | the design, as it is today | `cad/`: the design as released, and a STEP |
| Printed part | the slicer project and trial slices | `build/<process>/<model>/`: the G-code, and the slicer project that made it |
| Machined part | the CAM file and trial posts | `cam/<model>/`: the NC program and its setup sheet |

The frozen copy of the slicer project matters because `wip/` moves on.
Later, it is the only thing that says which settings made the released
G-code, and it is what you reopen to slice the same revision for a second
printer model. A sliced 3MF is not a substitute: the one Bambu Studio
exports holds the settings and the G-code but not the model.

## Get a release to a printer or CNC machine

Machines run files from a released revision, never from `wip/`.
`released/CURRENT` names the revision to run, and inside it each machine
model has its own folder:

- a printer's G-code or sliced 3MF is in `build/<process>/<model>/`, for
  example `released/rev-a/build/fdm/bambu-p1s/`;
- a CNC machine's NC programs and setup sheets are in `cam/<model>/`, for
  example `released/rev-a/cam/haas-vf2/`.

Three ways to get the file to the machine:

- **Open it from the revision folder.** A PC at the machine, a slicer, print
  farm software, or DNC software reads it where it is. Read-only access is
  enough.
- **Carry it.** Copy it to a USB stick or an SD card.
- **Use an outbox.** For a control that mounts a network share but cannot
  handle deep folders or long names, `add-machine` makes a flat
  `_outbox/<machine>/` folder. Copy that machine's programs into it at
  each release and share only that folder with the machine.

More in
[docs/CANONICAL_TREE.md](docs/CANONICAL_TREE.md#getting-a-release-to-a-machine).

### Example: a Tormach and a Haas on the same NAS

A revision holds one program per machine model, and at release each is
copied into that machine's flat outbox. The setup sheet and the drawing go
into a second folder, for a tablet or PC at the machine:

```
Motor Mount/released/rev-a/cam/tormach-pcnc440/o0042_op10_rev-a.nc
Motor Mount/released/rev-a/cam/haas-vf2/o0042_op10_rev-a.nc
_outbox/tormach-pcnc440/o0042_op10_rev-a.nc     <- the store pushes this to the Tormach
_outbox/haas-vf2/o0042_op10_rev-a.nc            <- the Haas reads this over the network
_outbox/haas-vf2-docs/o0042_op10_rev-a_setup-sheet.pdf
_outbox/haas-vf2-docs/motor-mount_rev-a.pdf
```

The revision is in the file name, and in a comment on the program's first
line, so the operator can see at the control which revision is loaded.

The two machines take it in opposite directions:

- **Tormach, PathPilot: the store pushes.** PathPilot shares its own
  G-code folder on the network as `gcode` and cannot mount anyone else's,
  so at each release you copy the outbox into `\\tormach\gcode`. The
  program then shows under Controller Files in the File tab. Mounting the
  NAS on PathPilot through `/etc/fstab` works too, but Tormach does not
  support it.
- **Haas, Next Generation Control: the machine pulls.** The control's
  Remote Net Share mounts a NAS share and lists it as a device under LIST
  PROGRAM. Make a share named `haas-vf2` that points at
  `_outbox/haas-vf2/`, with a read-only user; the share setting takes a
  name with no spaces, which is why the outbox is flat. After that a
  release needs nothing at the machine: it reads the new file next time.

Settings, NAS setup, SMB versions, what the operator has at the machine,
and the sources are in [docs/MACHINES.md](docs/MACHINES.md).

## Record a job

A job is one run of one revision on one machine. It is recorded by hand,
or by print farm or shop-floor software, the same way:

1. Make `jobs/<date>_<machine>_<job>/` in the design folder, for example
   `jobs/2026-10-02_p1s-01_j0001/`. The date first, so jobs sort in the
   order they ran.
2. Copy in the exact file that ran, from the revision folder.
3. Write `job.json` naming the revision, the machine model, the machine,
   the file, the operator, and the material lot, with `result` set to
   `pending`.
4. At inspection, complete it: how many were made and accepted, who
   inspected, how, and who accepted. Set `result` to `accepted` or
   `rejected`. It is not changed after that.
5. If anything was rejected, write `nonconformance.json` beside it: what
   was wrong, the disposition, and who decided.
6. Add a line to `history.jsonl`:
   `{"at": "...", "event": "ran", "job": "2026-10-02_p1s-01_j0001"}`.

The first job on a machine model the revision is not yet proven on is a
first article: it needs an inspection record before it can be accepted.
The fields are in
[docs/CANONICAL_TREE.md](docs/CANONICAL_TREE.md#jobjson-for-a-job).

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
- a job that does not say what ran, has no inspector or acceptor, or has
  rejected parts with no disposition;
- the first job on an unproven machine model accepted without an
  inspection record;
- names inside `released/` that break the naming rule (lowercase ASCII,
  digits, `-`, `_`, `.`, path under 200 characters). Names above the
  revision folder are the shop's own and are not policed;
- two folders claiming the same Fusion item (synced designs only).

`verify` does all of that and re-hashes every released file against its
`SHA256SUMS`, which reads the whole store.

Run `verify` on a schedule so a damaged or changed release is found early.
It exits non-zero when it finds a problem, so any scheduler can alert on
it. With cron on macOS or Linux, weekly on Sunday at 03:00:

```
0 3 * * 0 /path/to/MCAD_base/mcad_tree.py verify /path/to/store >> /path/to/verify.log 2>&1
```

On Windows, run `py mcad_tree.py verify D:\store` from Task Scheduler.

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
  checksummed, so they are protected from unintended change. A job
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
  or move in the cloud moves the folder, with its releases and jobs.
- A delete in the cloud marks the folder and removes nothing.
- Each design is kept in both of Fusion's formats: the `.f3d` as the cloud
  stores it, and a `.f3z` archive, which also carries the other designs an
  assembly links to.

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
| `docs/MACHINES.md` | Getting programs to a Tormach and a Haas: two worked examples. |
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

## Not done yet

- Assemblies whose files sit in more than one design folder.
- A run of the Fusion sync against a live Autodesk account.
- A run of either tool on Windows.
