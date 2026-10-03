# fusion_sync

Part of MCAD_base. See the [README](../README.md) for how this fits with the shop tree.

Keep a local, browsable mirror of every Fusion 360 design in your Autodesk cloud hubs.

Fusion designs are not files on disk. They are versioned records in Autodesk's cloud
(Fusion Team / Autodesk Docs). The local cache Fusion keeps under
`~/Library/Application Support/Autodesk/Autodesk Fusion 360/` is an opaque blob store,
not something you can open, back up, or diff. This tool asks the cloud to export each
design's latest version to a real file and writes it to a folder tree that mirrors
your hubs, projects, and folders. Every cloud item becomes a folder, and the export
goes in its `wip/` subfolder.

```
/path/to/store/
  Kenny's Hub/
    Bike/
      Headset Spacers/
        design.json              <- Fusion item id, part number, current wip version
        history.jsonl            <- one line per sync, move, or cloud delete
        wip/
          Headset Spacers.f3d
          Headset Spacers.f3z
          Headset Spacers.step
          _versions/             <- prior copies, kept when a design changes
        released/                <- not sync's; never touched
      Reference/
        vendor-part.pdf/
          wip/vendor-part.pdf    <- uploaded files come down as-is
  .fusion-sync/
    manifest.json                <- which cloud version each file came from
    sync.log
```

Runs are incremental. Only designs whose cloud version changed since the last run are
re-exported.

Inside an item's folder, sync writes `wip/`, `design.json`, `history.jsonl`, and the
`DELETED_IN_CLOUD` marker. It never touches anything else there, so release records and
loose files are safe from it. In `design.json` it owns its own keys and preserves the
rest, including `part_number`.

| In the cloud | On disk |
|---|---|
| New version of a design | `wip/` is updated; the prior copy moves to `wip/_versions/` |
| Design renamed or moved | The whole folder moves, releases and loose files included. No re-export. |
| Design deleted | The folder stays and gets a `DELETED_IN_CLOUD` marker. Nothing is removed. |
| Deleted design restored | The marker is removed. |

Deletes are only recognized on a complete pass: no `--hub` or `--project` filter and no
errors. A partial pass cannot tell "deleted" from "not looked at".

## One-time setup

1. Create an app at <https://aps.autodesk.com/myapps>.
   - Type: **Desktop, Mobile, Single-Page App** (uses PKCE, no secret needed).
   - Callback URL: `http://localhost:8912/callback`
   - APIs: **Data Management API** (the default set is fine).
   - Copy the **Client ID**.
2. Set up the store, if it is not one already: `./mcad_tree.py init <root>`.
   The sync writes only into a folder that `init` has marked as a store at
   the schema version it targets. Otherwise it stops and writes nothing,
   which also keeps it from building the tree on the local disk when the
   share is not mounted.
3. Configure and sign in:

```sh
chmod +x fusion_sync.py
./fusion_sync.py init --client-id <CLIENT_ID> --root /path/to/store
./fusion_sync.py auth        # browser opens once; refresh token is cached
./fusion_sync.py hubs        # lists hubs and projects to confirm access
```

Config lives in `~/.config/fusion-sync/config.json`, tokens in
`~/.config/fusion-sync/tokens.json` (mode 0600).

## Daily use

```sh
./fusion_sync.py sync                        # mirror everything, native format
./fusion_sync.py sync --dry-run              # see what would change
./fusion_sync.py sync --formats native,step  # also keep a STEP next to each design
./fusion_sync.py sync --project "Bike"
./fusion_sync.py status
```

`native` keeps a design in both of Fusion's own formats, fetched one after
the other before the sync moves to the next design:

- the `.f3d`, the design file as the cloud stores it. It downloads as it is,
  with no export job, so it takes seconds.
- the `.f3z`, an archive the cloud builds on request. It also carries copies
  of the other designs an assembly references. Building it takes longer.

Drawings export as PDF. Other formats accepted by the
cloud exporter: `step`, `iges`, `sat`, `smt`, `stl`, `obj`, `fbx`, `dwg`, `dxf`, `pdf`.
Formats the cloud cannot produce for a given item are skipped with a note.

Sometimes the cloud lists a design's archive as available and then fails to
build it. The design then has its `.f3d` alone, and the sync records the
failed export in the design's `history.jsonl`. It does not ask again for
that version; a new version in the cloud gets a fresh try. A `.f3d` does not
carry copies of the other designs an assembly references. An export that
fails on a network or service error is not recorded this way, and the next
run tries it again.

## Run it in the background

```sh
./fusion_sync.py install-launchd --interval 900   # every 15 minutes, survives reboots
./fusion_sync.py uninstall-launchd
```

Or in the foreground: `./fusion_sync.py watch --interval 900`.

## How it works

1. **Auth**: 3-legged OAuth 2.0 with PKCE against `authentication/v2`. The refresh
   token is used silently after the first sign-in.
2. **Walk**: `GET project/v1/hubs` → `.../projects` → `.../topFolders` →
   `GET data/v1/projects/{p}/folders/{f}/contents` (recursive, paginated). Each item's
   tip version comes back in the `included` array.
3. **Decide**: compare the tip version id with `manifest.json`. Unchanged items are skipped.
4. **Export**: for Fusion items, `GET .../versions/{v}/downloadFormats` lists what the
   cloud can produce; `POST data/v1/projects/{p}/downloads` starts an export job;
   `GET .../jobs/{job}` is polled until it turns into a `downloads` object whose
   `relationships.storage` points at the file. Uploaded (non-Fusion) files are fetched
   directly from their storage object via a signed S3 URL.
5. **Write**: download to a temp file, atomically rename into place, update the manifest.
   When a design's version changes, the previous local copy moves to
   `wip/_versions/` in the design's folder. Each design folder also gets an
   empty `released/` and `jobs/`, made once and never written to again, so
   anyone browsing the store sees where releases and job records go.

Rate limits and transient errors get exponential backoff with `Retry-After` honored.

## Limitations

- One-way, cloud to disk. Editing a local `.f3d` does not upload it. Upload it through
  Fusion if you want it back in the cloud.
- Exports are cloud-side jobs. A big assembly can take a minute or two per format on the
  first run; later runs only touch what changed.
- Only hubs your Autodesk account can see through the API are mirrored. Personal hubs
  work; some education or admin-locked team hubs may not expose the Data Management API.
- A project's single root folder is flattened so the path reads `hub/project/design`.
  This has not yet been confirmed against a live hub.
- Design history is preserved inside `.f3d`/`.f3z` archives. STEP/STL exports are
  geometry only.

## Alternative: a Fusion add-in

If you would rather not register an APS app, the same job can be done from inside
Fusion with its Python add-in API (`adsk.core.Application.data` to walk hubs, then
`exportManager` per design). That approach needs Fusion open and is slower because it
loads each design, but it needs no cloud credentials. This tool was built the API way
so it can run unattended.
