# MCAD_base store layout

One root on a NAS or shared drive, and one folder per design. Everything
about a design lands in that design's folder: the CAD, the released
revisions, the G-code and NC programs the machines run, and the record of
what was made. It serves printed and machined parts alike. Schema
version 2.

The layout does not set how a shop works. It says where things end up, and
`check` reports where a store differs from it.

The project, and inside it the design, is the container. State lives inside
the container: work in progress, released revisions, and job records are
subfolders of the design, not separate trees.

## Rules

1. **The folders above a design are the shop's.** Arrange them however
   the shop likes: by customer, by project, by product. For designs synced
   from a CAD system's cloud they follow that system instead. For Fusion
   that is hub, project, folder, so a design is found on disk where it is
   found in Fusion.
2. **One folder per design.** The folder is the configuration item. Nothing
   about a design lives outside its folder.
3. **A design is known by its path, or by its source's id when it is
   synced.** A design made by hand is known by its path. A design synced
   from Fusion carries the Fusion item id in `design.json`; a rename or
   move in the cloud moves the folder, and the id does not change. A part
   number is a field in `design.json`, added when one exists.
4. **Three reserved names, three writers.**
   - `wip/` is work in progress, and it is the working folder for every
     design, whatever CAD system made it. For a design synced from Fusion
     the sync tool also keeps the latest cloud export here. It replaces
     only the files it wrote and leaves everything else in `wip/` alone.
   - `released/` is written only at release, by a person or by a tool.
     Append-only; a revision folder is never modified after its
     `SHA256SUMS` is written.
   - `jobs/` holds records of what actually ran. Append-only.

   Anything else in the design folder is yours: photos, loose STLs, notes.
5. **Every sign-off names a person.** A revision's manifest names who
   reviewed it and who approved it. A job names who inspected it and who
   accepted it. Rejected parts get a nonconformance record naming who
   decided what happened to them. One person may hold several of these
   roles; the name is still written down each time.
6. **Sync never touches what it does not own.** Inside a design folder it
   writes its own exports in `wip/`, plus `design.json`, `history.jsonl`,
   and the `DELETED_IN_CLOUD` marker. Nothing else, ever, including other
   files in `wip/`.
7. **A revision folder is immutable.** New revision, new folder. Obsolete
   revisions stay; they get an `OBSOLETE` marker file, not a delete.
8. **Nothing is deleted because the cloud deleted it.** The folder stays
   and gets a `DELETED_IN_CLOUD` marker.
9. **`CURRENT` is a file, not a symlink.** SMB clients and CNC controls
   don't follow symlinks reliably. `CURRENT` contains one line: the
   current revision string.
10. **Safe names inside `released/`.** Lowercase ASCII, digits, `-`, `_`,
    `.`, no spaces, total path under 200 characters. Names above the
    revision folder are the shop's or the CAD system's and are not
    policed.
11. **Machine files are kept per machine model.** G-code and NC programs
    are valid for one machine model only, so each sits in a folder named
    for its model, with the slicer project and settings that made it.
12. **Integrity is explicit.** `SHA256SUMS` in every revision folder,
    generated last.
13. **Machines run released files, and only those.** A machine's program
    is in the current revision, in the folder for its model. A machine
    that cannot reach or read the tree gets copies in a flat
    `_outbox/<machine>/`. The outbox is a copy; the revision folder is the
    home. See "Getting a release to a machine".

## Tree

```
<root>/                                     <- a folder on the NAS; back this whole thing up off-site
  .mcad-tree.json                           <- schema version marker, written by mcad_tree.py init
  README.md
  SCHEMA.md                                 <- this document
  .fusion-sync/                             <- sync tool state: manifest.json, logs

  <folder>/                                 <- any folders, named and nested as the shop likes
    <folder>/...                            <- for a Fusion sync: <hub>/<project>/<folder>/
        <design>/                           <- ONE FOLDER PER DESIGN, e.g. Headset Spacers/
          design.json                       <- optional: part number, description; a synced design's id
          history.jsonl                     <- ledger: synced, moved, released, ran
          DELETED_IN_CLOUD                  <- marker, only if the design is gone from the cloud

          wip/                              <- WORK IN PROGRESS. The working folder for every design.
            <design>.f3d                    <- synced designs: native file, latest cloud version; sync replaces it
            <design>.f3z                    <- the same version as an archive, with the designs it references
            <design>.step
            _versions/                      <- prior copies when the cloud version changed
              <design>.v13.f3d

          released/                         <- RECORDS. Written at release. Frozen.
            CURRENT                         <- text file: "rev-b"
            rev-a/
              manifest.json                 <- part, rev, source version, who, when, why, reviewer, approver
              CHANGELOG.md                  <- what changed from the prior rev
              SHA256SUMS                    <- written last; folder frozen after this
              OBSOLETE                      <- marker, only once superseded
              cad/
                bm-0042.f3d
                bm-0042.step
              drawings/
                bm-0042_rev-a.pdf
              mesh/
                bm-0042_rev-a.3mf           <- generic 3MF, slicer-neutral
                bm-0042_rev-a.stl
              build/                        <- additive, per process
                fdm/
                  bambu-p1s/                <- one folder per printer model the revision is proven on
                    bm-0042_rev-a.3mf       <- the slicer's own project file: model, settings, gcode
                    profile.json            <- optional: the slicer settings, as exported
                    bm-0042_rev-a.gcode     <- optional when the project file already holds the gcode
              cam/                          <- subtractive, per machine model
                haas-vf2/
                  o0042_op10_rev-a.nc
                  setup-sheet_op10.pdf
              docs/
                orientation.md              <- build orientation, supports, post-processing
              reviews/                      <- evidence behind the sign-off; frozen with the revision
                design-review.md            <- review of the design, and of the change from the prior rev
                inspection_first-article.pdf  <- verification and validation records
            rev-b/
              ...

          jobs/                             <- WHAT ACTUALLY RAN. Append-only.
            2026-09-27_p1s-01_b0173/
              bm-0042_rev-a.gcode           <- exact file that ran
              job.json                      <- printer, material lot, operator, inspection, who accepted it
              inspection.pdf                <- the inspection record, if there is a separate one
              nonconformance.json           <- only if parts were rejected: what failed, disposition, who decided

          (anything else)                   <- yours: photos, loose STLs, notes

  _index/
    parts.csv                               <- generated by mcad_tree.py index: part number -> folder
  _outbox/                                  <- only if a machine needs it; created by add-machine
    haas-vf2/                               <- flat, safe names; copies from released/, never edited
```

The `.f3d` and `.f3z` names and the `.fusion-sync/` folder are what a Fusion sync
leaves. A design made by hand has whatever its CAD system saves.

Two things particular to a Fusion sync:

- Uploaded files that are not Fusion designs (a vendor PDF, an imported
  STL) are items in the cloud too, and get the same treatment: a folder
  named for the file, with the file in `wip/`.
- A Fusion project normally has a single root folder. It is flattened away
  so the disk path reads `<hub>/<project>/<design>/`, the same as the
  Fusion data panel. A project with several root folders keeps their
  names.

## Starting a design by hand

No tool is needed. Make a folder for the design anywhere below the store
root, make `wip/`, `released/`, and `jobs/` inside it, and work in `wip/`
with whatever CAD system the shop uses. The folder is a design as soon as
it holds any one of the three, and `check`, `verify`, and `index` treat it
like any other. The empty `released/` and `jobs/` say where things go; a
sync tool makes them too.
`design.json` is optional for such a design; add one to give it a part
number and description.

## Releasing a revision by hand

A release is a folder of files plus two text files. It needs a file
manager and a terminal, and nothing else.

1. **Make the revision folder** in the design: `released/rev-a/`, then
   `rev-b`, and so on.
2. **Copy in what the revision is**, renamed to lowercase with no spaces:
   - CAD in `cad/`: the native file and a STEP;
   - drawings in `drawings/`;
   - for each printer model, the G-code or sliced 3MF in
     `build/<process>/<model>/`, for example `build/fdm/bambu-p1s/`;
   - for each machine tool model, the NC programs and setup sheets in
     `cam/<model>/`, for example `cam/haas-vf2/`;
   - review and inspection records in `reviews/`.
3. **Write `manifest.json`** in the revision folder. This much is enough:

   ```json
   {
     "schema": 2,
     "part_number": "bm-0042",
     "revision": "rev-a",
     "released_at": "2026-09-27T05:20:00-05:00",
     "released_by": "engineer@example.com",
     "reason": "Initial release",
     "approval": {
       "reviewed_by": "reviewer@example.com",
       "approved_by": "owner@example.com"
     },
     "process": { "proven_on": ["bambu-p1s", "haas-vf2"] }
   }
   ```

   `proven_on` lists the machine models this revision holds a program for.
4. **Write `SHA256SUMS` last.** From inside the revision folder, on macOS:

   ```sh
   find * -type f ! -name SHA256SUMS ! -name '.*' | sort | xargs shasum -a 256 > SHA256SUMS
   ```

   On Linux use `sha256sum` in place of `shasum -a 256`. On Windows run the
   Linux command in Git Bash, which comes with Git for Windows. The
   revision is now frozen.
5. **Point `CURRENT` at it:** `released/CURRENT` holds one line, the
   revision name.

   ```sh
   echo rev-a > released/CURRENT
   ```
6. **Mark the revision it replaces**, if there is one, by adding an empty
   file named `OBSOLETE` to that revision's folder. It is the one file
   that may be added to a frozen revision. If the folder was made
   read-only, allow writing to the folder itself first.
7. **Add a line to `history.jsonl`** in the design folder:

   ```
   {"at": "2026-09-27T05:20:00-05:00", "event": "released", "revision": "rev-a"}
   ```
8. **Make the revision read-only**, for example `chmod -R a-w released/rev-a`.
9. **Run `mcad_tree.py verify <root>`.** It reports anything missing.

A tool can do these steps for you. The result is the same files either
way, and `check` cannot tell the difference.

## Getting a release to a machine

A printer or a CNC machine runs a file from a released revision, never
from `wip/`.

**Where the file is.** `released/CURRENT` names the revision to run.
Inside that revision:

| Machine | Folder | What is there |
|---|---|---|
| A printer | `released/<rev>/build/<process>/<model>/` | G-code, or a sliced 3MF with the G-code inside |
| A machine tool | `released/<rev>/cam/<model>/` | NC programs and setup sheets |

Do not run a revision that holds an `OBSOLETE` file.

**How it gets to the machine.** Use whichever the machine supports:

1. **Open it from the revision folder.** A PC at the machine, a slicer
   sending to a printer, print farm software, or DNC software that can
   reach the store reads the file where it is. Read-only access to the
   store is enough.
2. **Carry it.** Copy the file from the revision folder to a USB stick or
   an SD card.
3. **Use an outbox.** Some controls can mount a network share but cannot
   cope with deep folders or long names. `mcad_tree.py add-machine <root>
   haas-vf2` makes `_outbox/haas-vf2/`, a flat folder to share with that
   one machine. At each release, copy that machine's programs from the
   revision into it, replacing the old ones. Nothing is edited there.

Worked examples for a Tormach (PathPilot) and a Haas (Next Generation
Control) are in [MACHINES.md](MACHINES.md).

**Afterwards.** Record the run in `jobs/<date>_<machine>_<job>/`, with
a copy of the exact file that ran and a `job.json`. That ties the
physical part to the revision.

## design.json

Optional for a design made by hand; `part_number` and `description` are
the fields worth adding. For a synced design the sync tool writes it. The
sync owns the keys shown here and preserves any others, so `part_number`,
`description`, and fields of your own survive every sync.

```json
{
  "schema": 2,
  "fusion_item_id": "urn:adsk.wipprod:dm.lineage:...",
  "name": "Headset Spacers",
  "kind": "design",
  "hub": "Kenny's Hub",
  "project": "Bike",
  "part_number": null,
  "description": null,
  "wip": {
    "version_id": "urn:adsk.wipprod:fs.file:vf....?version=14",
    "version_number": 14,
    "last_modified": "2026-09-30T21:14:03Z",
    "synced_at": "2026-10-01T02:00:11-0500",
    "files": ["Headset Spacers.f3d", "Headset Spacers.step"]
  }
}
```

## history.jsonl

One JSON object per line, appended, never rewritten. The design's status
record: what happened to it and when.

```
{"at": "2026-09-27T02:00:09-0500", "event": "synced", "version_number": 13, "files": ["Headset Spacers.f3d"]}
{"at": "2026-10-01T02:00:11-0500", "event": "synced", "version_number": 14, "files": ["Headset Spacers.f3d"]}
{"at": "2026-10-02T02:00:07-0500", "event": "moved", "from": "Hub/Bike/Spacers", "to": "Hub/Bike/Headset Spacers"}
```

The Fusion sync writes `synced`, `moved`, `deleted_in_cloud`, and
`restored_in_cloud`. A release adds `released`, and `obsoleted` for the
revision it replaces, each with a `revision` field. A job adds `ran`, with
a `job` field naming its folder.

## manifest.json for a release revision

```json
{
  "schema": 2,
  "part_number": "bm-0042",
  "description": "Spacer, headset, 10 mm",
  "revision": "rev-a",
  "status": "released",
  "released_at": "2026-09-27T05:20:00-05:00",
  "released_by": "engineer@example.com",
  "reason": "Initial release after first-article inspection 2026-09-25",
  "approval": {
    "reviewed_by": "reviewer@example.com",
    "reviewed_at": "2026-09-26T16:05:00-05:00",
    "approved_by": "owner@example.com",
    "approved_at": "2026-09-27T05:15:00-05:00",
    "review_record": "reviews/design-review.md"
  },
  "source": {
    "hub": "Kenny's Hub",
    "project": "Bike",
    "fusion_item_id": "urn:adsk.wipprod:dm.lineage:...",
    "fusion_version_id": "urn:adsk.wipprod:fs.file:vf....?version=14",
    "fusion_version_name": "REL A"
  },
  "process": {
    "primary": "fdm",
    "material": "PETG",
    "color": "black",
    "proven_on": ["bambu-p1s"],
    "post_processing": []
  },
  "files": {
    "cad/bm-0042.f3d":            { "tool": "fusion-cloud-export", "format": "f3d" },
    "cad/bm-0042.step":           { "tool": "fusion-cloud-export", "format": "step" },
    "drawings/bm-0042_rev-a.pdf": { "tool": "fusion-cloud-export", "from_drawing_version": "urn:...?version=6" },
    "mesh/bm-0042_rev-a.3mf":     { "tool": "fusion-cloud-export", "format": "3mf" },
    "build/fdm/bambu-p1s/bm-0042_rev-a.gcode": {
      "tool": "orca-slicer 2.3.0",
      "from_mesh": "mesh/bm-0042_rev-a.3mf",
      "profile": "build/fdm/bambu-p1s/profile.json",
      "estimated_minutes": 84,
      "filament_g": 31.2
    }
  },
  "supersedes": null,
  "superseded_by": null,
  "retention": { "policy": "iso9001-default", "keep_until": "product-eol+7y" }
}
```

`source` says where the revision's CAD came from. The example is a design
synced from Fusion; for a design made by hand, leave it out or name the
working file.

`approval.reviewed_by` and `approval.approved_by` are required; `check`
reports a revision without them. They may name the same person as
`released_by`. `review_record` is optional and, when given, must be a file
in the revision folder.

A shop may add keys of its own to `manifest.json`, `job.json`,
`nonconformance.json`, and `design.json`: a customer, a purchase order, a
material certificate. The tools read only the keys this document names
and leave the rest alone. Keep shop keys under one key of the shop's own,
such as `"acme": {...}`, so a later schema cannot collide with them.

Every file in `files` must be in the revision folder. Each model in
`process.proven_on` needs its program in `build/<process>/<model>/` or
`cam/<model>/`.

## Machine files in a revision

A revision holds the files the shop's own machines need, one folder per
machine model: the slicer's project file, the settings it used, and the
G-code or NC program. They are saved as the slicer or CAM system wrote
them; there is no neutral settings format, because slicers do not share
one. Material and color are stated in `process` so the record can be read
without opening a slicer.

What a revision releases for a printer is G-code: a `.gcode` file, or a
sliced 3MF with the G-code inside. For a Bambu printer the sliced 3MF that
Bambu Studio exports is enough on its own: it holds the model, the
settings, and the G-code. A project 3MF saved before slicing holds no
G-code and does not count. A separate `profile.json` beside it is
optional.

These files are locked with the revision. Changing the material, color,
infill, or any other setting is a new revision.

`process.proven_on` lists the machine models the revision held a program
for when it was released. Running the revision on another model does not
need a new revision: re-target the saved slicer project to that model, and
the job record keeps the exact program that ran. The first job on a
model the revision is not proven on is a first article. It needs an
inspection record before it can be accepted, and once it is accepted that
model counts as proven for the jobs that follow.

Slices made while getting a print right are not records. Until release
they are loose material in the design folder, with no rules. The files
that worked are copied into the revision when it is released.

Review records live in `reviews/` and are frozen with the revision, so the
review happens before the release, not after. For a revision that changes
a released design, the design review also covers the effect of the change
on parts already made or in stock.

## job.json for a job

One folder per job under `jobs/`, named `<date>_<machine>_<job>`. The
folder is created when the job runs, with `result` set to `pending`.
`job.json` is completed once at inspection and not changed after.

```json
{
  "schema": 2,
  "part_number": "bm-0042",
  "revision": "rev-a",
  "file": "bm-0042_rev-a.gcode",
  "machine": "p1s-01",
  "machine_model": "bambu-p1s",
  "material": { "type": "PETG", "lot": "LOT-2026-0911" },
  "operator": "operator@example.com",
  "started_at": "2026-09-27T08:02:00-05:00",
  "finished_at": "2026-09-27T09:26:00-05:00",
  "quantity": { "made": 4, "accepted": 4 },
  "result": "accepted",
  "inspection": {
    "inspected_by": "qc@example.com",
    "inspected_at": "2026-09-27T10:10:00-05:00",
    "method": "visual, calipers on the 10 mm height",
    "record": "inspection.pdf"
  },
  "accepted_by": "qc@example.com",
  "accepted_at": "2026-09-27T10:12:00-05:00"
}
```

`revision` and `machine_model` are required: a job says what ran and on
what. `machine` is the individual machine. `result` is `pending`,
`accepted`, or `rejected`.

## nonconformance.json for rejected parts

Written beside `job.json` whenever anything made was not accepted.

```json
{
  "schema": 2,
  "found_by": "qc@example.com",
  "found_at": "2026-09-27T10:10:00-05:00",
  "description": "Layer shift at 14 mm on 2 of 4 parts",
  "quantity_affected": 2,
  "disposition": "scrap",
  "concession": null,
  "actions": "Parts scrapped. Belt tension checked on p1s-01.",
  "decided_by": "owner@example.com",
  "decided_at": "2026-09-27T10:30:00-05:00"
}
```

`description`, `disposition`, and `decided_by` are required. `disposition`
is `scrap`, `rework`, `use-as-is`, or `return`. For `use-as-is`,
`concession` records who authorized accepting the parts as they are,
including the customer where the customer has to agree.

## What check and verify find

`mcad_tree.py check` hashes nothing and is quick. `verify` does the
same and then re-hashes every released file, which reads the whole store.
Both exit non-zero when there is a problem.

In a revision:

- no `manifest.json` or no `SHA256SUMS`;
- no reviewer or approver in the manifest, or a review record it cites
  that is not there;
- a file listed in the manifest that is not there;
- a file listed in `SHA256SUMS` that is not there, or a file in the folder
  that `SHA256SUMS` does not list (added after the revision was frozen);
- a revision of a design with nothing under `cad/`;
- a model in `proven_on` with no G-code or NC program in the revision;
  a 3MF counts only if it has G-code inside;
- a name that breaks the naming rule;
- with `verify`: a file whose contents no longer match its checksum.

In a job:

- no `job.json`, or one that does not name its revision and machine
  model, or names a revision that does not exist;
- an accepted job with no inspector or no acceptor, or an inspection
  record it cites that is not there;
- a first job on a model the revision is not proven on, accepted
  without an inspection record;
- rejected parts with no `nonconformance.json`, or one without a
  description, a disposition, and who decided.

In either: a `manifest.json` or `job.json` at a schema this version of
the tool does not know.

A job still awaiting inspection is a note, not a problem.

What it cannot find: an artifact your shop expects that neither the
manifest nor this schema requires. A revision with no drawing passes
unless the manifest lists one.

## Who writes where

| Area | Writer | Mutability | Reader |
|---|---|---|---|
| `<design>/wip/` | people; a sync tool for its own exports | working files; sync replaces only the files it wrote | people, backup, whoever releases |
| `<design>/design.json` | people; a sync tool (its keys) | updated | every tool |
| `<design>/history.jsonl` | a sync tool; whoever releases | append-only | auditors |
| `<design>/released/` and `<design>/jobs/`, the empty folders | people; a sync tool | made once | everyone |
| `<design>/released/<rev>/` | whoever releases, a person or a tool | frozen after SHA256SUMS | everyone, machines, auditors |
| `<design>/released/CURRENT` | whoever releases | replaced on each release | people, machines, scripts |
| `<design>/jobs/` | operator, inspector, shop software | append-only; `job.json` completed once at inspection | QC, traceability |
| `<design>/` anything else | people | free | people |
| `_index/` | mcad_tree index | regenerated | people, scripts |
| `_outbox/<machine>/` | whoever releases, copying from the revision | replaced on release | machines via a network share |

## MCAD_base compatible

A product that reads or writes a store can be described as "MCAD_base
compatible" when all four of these hold.

1. **What it writes passes `check`.** After the product has written to a
   store, `check` reports no problem in anything it wrote, and `verify`
   reports none in any revision it released.
2. **It writes only where its kind of tool may write**, as set out in
   "Who writes where" above:
   - a sync tool writes its own exports in `wip/`, its own keys in
     `design.json`, and events in `history.jsonl`;
   - a release tool writes `released/`, `CURRENT`, events in
     `history.jsonl`, and copies in `_outbox/`;
   - farm or shop-floor software writes `jobs/`;
   - a tool that only reads writes nothing inside a design folder.

   It leaves alone what it does not own: other files in `wip/`, keys it did
   not write in a JSON file, frozen revisions, and anything else in the
   design folder.
3. **It names the schema version it targets, and writes to no other.**
   The claim carries the number: "MCAD_base 2 compatible". The version is
   the `schema` value in the store's `.mcad-tree.json`. Before writing, the
   product reads that file and stops if it is missing or names another
   version.
4. **Everything it writes stays readable and verifiable with the open
   tools alone.** Its records are plain files in the formats this document
   defines. Nothing about a design exists only in the product's own
   database, cloud, or file format. Take the product away and the store
   still passes `check` and `verify`.

A product may keep settings or working state of its own outside the design
folders, as the Fusion sync does in `.fusion-sync/`. The store must be
complete without it.

Anyone can test the claim: point the product at a store, then run `check`
and `verify`.

## Protection

- Revision folders are set read-only after `SHA256SUMS` is written.
- Snapshots on the NAS daily, off-site copy weekly. A snapshot of the root
  is a coherent point in time for every design.
- With more than one person writing: a sync account and a release account,
  and NAS permissions that match the table above.

## Versions

Two numbers, kept apart:

- **The schema version** is the layout's. It is a whole number, stored in
  `.mcad-tree.json` at the root and in each record (`design.json`,
  `manifest.json`, `job.json`, `nonconformance.json`). This document
  describes schema 2.
- **The tool version** is the version of `mcad_tree.py` and
  `fusion_sync.py`, shown by `--version` and tagged in the repository as
  `v<version>`. It changes with every release of the tools. Many tool
  versions work on one schema.

When the schema version changes:

- It does **not** change for an addition that a valid store is still valid
  without: a new optional folder, file, or key. Nothing has to be
  upgraded, and tools that do not know the addition ignore it.
- It **does** change, by one, for anything that would make a valid store
  invalid: a new required file or key, a rename, a moved folder, or a
  changed meaning.

What happens when it changes:

- `mcad_tree.py upgrade <root>` converts a store from the version before.
  `--dry-run` shows the steps without writing. The marker is rewritten
  last, so a store is never marked as a version it has not reached.
- A frozen revision is never rewritten, by an upgrade or anything else. It
  keeps the `schema` its `manifest.json` was released under, and `check`
  judges it by that version's rules. `upgrade` hashes every file in every
  frozen revision before and after, and refuses to mark the store if one
  changed.
- A completed job record is not rewritten either, and is judged the same
  way.
- A record that states no `schema` is read as schema 2.
- A tool refuses a store at a schema it does not target. An older store
  needs `upgrade`; a newer store needs newer tools.
- Back the store up before an upgrade.

Change log:

- **Schema 2**, current. One folder per design, holding `wip/`,
  `released/`, and `jobs/`. Sign-offs, job and nonconformance records,
  `proven_on`, and designs made by hand were added while the schema was
  still being settled and no store held released revisions, so the number
  did not change. From tool version 0.1.0 the rules above apply.
- **Schema 1.** An early layout keyed on part number, with separate
  `mirror/`, `released/`, `nc/`, and `print/` trees at the root. Never
  used outside development. There is no upgrade from it.

## Mapping to the standards

- ISO 9001 7.5.2 identification: `design.json` and each revision's
  `manifest.json`.
- ISO 9001 7.5.2 review and approval: `manifest.approval` names the
  reviewer and the approver of every revision.
- ISO 9001 7.5.3 control and preservation: `wip/` plus NAS snapshots plus
  off-site for preservation; frozen revision folders and `SHA256SUMS` for
  protection from unintended change; `OBSOLETE` markers for control of
  superseded records.
- ISO 9001 8.3.5 design outputs and 8.5.2 traceability: `released/<rev>/`
  holds the outputs; `jobs/` ties a physical part to the revision and
  the exact file that made it. One folder holds the whole chain.
- ISO 9001 8.3.4 design controls and 8.3.6 design changes: `reviews/` in
  each revision holds the review, verification, and validation records;
  `CHANGELOG.md`, `reason`, and `manifest.approval` record what changed and
  who authorized it.
- ISO 9001 8.5.1 controlled production and 8.5.6 production changes: the
  slicer project, settings, and program for each proven machine model are
  frozen in the revision; changing them is a new revision, and a new
  machine model is admitted by a first-article job with an inspection
  record.
- ISO 9001 8.6 release of products: `job.json` records the inspection
  and who accepted the job.
- ISO 9001 8.7 nonconforming outputs: `nonconformance.json` records what
  failed, the disposition, any concession, and who decided.
- ISO 10007 configuration management: the design folder is the
  configuration item, `released/<rev>/` are its baselines, and
  `history.jsonl` is its status accounting.
- ISO 19650 container states, borrowed from construction: `wip/` is work
  in progress (the standard's own name for information still being
  authored), `released/` with `CURRENT` is published, revisions marked
  `OBSOLETE` are the archive.
- Retention: `manifest.retention` per revision.
