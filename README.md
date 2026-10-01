# MCAD_base

Working name. Tools for keeping mechanical CAD on a NAS in a layout that
survives SMB shares, CNC controls, and an audit: a nightly backup of the
Fusion cloud on one side, immutable release records on the other.

Standard library Python only. Python 3.9+.

## What's here

| File | What it does |
|---|---|
| `mcad_tree.py` | Scaffolds the canonical shop tree on a share and checks that it still matches the schema. |
| `fusion_sync.py` | Mirrors Fusion cloud hubs to disk, one way, incrementally. Fills `mirror/`. See [docs/FUSION_SYNC.md](docs/FUSION_SYNC.md). |
| `docs/CANONICAL_TREE.md` | The tree, its rules, the release manifest schema. Copied onto the share as `SCHEMA.md`. |
| `docs/PROJECT_LAYOUT.md` | Layout of a single part's working folder. |
| `tests/` | Offline tests for both tools. |

## Scaffold a tree

```sh
./mcad_tree.py init /Volumes/MCAD/MCAD_base --dry-run   # show what would be created
./mcad_tree.py init /Volumes/MCAD/MCAD_base             # create it
./mcad_tree.py check /Volumes/MCAD/MCAD_base            # does the share still match?
./mcad_tree.py add-machine /Volumes/MCAD/MCAD_base haas-vf2
./mcad_tree.py add-printer /Volumes/MCAD/MCAD_base bambu-p1s
```

```
<root>/
  .mcad-tree.json     schema version marker
  README.md
  SCHEMA.md           copy of docs/CANONICAL_TREE.md
  mirror/             nightly backup of the Fusion cloud; sync tool only
  released/           <part-number>/<rev>/, frozen after sign-off; release tool only
  nc/                 what the machines mount, copied from released/
    _prove-out/       unreleased programs, kept apart
  print/
    queue/  builds/  archive/
  library/
    posts/  tools/  machines/  print-profiles/  templates/  standards/
  logs/
```

Unlike a project scaffolder that runs once, `init` is meant to be re-run for
the life of the share:

- It only adds what is missing. It never overwrites, moves, or deletes.
  Anything already on the share is left where it is, and `check` lists it as
  unmanaged.
- It refuses to run if the root folder does not exist, so an unmounted share
  does not get a tree built on the local disk in its place.
- The tree is defined as data at the top of `mcad_tree.py`. Changing the
  layout is an edit there plus a bump of `SCHEMA_VERSION`; the marker file on
  the share records which version it was built with.
- `init --update-docs` rewrites `SCHEMA.md` and the per-folder READMEs when
  the copies on the share are out of date.

`check` exits non-zero on missing folders, a schema mismatch, or names that
break the naming rule (lowercase ASCII, digits, `-`, `_`, `.`, paths under
200 characters). `mirror/` is exempt from the naming rule because it carries
the cloud's own names.

## Fill the mirror

```sh
./fusion_sync.py init --client-id <APS_CLIENT_ID> --root /Volumes/MCAD/MCAD_base/mirror
./fusion_sync.py auth
./fusion_sync.py sync --formats native,step
```

## Tests

```sh
python3 tests/test_mcad_tree.py
python3 tests/test_fusion_sync.py
```

## Not built yet

- Release mode: build `released/<part>/<rev>/` from a named Fusion version,
  write the manifest and `SHA256SUMS`, copy NC programs to `nc/`, drop sliced
  files in `print/queue/`.
- Nightly verify of `SHA256SUMS` into `logs/verify.log`.
- Sync: treat cloud renames and moves as local moves, and explicit handling
  of cloud deletes.
- The Fusion add-in that provides the Release command.
