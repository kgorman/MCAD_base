# MCAD_base for AI coding agents

Read this before writing code that reads or writes an MCAD_base store: a
sync tool, a release tool, print farm or shop-floor software, a CAD
plugin, or a viewer. It tells you what "MCAD_base 2 compatible" requires
and how to prove your code meets it.

Three sources, in order of authority:

1. `mcad_tree.py check` and `verify`. They are the test. If they report a
   problem in what your code wrote, your code is wrong.
2. [CANONICAL_TREE.md](CANONICAL_TREE.md). The full schema, with every
   file format and an example of each.
3. This file. A summary for getting it right the first time. If it
   disagrees with the two above, they win.

## The model

- A **store** is a folder that holds `.mcad-tree.json` with
  `{"schema": 2}`. It lives on a NAS, a shared drive, or a local disk.
- A **design** is one folder somewhere below the store root. Everything
  about the design is inside that folder. Design folders do not nest.
- A folder is a design when it holds `design.json`, or when it holds
  `wip/`, `released/`, or `jobs/`. The folders above a design can have
  any names and any depth.
- **The file system is the database.** Every record is a plain file in the
  design's folder. There is no server, no SQLite file, no index that is a
  source of truth. `_index/parts.csv` is generated and can be deleted.
- **Users are the operating system's users.** There are no accounts or
  roles in MCAD_base. Do not add any.
- **Tools report; they do not block.** People work in `wip/` with whatever
  CAD system they use, and make design folders and releases by hand. Do
  not require a command, a registration step, or a workflow before someone
  can work. A release your tool makes must be the same files a person
  would have made.
- **Machines run released files.** A printer's G-code is in
  `released/<rev>/build/<process>/<model>/`, a CNC machine's programs in
  `released/<rev>/cam/<model>/`, and `released/CURRENT` names the revision
  to run. Never send a machine a file from `wip/` or from a revision
  marked `OBSOLETE`.

```
<root>/
  .mcad-tree.json
  <any folders>/<design>/
    design.json        optional: part number, description, identity
    history.jsonl      append-only ledger
    wip/               working files
    released/
      CURRENT          one line: the current revision, e.g. rev-b
      rev-a/           frozen once SHA256SUMS is written
      rev-b/
    jobs/
      <date>_<machine>_<job>/
    (anything else)    belongs to people
  _index/parts.csv     generated
  _outbox/<machine>/   flat copies for machines
```

## Before your code writes anything

1. Read `<root>/.mcad-tree.json`. If it is missing, stop: the folder is
   not a store, or the share is not mounted. If `schema` is not the
   version you target, stop.
2. Never create the store root or the marker yourself. `mcad_tree.py init`
   does that, and it refuses a root that does not exist so that a store is
   never built on the local disk by mistake.
3. Know which kind of tool you are. It decides where you may write.

| Kind of tool | May write |
|---|---|
| Sync tool (fills the tree from a CAD system or cloud) | its own exports in `wip/`; its own keys in `design.json`; events in `history.jsonl`; the empty `released/` and `jobs/` folders |
| Release tool | `released/<rev>/`; `released/CURRENT`; the `OBSOLETE` marker in a superseded revision; events in `history.jsonl`; copies in `_outbox/<machine>/` |
| Print farm or shop-floor software | folders under `jobs/` |
| Read-only tool (viewer, report, search) | nothing inside a design folder |

## Rules you must not break

- **Never change a frozen revision.** Once `released/<rev>/SHA256SUMS`
  exists, do not add, edit, rename, or delete anything in that folder. A
  change is a new revision folder. The one exception is the `OBSOLETE`
  marker file.
- **Never delete records.** Not a design, a revision, or a job. Mark
  them instead: `OBSOLETE` in a superseded revision, `DELETED_IN_CLOUD` in
  a design whose source is gone.
- **Leave alone what you did not write.** In `wip/`, replace only files
  your tool wrote, and keep a record of which those are. When you rewrite
  a JSON file, read it first and keep every key you do not own.
- **Append to `history.jsonl`; never rewrite it.** One JSON object per
  line, with `at` (ISO 8601 with offset) and `event`.
- **Keep no record about a design outside its folder.** If your product
  has a database, the store must still be complete without it. Working
  state of your own goes in a dot-folder at the store root
  (`.your-tool/`) or outside the store.
- **No symlinks.** `CURRENT` is a text file.
- **Do not change the layout to suit your product.** The product adapts to
  the layout. If the layout cannot hold something you need, open an issue
  on the MCAD_base repository; do not invent folders or rename
  `wip/`, `released/`, or `jobs/`.
- **Cover printed and machined parts.** Printer files go in
  `build/<process>/<model>/`, machine tool programs in `cam/<model>/`.

## What `check` enforces

Write code that satisfies every line here.

**A revision, `released/<rev>/`**

- The folder name matches `^rev-[a-z0-9]+$`.
- It holds `manifest.json` and `SHA256SUMS`.
- `manifest.approval.reviewed_by` and `manifest.approval.approved_by` are
  set. Each names a person.
- `manifest.approval.review_record`, if given, is a file in the revision.
- Every key in `manifest.files` is a file in the revision, as a path
  relative to the revision with forward slashes.
- A design's revision has at least one file under `cad/`.
- Every model in `manifest.process.proven_on` has a program in the
  revision: for a printer, a `.gcode`, `.bgcode`, or `.gco` file, or a
  `.3mf` that contains one, in `build/<process>/<model>/`; for a machine
  tool, any file other than `profile.json` in `cam/<model>/`. A 3MF saved
  before slicing holds no G-code and does not count.
- Every file and folder name inside the revision matches
  `^[a-z0-9][a-z0-9._-]*$`, except `CHANGELOG.md`, `SHA256SUMS`, and
  `OBSOLETE`. No spaces, no capitals.
- Every path, measured from the store root, is under 200 characters.
- `SHA256SUMS` is in `sha256sum` format: the hex digest, two spaces, then
  the path relative to the revision with forward slashes. It lists every
  file in the revision except itself and `OBSOLETE`. Write it last.
- When any revision exists, `released/CURRENT` exists and names one of
  them.

**A job, `jobs/<date>_<machine>_<job>/`**

- Start the folder name with the date (`2026-09-27_...`). Jobs are read
  in name order, and that order decides which job was first on a
  machine model.
- It holds `job.json` with `revision` (an existing revision folder) and
  `machine_model`.
- `result` is `pending`, `accepted`, or `rejected`.
- An accepted job has `inspection.inspected_by` and `accepted_by`.
  `inspection.record`, if given, is a file in the job folder.
- The first accepted job on a model that is not in the revision's
  `proven_on` must have `inspection.record`.
- A rejected job, or one where `quantity.accepted` is less than
  `quantity.made`, has `nonconformance.json` with `description`,
  `disposition` (`scrap`, `rework`, `use-as-is`, or `return`), and
  `decided_by`.
- Copy the exact file that ran into the job folder and name it in
  `job.json` as `file`.

**A design**

- `design.json` is optional. When your tool writes one, set `schema` and
  your own keys, and keep the rest.
- `fusion_item_id` is only for designs synced from Fusion. Two folders
  must never carry the same one.

## Versions

- The store's schema version is `schema` in `.mcad-tree.json`. Write
  `"schema": <that number>` into every `design.json`, `manifest.json`,
  `job.json`, and `nonconformance.json` you create.
- A store can hold records at an older schema: frozen revisions and
  completed jobs are never rewritten when a store is upgraded. Read each
  record by the `schema` it states. A record that states none is schema 2.
- Do not rewrite an old record to bring it up to date. Do not write an
  upgrade of your own; `mcad_tree.py upgrade` is the only thing that
  changes a store's version.
- If a record states a schema your code does not know, do not guess.
  Report it and leave the record alone.
- An optional folder, file, or key does not change the schema version. If
  your product needs something the layout lacks, propose it as an optional
  addition in an issue on the MCAD_base repository.

## Order of writes for a release

1. Create `released/<rev>/` and put every file in it.
2. Write `manifest.json`.
3. Write `SHA256SUMS` last. The revision is now frozen.
4. Replace `released/CURRENT` with the new revision name.
5. Add an `OBSOLETE` file to the prior revision.
6. Append a `released` event to `history.jsonl`.

If the process stops before step 3, the folder has no `SHA256SUMS` and
`check` reports it, which is the correct result.

## Prove it

Run the checker on a store your code has written to. Exit code 0 means no
problems; 1 means at least one.

```sh
./mcad_tree.py check <root>    # fast, hashes nothing
./mcad_tree.py verify <root>   # check, plus re-hashes every released file
```

In your product's tests, build a store in a temporary folder, run your
writer against it, and assert the checker finds nothing:

```python
import mcad_tree

mcad_tree.init_tree(root, out=lambda line: None)
your_tool.write_something(root)
problems, notes = mcad_tree.check_tree(root, hashes=True)
assert problems == [], problems
```

Also test the cases that must leave the store alone: a root with no
marker, a store at another schema version, a person's file in `wip/`
across a second run, and an unknown key in a JSON file you rewrite.

## The claim

A product may be described as "MCAD_base 2 compatible" when:

1. What it writes passes `check`, and `verify` for any revision it
   released.
2. It writes only where its kind of tool may write.
3. It names the schema version it targets and writes to no other.
4. Everything it writes stays readable and verifiable with the open tools
   alone. Take the product away and the store still passes.

## Mistakes to avoid

- Storing state in a database or a hidden cache that the store needs in
  order to make sense.
- Creating the store root when it is missing.
- Capitals or spaces in file names under `released/`.
- Listing a machine model in `proven_on` when the revision holds only an
  unsliced project file.
- Writing `SHA256SUMS` before the last file is in place, or leaving a
  file out of it.
- Editing a revision to fix a mistake. Release a new revision.
- Rewriting `history.jsonl` or `design.json` from your own model and
  losing lines or keys you did not write.
- Backslashes in paths written to `manifest.json` or `SHA256SUMS`.
- Adding accounts, roles, lock files, or a required command to start a
  design.
