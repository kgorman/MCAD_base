# MCAD_base store layout

One root on the NAS, shaped like the Fusion cloud: hub, project, folder,
then one folder per design. Everything about a design lands in that
design's folder. Schema version 2.

The project, and inside it the design, is the container. State lives inside
the container: work in progress, released revisions, and build records are
subfolders of the design, not separate trees.

## Rules

1. **The tree follows Fusion.** Hub, project, and folder names are the
   cloud's names. A design is found on disk where it is found in Fusion.
2. **One folder per design.** The folder is the configuration item. Nothing
   about a design lives outside its folder.
3. **Identity is the Fusion item id, not the path.** It is recorded in
   `design.json`. A rename or move in the cloud moves the folder; the id
   does not change. A part number is a field in `design.json`, added when
   one exists.
4. **Three reserved names, three writers.**
   - `wip/` is written only by the sync tool. Mutable, overwritten whenever
     the cloud changes.
   - `released/` is written only by the release tool. Append-only; a
     revision folder is never modified after its `SHA256SUMS` is written.
   - `builds/` holds records of what actually ran. Append-only.

   Anything else in the design folder is yours: photos, loose STLs, notes.
5. **Sync never touches what it does not own.** Inside a design folder it
   writes `wip/`, `design.json`, `history.jsonl`, and the
   `DELETED_IN_CLOUD` marker. Nothing else, ever.
6. **A revision folder is immutable.** New revision, new folder. Obsolete
   revisions stay; they get an `OBSOLETE` marker file, not a delete.
7. **Nothing is deleted because the cloud deleted it.** The folder stays
   and gets a `DELETED_IN_CLOUD` marker.
8. **`CURRENT` is a file, not a symlink.** SMB clients and CNC controls
   don't follow symlinks reliably. `CURRENT` contains one line: the
   current revision string.
9. **Safe names inside `released/`.** Lowercase ASCII, digits, `-`, `_`,
   `.`, no spaces, total path under 200 characters. Names above the
   revision folder come from Fusion and are not policed.
10. **Every derived file states its parameters in its path.** G-code and
    NC programs are only valid for one machine, material, and profile.
11. **Integrity is explicit.** `SHA256SUMS` in every revision folder,
    generated last.
12. **Machines do not browse this tree.** When a machine needs files, the
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

          wip/                              <- WORK IN PROGRESS. Sync tool only. Overwritten.
            <design>.f3d                    <- native archive, latest cloud version
            <design>.step
            _versions/                      <- prior copies when the cloud version changed
              <design>.v13.f3d

          released/                         <- RECORDS. Release tool only. Frozen.
            CURRENT                         <- text file: "rev-b"
            rev-a/
              manifest.json                 <- part, rev, fusion version id, who, when, why
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
              build/                        <- additive, per process/machine/profile
                fdm/bambu-p1s/petg_0.4_0.20mm/
                  profile.json
                  bm-0042_rev-a.3mf         <- project 3MF with gcode inside
                  bm-0042_rev-a.gcode
              cam/                          <- subtractive, per machine
                haas-vf2/
                  o0042_op10_rev-a.nc
                  setup-sheet_op10.pdf
              docs/
                orientation.md              <- build orientation, supports, post-processing
                inspection_first-article.pdf
            rev-b/
              ...

          builds/                           <- WHAT ACTUALLY RAN. Append-only.
            2026-09-27_p1s-01_b0173/
              bm-0042_rev-a.gcode           <- exact file that ran
              build.json                    <- printer, material lot, operator, result

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
  "hub": "Kevin's Hub",
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
  "source": {
    "hub": "Kevin's Hub",
    "project": "Bike",
    "fusion_item_id": "urn:adsk.wipprod:dm.lineage:...",
    "fusion_version_id": "urn:adsk.wipprod:fs.file:vf....?version=14",
    "fusion_version_name": "REL A"
  },
  "process": {
    "primary": "fdm",
    "material": "PETG",
    "orientation": "docs/orientation.md",
    "post_processing": []
  },
  "files": {
    "cad/bm-0042.f3d":            { "tool": "fusion-cloud-export", "format": "f3d" },
    "cad/bm-0042.step":           { "tool": "fusion-cloud-export", "format": "step" },
    "drawings/bm-0042_rev-a.pdf": { "tool": "fusion-cloud-export", "from_drawing_version": "urn:...?version=6" },
    "mesh/bm-0042_rev-a.3mf":     { "tool": "fusion-cloud-export", "format": "3mf" },
    "build/fdm/bambu-p1s/petg_0.4_0.20mm/bm-0042_rev-a.gcode": {
      "tool": "orca-slicer 2.3.0",
      "from_mesh": "mesh/bm-0042_rev-a.3mf",
      "profile": "build/fdm/bambu-p1s/petg_0.4_0.20mm/profile.json",
      "estimated_minutes": 84,
      "filament_g": 31.2
    }
  },
  "supersedes": null,
  "superseded_by": null,
  "retention": { "policy": "iso9001-default", "keep_until": "product-eol+7y" }
}
```

## Who writes where

| Area | Writer | Mutability | Reader |
|---|---|---|---|
| `<design>/wip/` | fusion_sync | overwritten | humans (browse), disaster recovery, release tool |
| `<design>/design.json` | fusion_sync (its keys); people (the rest) | updated | every tool |
| `<design>/history.jsonl` | fusion_sync, release tool | append-only | auditors |
| `<design>/released/<rev>/` | release tool | frozen after SHA256SUMS | everyone, auditors |
| `<design>/released/CURRENT` | release tool | replaced on each release | release tool, scripts |
| `<design>/builds/` | farm software or operator | append-only | QC, traceability |
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
- ISO 9001 7.5.3 control and preservation: `wip/` plus NAS snapshots plus
  off-site for preservation; frozen revision folders and `SHA256SUMS` for
  protection from unintended change; `OBSOLETE` markers for control of
  superseded records.
- ISO 9001 8.3.5 design outputs and 8.5.2 traceability: `released/<rev>/`
  holds the outputs; `builds/` ties a physical part to the revision and
  the exact file that made it. One folder holds the whole chain.
- ISO 10007 configuration management: the design folder is the
  configuration item, `released/<rev>/` are its baselines, and
  `history.jsonl` is its status accounting.
- ISO 19650 container states, borrowed from construction: `wip/` is work
  in progress, `released/` with `CURRENT` is published, revisions marked
  `OBSOLETE` are the archive.
- Retention: `manifest.retention` per revision.
