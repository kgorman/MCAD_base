# MCAD_base

Working name. Product data management (PDM) for mechanical CAD, kept in
plain folders on a NAS. The file system is the database: every record is a
plain file in the tree, with no server and no separate database, so the
store stays readable without these tools.

The layout is meant to survive an audit: a file store shaped like the
Fusion cloud, where every design has one folder holding its working files,
its frozen released revisions, and the record of what was built from them.

Standard library Python only. Python 3.9+.

## What's here

| File | What it does |
|---|---|
| `mcad_tree.py` | Marks a folder as a store, checks it against the schema, writes the part-number index. |
| `fusion_sync.py` | Mirrors Fusion cloud hubs into the store, one way, incrementally. Creates the hub, project, and design folders. See [docs/FUSION_SYNC.md](docs/FUSION_SYNC.md). |
| `docs/CANONICAL_TREE.md` | The layout, its rules, the file formats, and the ISO mapping. Copied into the store as `SCHEMA.md`. |
| `tests/` | Offline tests for both tools and for the two working together. |

## The store

```
<root>/
  <hub>/<project>/<folder>/.../<design>/
    design.json       Fusion item id, part number
    history.jsonl     ledger: synced, moved, released, built
    wip/              work in progress: the working folder, plus the latest cloud export for a synced design
    released/         rev-a/, rev-b/, CURRENT; release tool only, frozen
    builds/           what actually ran
    (anything else)   yours
  _index/parts.csv    generated: part number -> folder
  _outbox/<machine>/  flat copies for machines, only if needed
```

The tree follows Fusion, so a design is on disk where it is in the data
panel. Identity is the Fusion item id in `design.json`, not the path: a
rename or move in the cloud moves the folder, and a delete in the cloud
marks the folder and removes nothing.

Backup and release are different things and both live in the design's
folder. `wip/` changes whenever the design does. A revision under
`released/` never changes after it is signed off.

## Set up a store

```sh
./mcad_tree.py init /Volumes/MCAD/MCAD_base --dry-run   # show what would be created
./mcad_tree.py init /Volumes/MCAD/MCAD_base             # create it
```

`init` writes `README.md`, `SCHEMA.md`, and a schema version marker. It
creates no folders; the sync tool builds the tree from the cloud.

- It never overwrites, moves, or deletes. `--update-docs` rewrites the two
  docs when the store's copies are out of date.
- It refuses to run if the root folder does not exist, so an unmounted share
  does not get a store built on the local disk in its place.

## Without Fusion

Make a folder for a design anywhere below the store root, make `wip/`
inside it, and work there. No command is needed; `check`, `verify`, and
`index` pick the folder up.

## Fill it from Fusion

```sh
./fusion_sync.py init --client-id <APS_CLIENT_ID> --root /Volumes/MCAD/MCAD_base
./fusion_sync.py auth
./fusion_sync.py sync --dry-run
./fusion_sync.py sync --formats native,step
```

## Check and index

```sh
./mcad_tree.py check /Volumes/MCAD/MCAD_base   # find gaps; non-zero exit on problems
./mcad_tree.py verify /Volumes/MCAD/MCAD_base  # check, plus re-hash every released file
./mcad_tree.py index /Volumes/MCAD/MCAD_base   # writes _index/parts.csv
./mcad_tree.py add-machine /Volumes/MCAD/MCAD_base haas-vf2
```

`check` finds gaps without hashing anything, so it is quick:

- two folders claiming the same Fusion item, or a `CURRENT` that names no
  revision;
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
  revision folder come from Fusion and are not policed.

`verify` does all of that and re-hashes every released file against its
`SHA256SUMS`, which reads the whole store. The full list is in
[docs/CANONICAL_TREE.md](docs/CANONICAL_TREE.md).

## Tests

```sh
python3 tests/test_mcad_tree.py
python3 tests/test_fusion_sync.py
python3 tests/test_store.py
```

## Licence

Apache-2.0, for everything in this repository: the layout, `mcad_tree.py`,
and `fusion_sync.py`. See [LICENSE](LICENSE).

## Not built yet

- Release: build `released/<rev>/` from a named Fusion version, collect the
  reviewer, approver, and review records, write the manifest and
  `SHA256SUMS`, set `CURRENT`, mark the prior revision `OBSOLETE`, append
  to `history.jsonl`, copy to `_outbox/` if a machine needs it.
- Build logging: nothing writes `builds/` yet; `build.json` and
  `nonconformance.json` are filled in by hand.
- Running `verify` on a schedule.
- The Fusion add-in that provides the Release command.
- A run of `fusion_sync.py` against a live Autodesk account.
