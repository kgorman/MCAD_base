# MCAD_base store layout

One root on the NAS and one folder per design. Everything about a design
lands in that design's folder. For designs synced from Fusion the folders
above are shaped like the Fusion cloud: hub, project, folder. Schema
version 2.

The layout does not set how a shop works. It says where things end up, and
`check` reports where a store differs from it.

The project, and inside it the design, is the container. State lives inside
the container: work in progress, released revisions, and build records are
subfolders of the design, not separate trees.

## Rules

1. **The tree follows Fusion, for designs that come from Fusion.** Hub,
   project, and folder names are the cloud's names. A design is found on
   disk where it is found in Fusion. A shop that does not use Fusion
   arranges the folders above a design however it likes.
2. **One folder per design.** The folder is the configuration item. Nothing
   about a design lives outside its folder.
3. **Identity is the Fusion item id, not the path.** It is recorded in
   `design.json`. A rename or move in the cloud moves the folder; the id
   does not change. A part number is a field in `design.json`, added when
   one exists. A design made by hand has no Fusion id and is known by its
   path.
4. **Three reserved names, three writers.**
   - `wip/` is work in progress, and it is the working folder for every
     design, whatever CAD system made it. For a design synced from Fusion
     the sync tool also keeps the latest cloud export here. It replaces
     only the files it wrote and leaves everything else in `wip/` alone.
   - `released/` is written only by the release tool. Append-only; a
     revision folder is never modified after its `SHA256SUMS` is written.
   - `builds/` holds records of what actually ran. Append-only.

   Anything else in the design folder is yours: photos, loose STLs, notes.
5. **Every sign-off names a person.** A revision's manifest names who
   reviewed it and who approved it. A build names who inspected it and who
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
    revision folder come from Fusion and are not policed.
11. **Machine files are kept per machine model.** G-code and NC programs
    are valid for one machine model only, so each sits in a folder named
    for its model, with the slicer project and settings that made it.
12. **Integrity is explicit.** `SHA256SUMS` in every revision folder,
    generated last.
13. **Machines do not browse this tree.** When a machine needs files, the
    release tool copies them into a flat `_outbox/<machine>/`. The outbox
    is a view; the revision folder is the home.

## Tree

```
<root>/                                     <- a folder on the NAS; back this whole thing up off-site
  .mcad-tree.json                           <- schema version marker, written by mcad_tree.py init
  README.md
  SCHEMA.md                                 <- this document
  .fusion-sync/                             <- sync tool state: manifest.json, logs

  <hub>/                                    <- as named in Fusion
    <project>/                              <- Fusion project, e.g. Bike/
      <folder>/...                          <- Fusion folders, as-is
        <design>/                           <- ONE FOLDER PER FUSION DESIGN, e.g. Headset Spacers/
          design.json                       <- identity: Fusion item id, part number, description
          history.jsonl                     <- ledger: synced, moved, released, built
          DELETED_IN_CLOUD                  <- marker, only if the design is gone from the cloud

          wip/                              <- WORK IN PROGRESS. The working folder for every design.
            <design>.f3d                    <- synced designs: native archive, latest cloud version; sync replaces it
            <design>.step
            _versions/                      <- prior copies when the cloud version changed
              <design>.v13.f3d

          released/                         <- RECORDS. Release tool only. Frozen.
            CURRENT                         <- text file: "rev-b"
            rev-a/
              manifest.json                 <- part, rev, fusion version id, who, when, why, reviewer, approver
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

          builds/                           <- WHAT ACTUALLY RAN. Append-only.
            2026-09-27_p1s-01_b0173/
              bm-0042_rev-a.gcode           <- exact file that ran
              build.json                    <- printer, material lot, operator, inspection, who accepted it
              inspection.pdf                <- the inspection record, if there is a separate one
              nonconformance.json           <- only if parts were rejected: what failed, disposition, who decided

          (anything else)                   <- yours: photos, loose STLs, notes

  _index/
    parts.csv                               <- generated by mcad_tree.py index: part number -> folder
  _outbox/                                  <- only if a machine needs it; created by add-machine
    haas-vf2/                               <- flat, safe names; copies from released/, never edited
```

Uploaded files that are not Fusion designs (a vendor PDF, an imported STL)
are items in the cloud too, and get the same treatment: a folder named for
the file, with the file in `wip/`.

A Fusion project normally has a single root folder. It is flattened away so
the disk path reads `<hub>/<project>/<design>/`, the same as the Fusion data
panel. A project with several root folders keeps their names.

## Starting a design by hand

No tool is needed. Make a folder for the design anywhere below the store
root, make `wip/` inside it, and work there with whatever CAD system the
shop uses. The folder is a design as soon as it holds `wip/`, `released/`,
or `builds/`, and `check`, `verify`, and `index` treat it like any other.
`design.json` is optional for such a design; add one to give it a part
number and description.

## design.json

Written by the sync tool. It owns the keys shown here and preserves any
others, so `part_number`, `description`, and fields of your own survive
every sync.

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

Events written today: `synced`, `moved`, `deleted_in_cloud`,
`restored_in_cloud`. The release tool will add `released` and `obsoleted`.

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

`approval.reviewed_by` and `approval.approved_by` are required; `check`
reports a revision without them. They may name the same person as
`released_by`. `review_record` is optional and, when given, must be a file
in the revision folder.

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
the build record keeps the exact program that ran. The first build on a
model the revision is not proven on is a first article. It needs an
inspection record before it can be accepted, and once it is accepted that
model counts as proven for the builds that follow.

Slices made while getting a print right are not records. Until release
they are loose material in the design folder, with no rules. The files
that worked are copied into the revision when it is released.

Review records live in `reviews/` and are frozen with the revision, so the
review happens before the release, not after. For a revision that changes
a released design, the design review also covers the effect of the change
on parts already built or in stock.

## build.json for a build

One folder per build under `builds/`, named `<date>_<machine>_<job>`. The
folder is created when the job runs, with `result` set to `pending`.
`build.json` is completed once at inspection and not changed after.

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
  "quantity": { "built": 4, "accepted": 4 },
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

`revision` and `machine_model` are required: a build says what ran and on
what. `machine` is the individual machine. `result` is `pending`,
`accepted`, or `rejected`.

## nonconformance.json for rejected parts

Written beside `build.json` whenever anything built was not accepted.

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
`concession` records who authorised accepting the parts as they are,
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

In a build:

- no `build.json`, or one that does not name its revision and machine
  model, or names a revision that does not exist;
- an accepted build with no inspector or no acceptor, or an inspection
  record it cites that is not there;
- a first build on a model the revision is not proven on, accepted
  without an inspection record;
- rejected parts with no `nonconformance.json`, or one without a
  description, a disposition, and who decided.

A build still awaiting inspection is a note, not a problem.

What it cannot find: an artifact your shop expects that neither the
manifest nor this schema requires. A revision with no drawing passes
unless the manifest lists one.

## Who writes where

| Area | Writer | Mutability | Reader |
|---|---|---|---|
| `<design>/wip/` | people; fusion_sync for its own exports | working files; sync replaces only the files it wrote | humans, disaster recovery, release tool |
| `<design>/design.json` | fusion_sync (its keys); people (the rest) | updated | every tool |
| `<design>/history.jsonl` | fusion_sync, release tool | append-only | auditors |
| `<design>/released/<rev>/` | release tool | frozen after SHA256SUMS | everyone, auditors |
| `<design>/released/CURRENT` | release tool | replaced on each release | release tool, scripts |
| `<design>/builds/` | farm software, operator, inspector | append-only; `build.json` completed once at inspection | QC, traceability |
| `<design>/` anything else | people | free | people |
| `_index/` | mcad_tree index | regenerated | people, scripts |
| `_outbox/<machine>/` | release tool copies from released | replaced on release | machines via SMB |

## Protection

- Revision folders are set read-only after `SHA256SUMS` is written.
- Snapshots on the NAS daily, off-site copy weekly. A snapshot of the root
  is a coherent point in time for every design.
- With more than one person writing: a sync account and a release account,
  and NAS permissions that match the table above.

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
  holds the outputs; `builds/` ties a physical part to the revision and
  the exact file that made it. One folder holds the whole chain.
- ISO 9001 8.3.4 design controls and 8.3.6 design changes: `reviews/` in
  each revision holds the review, verification, and validation records;
  `CHANGELOG.md`, `reason`, and `manifest.approval` record what changed and
  who authorised it.
- ISO 9001 8.5.1 controlled production and 8.5.6 production changes: the
  slicer project, settings, and program for each proven machine model are
  frozen in the revision; changing them is a new revision, and a new
  machine model is admitted by a first-article build with an inspection
  record.
- ISO 9001 8.6 release of products: `build.json` records the inspection
  and who accepted the build.
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
