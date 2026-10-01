# CAD project folder layout

A folder convention for keeping native CAD, neutral exports, meshes, and
machine-specific output together, with the lineage between them recorded.

No industry-wide standard exists for this in mechanical work. The layout
below borrows from four partial conventions:

- **PDM/PLM vaults** (Vault, Windchill, Teamcenter): every file is an item
  with a revision and links to its source; folders are just a view.
- **ISO 19650** (construction): work-in-progress / shared / published /
  archive containers, strict file naming.
- **Open-source hardware repos**: the Open Know-How manifest and the common
  `cad/`, `stl/`, `docs/`, `bom/` split of source from exports.
- **Shop job folders**: CAD, drawings, CAM, NC, setup sheets, inspection,
  mirroring the paper traveler.

## Rules

1. **Separate source from derived.** Native CAD is the source. STEP, STL,
   3MF, G-code, and PDF are regenerable and never hand-edited.
2. **Derived files carry their parameters in the path or name.** G-code is
   only valid for one printer, material, nozzle, and profile.
   `bracket.gcode` is useless; `bracket-mount_v7_P1S_PETG_0.4_0.20mm.gcode`
   is not.
3. **Version is explicit.** Every derived file ties to the source version it
   came from, by number in the name and by entry in the manifest.
4. **A manifest, not just folders.** One machine-readable file records which
   STEP came from which f3d version, which G-code from which 3MF and profile.
5. **Released vs in-progress.** What went to the printer or the shop is a
   published snapshot, never the working file.

## Layout

```
bracket-mount/
  README.md
  manifest.json                    <- part number, current version, lineage of every derived file
  bom.csv
  cad/                             <- SOURCE
    bracket-mount.f3d              <- mirrored from the Fusion cloud by fusion_sync
    bracket-mount.step             <- exact neutral export of the same version
  drawings/
    bracket-mount_v7.pdf
    bracket-mount_v7.dxf
  mesh/                            <- DERIVED, slicer-neutral
    bracket-mount_v7.3mf
    bracket-mount_v7.stl
  print/                           <- DERIVED, machine-specific
    bambu-p1s/
      petg_0.4_0.20mm/
        profile.json               <- the exact slicer settings used
        bracket-mount_v7.3mf       <- project 3MF with gcode inside
        bracket-mount_v7.gcode
        slice_info.xml             <- time and filament estimates
  cam/                             <- if machined
    setups/
    nc/
    setup-sheets/
  docs/
  images/
  released/                        <- snapshots sent to the printer or the shop, never edited
    v7_2026-09-26/
  .versions/                       <- prior source versions, kept by fusion_sync
```

## manifest.json sketch

```json
{
  "part": "bracket-mount",
  "part_number": "BM-0042",
  "current_version": 7,
  "source": {
    "fusion_item_id": "urn:adsk.wipprod:dm.lineage:...",
    "fusion_version_id": "urn:adsk.wipprod:fs.file:vf....?version=7",
    "hub": "Kevin's Hub",
    "project": "Shop Projects",
    "file": "cad/bracket-mount.f3d",
    "synced_at": "2026-09-26T21:40:00-05:00"
  },
  "derived": [
    { "file": "cad/bracket-mount.step", "from_version": 7, "tool": "fusion-cloud-export" },
    { "file": "mesh/bracket-mount_v7.3mf", "from_version": 7, "tool": "fusion-cloud-export" },
    {
      "file": "print/bambu-p1s/petg_0.4_0.20mm/bracket-mount_v7.gcode",
      "from_version": 7,
      "from_mesh": "mesh/bracket-mount_v7.3mf",
      "tool": "orca-slicer 2.3.0",
      "profile": "print/bambu-p1s/petg_0.4_0.20mm/profile.json",
      "profile_sha256": "...",
      "printer": "Bambu P1S",
      "material": "PETG",
      "nozzle_mm": 0.4,
      "layer_mm": 0.20,
      "estimated_minutes": 84,
      "filament_g": 31.2
    }
  ],
  "released": [
    { "snapshot": "released/v7_2026-09-26/", "version": 7, "note": "first article" }
  ]
}
```

## How the tools in this folder map onto it

- `fusion_sync.py` produces `cad/` and `.versions/` and can write the
  `source` block of the manifest.
- A slicer bridge (Fusion add-in calling the OrcaSlicer or Bambu Studio CLI)
  would fill `print/<printer>/<profile>/` and append `derived` entries.
- A printability checker reads `mesh/`.
- Git handles the tree as long as the f3d stays under a few hundred MB;
  GitHub renders STL and 3MF in the browser.
