# Canonical shop tree

One root on the NAS that receives everything: the nightly mirror of the
Fusion cloud, immutable release packages, the NC share the machines mount,
and the shared libraries. Schema version 1.

Companion to `PROJECT_LAYOUT.md`, which describes a single part's working
folder. This file describes the shop-wide root that release packages land
in.

## Rules

1. **Part number is the key, not project or customer.** Projects change
   names; part numbers don't. Customer and project live in the manifest.
2. **Three kinds of area, three kinds of writer.**
   - `mirror/` is written only by the sync tool. Mutable, overwritten nightly.
   - `released/` is written only by the release tool. Append-only; a
     revision folder is never modified after its `SHA256SUMS` is written.
   - `nc/` and `print/` are derived from `released/` by the release tool.
     Machines and farm software read them. Humans don't edit them.
3. **A revision folder is immutable.** New revision, new folder. Obsolete
   revisions stay; they get an `OBSOLETE` marker file, not a delete.
4. **`CURRENT` is a file, not a symlink.** SMB clients and CNC controls
   don't follow symlinks reliably. `CURRENT` contains one line: the
   current revision string.
5. **Names are safe everywhere.** Lowercase ASCII, digits, `-` and `_`,
   no spaces. Total path length under 200 characters so Windows clients
   and CNC controls with 8.3 or 32-character limits don't choke.
   Machine-facing NC file names follow the control's own limits.
6. **Every derived file states its parameters in its path.** G-code and
   NC programs are only valid for one machine, material, and profile.
7. **Every folder that matters has a `manifest.json`.** Machine-readable
   lineage: which Fusion version id, which mesh, which profile, which tool.
8. **Integrity is explicit.** `SHA256SUMS` in every release revision folder,
   generated last, verified by the nightly job.

## Tree

```
<root>/                                     <- a share or folder on the NAS; back this whole thing up off-site
  .mcad-tree.json                           <- schema version marker, written by mcad_tree.py init
  README.md                                 <- points here
  SCHEMA.md                                 <- this document, versioned
  logs/
    fusion-sync.log
    release.log
    verify.log                              <- nightly SHA256SUMS check

  mirror/                                   <- NIGHTLY, MUTABLE. Written by fusion_sync only.
    <hub>/
      <project>/
        <folder>/.../<design>.f3d            <- native archive, latest cloud version
                     <design>.step
    .fusion-sync/
      manifest.json                         <- cloud version id behind each file
      _versions/...                         <- prior copies when a design changed

  released/                                 <- IMMUTABLE. Written by the release tool only.
    <part-number>/                          e.g. bm-0042/
      CURRENT                               <- text file: "rev-b"
      rev-a/
        manifest.json                       <- part, rev, fusion version id, who, when, why
        CHANGELOG.md                        <- what changed from the prior rev
        SHA256SUMS                          <- written last; folder frozen after this
        cad/
          bm-0042.f3d
          bm-0042.step
        drawings/
          bm-0042_rev-a.pdf
          bm-0042_rev-a.dxf
        mesh/
          bm-0042_rev-a.3mf                 <- generic 3MF, slicer-neutral
          bm-0042_rev-a.stl
        build/                              <- additive, per process/machine/profile
          fdm/bambu-p1s/petg_0.4_0.20mm/
            profile.json
            bm-0042_rev-a.3mf               <- project 3MF with gcode inside
            bm-0042_rev-a.gcode
            slice_info.xml
          mjf/hp-5200/pa12_default/
            bm-0042_rev-a.3mf
            build-notes.md
        cam/                                <- subtractive, per machine
          haas-vf2/
            o0042_op10_rev-a.nc
            o0042_op20_rev-a.nc
            setup-sheet_op10.pdf
            tools.json
        docs/
          orientation.md                    <- build orientation, supports, post-processing
          test-report.pdf
          inspection_first-article.pdf
          material-spec.pdf
      rev-b/
        ...
        OBSOLETE                            <- marker only, never deleted (absent while current)

  nc/                                       <- WHAT THE MACHINES MOUNT. Copies from released/, never edited.
    haas-vf2/
      bm-0042/
        o0042_op10_rev-a.nc
        o0042_op20_rev-a.nc
      _CHANGELOG.txt                        <- appended by the release tool
    mazak-qtn/
      ...
    _prove-out/                             <- unreleased programs; separate so nobody runs them by mistake
      haas-vf2/...

  print/                                    <- WHAT THE FARM SOFTWARE READS
    queue/                                  <- release tool drops sliced files here; farm consumes
    builds/                                 <- one folder per build actually run
      2026/2026-09-27_p1s-01_b0173/
        bm-0042_rev-a.gcode                 <- exact file that ran
        build.json                          <- printer, material lot, operator, result, photos
    archive/                                <- old builds, retained per policy

  library/                                  <- SHARED ASSETS. Mirror of the hub's Assets plus shop extras.
    posts/                                  <- post processors, same files as hub Assets/CAMPosts
    tools/                                  <- tool libraries
    machines/                               <- machine definitions
    print-profiles/
      bambu-p1s/petg_0.4_0.20mm.json
    templates/
    standards/
      naming.md
      release-procedure.md                  <- the two-page ISO procedure
      retention.md
```

## manifest.json for a release revision

```json
{
  "schema": 1,
  "part_number": "bm-0042",
  "description": "Bracket, motor mount",
  "revision": "rev-a",
  "status": "released",
  "released_at": "2026-09-27T05:20:00-05:00",
  "released_by": "engineer@example.com",
  "reason": "Initial release after first-article inspection 2026-09-25",
  "source": {
    "hub": "Kevin's Hub",
    "project": "Shop Projects",
    "fusion_item_id": "urn:adsk.wipprod:dm.lineage:...",
    "fusion_version_id": "urn:adsk.wipprod:fs.file:vf....?version=14",
    "fusion_version_name": "REL A"
  },
  "process": {
    "primary": "mjf",
    "material": "PA12",
    "orientation": "docs/orientation.md",
    "post_processing": ["bead blast", "black dye"]
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
| `mirror/` | fusion_sync, nightly | overwritten | humans (browse), disaster recovery |
| `released/<pn>/<rev>/` | release tool, on release | frozen after SHA256SUMS | everyone, auditors |
| `released/<pn>/CURRENT` | release tool | replaced on each release | release tool, scripts |
| `nc/<machine>/` | release tool copies from released | replace on release | CNC controls via SMB |
| `nc/_prove-out/` | CAM programmer | free | CNC controls, clearly separated |
| `print/queue/` | release tool or engineer | consumed by farm | farm software |
| `print/builds/` | farm software or operator | append-only | QC, traceability |
| `library/` | admin | controlled | Fusion seats, release tool |

## NAS permissions that enforce it

- `mirror/`: write for the sync service account only.
- `released/`: write for the release service account only; everyone else
  read. Revision folders additionally set read-only after `SHA256SUMS`.
- `nc/`: write for the release account; machines and operators read.
  `_prove-out/` writable by programmers.
- `print/builds/`: append for the farm account and operators.
- Snapshots on the NAS (ZFS, btrfs, or the vendor's) daily, off-site copy
  weekly. The tree is designed so a snapshot is a coherent point in time.

## Mapping to the standards

- ISO 9001 7.5.3 preservation: `mirror/` plus NAS snapshots plus off-site.
- ISO 9001 8.3 design outputs and 8.5.2 traceability: `released/` with
  manifest and SHA256SUMS; `print/builds/` and NC header blocks link
  shipped parts to a revision.
- AS9100 8.1.2 configuration: `manifest.json` `supersedes` chain plus
  `OBSOLETE` markers; nothing deleted.
- Retention: `manifest.retention` per revision; `verify.log` proves the
  archive is still intact.
