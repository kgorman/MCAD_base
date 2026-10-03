#!/usr/bin/env python3
"""Offline tests for fusion_sync: exercises the sync engine against a fake cloud."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fusion_sync as fs  # noqa: E402


class FakeAps:
    """Mimics the subset of the Data Management API the syncer touches."""

    def __init__(self):
        self.design_version = "urn:v:design?version=3"
        self.design_name = "Bench Vise"
        self.design_folder = "f_root"   # move it by pointing this at "f_sub"
        self.design_present = True
        self.top_folder_names = ["Shop_Projects"]
        self.export_calls = []
        self.files = {}  # href -> bytes

    def hubs(self):
        return [{"id": "h1", "attributes": {"name": "Kenny's Hub"}}]

    def projects(self, hub_id):
        return [{"id": "p1", "attributes": {"name": "Shop/Projects"}}]  # slash must be sanitized

    def top_folders(self, hub_id, project_id):
        tops = [{"id": "f_root", "attributes": {"name": self.top_folder_names[0]}}]
        tops += [{"id": f"f_extra{i}", "attributes": {"name": n}} for i, n in enumerate(self.top_folder_names[1:])]
        return tops

    def _design(self):
        return {
            "type": "items", "id": "item_design",
            "attributes": {"displayName": self.design_name, "extension": {"type": "items:autodesk.fusion360:Design"}},
            "relationships": {"tip": {"data": {"id": self.design_version}}},
            "_tip": {
                "id": self.design_version,
                "attributes": {"displayName": self.design_name, "versionNumber": int(self.design_version[-1]),
                               "extension": {"type": "versions:autodesk.fusion360:Design"}},
                "relationships": {},
            },
        }

    def folder_contents(self, project_id, folder_id):
        if self.design_present and folder_id == self.design_folder:
            yield self._design()
        if folder_id == "f_root":
            yield {"type": "folders", "id": "f_sub", "attributes": {"displayName": "Fixtures"}}
            yield {"type": "items", "id": "hidden", "attributes": {"hidden": True, "displayName": "gone"}}
        elif folder_id == "f_sub":
            yield {
                "type": "items", "id": "item_pdf",
                "attributes": {"displayName": "vendor.pdf", "extension": {"type": "items:autodesk.core:File"}},
                "relationships": {"tip": {"data": {"id": "urn:v:pdf?version=1"}}},
                "_tip": {
                    "id": "urn:v:pdf?version=1",
                    "attributes": {"name": "vendor.pdf", "versionNumber": 1, "extension": {"type": "versions:autodesk.core:File"}},
                    "relationships": {"storage": {"data": {"id": "urn:adsk.objects:os.object:wip.dm.prod/abc.pdf"},
                                                  "meta": {"link": {"href": "https://signed.example/abc.pdf"}}}},
                },
            }

    def download_formats(self, project_id, version_id):
        return ["f3d", "step", "stl"]

    def existing_downloads(self, project_id, version_id, file_type):
        return None

    def export_version(self, project_id, version_id, file_type):
        self.export_calls.append((version_id, file_type))
        return f"https://signed.example/{version_id[-1]}.{file_type}"

    def download_to(self, href, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f"bytes-of:{href}".encode())
        return dest.stat().st_size


class SyncEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "store"
        self.root.mkdir()
        self.marker = self.root / fs.STORE_MARKER   # what mcad_tree.py init leaves behind
        self.marker.write_text(json.dumps({"schema": fs.STORE_SCHEMA}))
        self.cfg = fs.Config(client_id="x", root=self.root)
        self.api = FakeAps()
        self.project = self.root / "Kenny's Hub" / "Shop_Projects"
        self.design = self.project / "Bench Vise"

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self, formats=("native",), dry_run=False, **kw):
        return fs.Syncer(self.cfg, self.api, formats=list(formats), dry_run=dry_run, **kw).run()

    def history(self, item_dir):
        return [json.loads(line) for line in (item_dir / "history.jsonl").read_text().splitlines()]

    def stored_design(self):
        """What the real cloud does: it holds the design's own file and offers the archive as an export."""
        api = self.api
        tip = api._design
        def design():
            d = tip()
            d["_tip"]["relationships"] = {"storage": {"data": {"id": "urn:adsk.objects:os.object:wip.dm.prod/stored.f3d"}}}
            return d
        api._design = design
        api.download_formats = lambda project_id, version_id: ["f3z"]

    def failing_export(self):
        """The cloud lists the design but cannot build its export; the stored file is still there."""
        self.stored_design()
        api = self.api
        def export(project_id, version_id, file_type):
            api.export_calls.append((version_id, file_type))
            raise RuntimeError("Export job failed")
        api.export_version = export

    def test_native_keeps_the_stored_file_and_the_archive(self):
        self.stored_design()
        stats = self.run_sync()
        self.assertEqual((stats.downloaded, stats.failed), (3, 0))   # .f3d, .f3z, and the uploaded pdf
        stored = self.design / "wip" / "Bench Vise.f3d"
        self.assertEqual(stored.read_bytes(), b"bytes-of:urn:adsk.objects:os.object:wip.dm.prod/stored.f3d")
        self.assertEqual((self.design / "wip" / "Bench Vise.f3z").read_bytes(), b"bytes-of:https://signed.example/3.f3z")
        self.assertEqual(self.api.export_calls, [(self.api.design_version, "f3z")])   # the .f3d needs no export
        self.assertEqual(self.history(self.design)[-1]["files"], ["Bench Vise.f3d", "Bench Vise.f3z"])
        self.assertEqual(self.run_sync().downloaded, 0)

    def test_store_holding_only_the_archive_gains_the_stored_file(self):
        self.stored_design()
        self.run_sync(formats=("f3z",))
        self.assertFalse((self.design / "wip" / "Bench Vise.f3d").exists())
        stats = self.run_sync()
        self.assertEqual(stats.downloaded, 1)
        self.assertTrue((self.design / "wip" / "Bench Vise.f3d").exists())
        self.assertEqual(len(self.api.export_calls), 1)              # the archive is not exported again
        record = json.loads((self.root / ".fusion-sync" / "manifest.json").read_text())["items"]["item_design"]
        self.assertEqual(sorted(record["files"]), ["f3d", "f3z"])

    def test_failed_export_of_the_archive_alone_falls_back_to_the_stored_file(self):
        self.failing_export()
        stats = self.run_sync(formats=("f3z",))
        self.assertEqual(stats.failed, 0)
        self.assertTrue((self.design / "wip" / "Bench Vise.f3d").exists())
        record = json.loads((self.root / ".fusion-sync" / "manifest.json").read_text())["items"]["item_design"]
        self.assertEqual(record["export_failed"], {"f3z": self.api.design_version})

    def test_failed_export_keeps_the_stored_file(self):
        self.failing_export()
        stats = self.run_sync()
        self.assertEqual(stats.failed, 0)
        stored = self.design / "wip" / "Bench Vise.f3d"
        self.assertEqual(stored.read_bytes(), b"bytes-of:urn:adsk.objects:os.object:wip.dm.prod/stored.f3d")
        record = json.loads((self.root / ".fusion-sync" / "manifest.json").read_text())["items"]["item_design"]
        self.assertEqual(record["export_failed"], {"f3z": self.api.design_version})
        self.assertEqual(record["files"], {"f3d": "Kenny's Hub/Shop_Projects/Bench Vise/wip/Bench Vise.f3d"})
        self.assertEqual(self.history(self.design)[-1]["export_failed"], ["f3z"])
        self.assertFalse((self.design / "wip" / "Bench Vise.f3z").exists())
        self.assertEqual(json.loads((self.design / "design.json").read_text())["wip"]["files"], ["Bench Vise.f3d"])

    def test_failed_export_is_not_retried_until_a_new_version(self):
        self.failing_export()
        self.run_sync()
        calls = len(self.api.export_calls)
        stats = self.run_sync()
        self.assertEqual(len(self.api.export_calls), calls)      # same version: left alone
        self.assertEqual((stats.downloaded, stats.failed), (0, 0))
        self.api.design_version = "urn:v:design?version=4"
        self.run_sync()
        self.assertEqual(len(self.api.export_calls), calls + 1)  # new version: tried again
        self.assertTrue((self.design / "wip" / "_versions" / "Bench Vise.v3.f3d").exists())

    def test_http_error_during_export_is_retried_on_the_next_run(self):
        self.stored_design()
        export = self.api.export_version
        def unavailable(project_id, version_id, file_type):
            raise fs.ApiError(503, "unavailable", "https://example/export")
        self.api.export_version = unavailable
        stats = self.run_sync()
        self.assertEqual(stats.failed, 1)
        record = json.loads((self.root / ".fusion-sync" / "manifest.json").read_text())["items"]["item_design"]
        self.assertNotIn("export_failed", record)
        self.assertEqual(sorted(record["files"]), ["f3d"])           # the stored file still came down
        self.api.export_version = export
        self.run_sync()
        self.assertTrue((self.design / "wip" / "Bench Vise.f3z").exists())

    def test_failed_export_without_a_stored_file_is_a_failure(self):
        def export(project_id, version_id, file_type):
            raise RuntimeError("Export job failed")
        self.api.export_version = export
        stats = self.run_sync()
        self.assertEqual(stats.failed, 1)
        self.assertFalse(self.design.exists())

    def test_first_run_builds_one_folder_per_item(self):
        stats = self.run_sync()
        # the project's single root folder is flattened away
        self.assertTrue((self.design / "wip" / "Bench Vise.f3d").exists())
        self.assertTrue((self.project / "Fixtures" / "vendor.pdf" / "wip" / "vendor.pdf").exists())
        self.assertEqual(stats.downloaded, 2)
        self.assertEqual(stats.scanned, 2)  # hidden item never counted
        self.assertEqual(self.api.export_calls, [("urn:v:design?version=3", "f3d")])
        manifest = json.loads((self.root / ".fusion-sync" / "manifest.json").read_text())
        self.assertEqual(manifest["items"]["item_design"]["version_number"], 3)
        self.assertEqual(manifest["items"]["item_design"]["dir"], "Kenny's Hub/Shop_Projects/Bench Vise")

    def test_every_design_gets_empty_released_and_jobs_folders(self):
        self.run_sync()
        for area in ("released", "jobs"):
            self.assertTrue((self.design / area).is_dir())
            self.assertEqual(list((self.design / area).iterdir()), [])
        (self.design / "jobs").rmdir()
        self.run_sync()                                         # an up-to-date design gets them back
        self.assertTrue((self.design / "jobs").is_dir())
        self.assertFalse((self.root / "Kenny's Hub" / "released").exists())   # only inside designs

    def test_design_json_and_history(self):
        self.run_sync()
        info = json.loads((self.design / "design.json").read_text())
        self.assertEqual(info["fusion_item_id"], "item_design")
        self.assertEqual(info["hub"], "Kenny's Hub")
        self.assertEqual(info["project"], "Shop/Projects")  # the cloud's name, not the sanitized one
        self.assertIsNone(info["part_number"])
        self.assertEqual(info["wip"]["version_number"], 3)
        self.assertEqual(info["wip"]["files"], ["Bench Vise.f3d"])
        events = self.history(self.design)
        self.assertEqual([e["event"] for e in events], ["synced"])
        self.assertEqual(events[0]["version_number"], 3)

    def test_sync_preserves_fields_it_does_not_own(self):
        self.run_sync()
        path = self.design / "design.json"
        info = json.loads(path.read_text())
        info["part_number"] = "bm-0042"
        info["customer"] = "internal"
        path.write_text(json.dumps(info))
        self.api.design_version = "urn:v:design?version=4"
        self.run_sync()
        info = json.loads(path.read_text())
        self.assertEqual(info["part_number"], "bm-0042")
        self.assertEqual(info["customer"], "internal")
        self.assertEqual(info["wip"]["version_number"], 4)

    def test_sync_only_writes_its_own_names(self):
        self.run_sync()
        (self.design / "released" / "rev-a").mkdir(parents=True)
        (self.design / "released" / "rev-a" / "SHA256SUMS").write_text("frozen")
        (self.design / "photo.jpg").write_text("loose")
        self.api.design_version = "urn:v:design?version=4"
        self.run_sync(formats=("native", "step"))
        self.assertEqual(sorted(p.name for p in self.design.iterdir()),
                         ["design.json", "history.jsonl", "jobs", "photo.jpg", "released", "wip"])
        self.assertEqual((self.design / "released" / "rev-a" / "SHA256SUMS").read_text(), "frozen")
        self.assertEqual((self.design / "photo.jpg").read_text(), "loose")

    def test_second_run_is_noop(self):
        self.run_sync()
        stats = self.run_sync()
        self.assertEqual(stats.downloaded, 0)
        self.assertEqual(stats.skipped, 2)
        self.assertEqual(len(self.api.export_calls), 1)
        self.assertEqual(len(self.history(self.design)), 1)

    def test_stopped_run_keeps_what_it_fetched(self):
        download = self.api.download_to
        def stop_at_the_pdf(href, dest):
            if dest.name == "vendor.pdf":
                raise KeyboardInterrupt
            return download(href, dest)
        self.api.download_to = stop_at_the_pdf
        with self.assertRaises(KeyboardInterrupt):
            self.run_sync()
        manifest = json.loads((self.root / ".fusion-sync" / "manifest.json").read_text())
        self.assertIn("item_design", manifest["items"])
        self.api.download_to = download
        stats = self.run_sync()
        self.assertEqual(stats.downloaded, 1)   # only the file the first run never got
        self.assertEqual(len(self.api.export_calls), 1)

    def test_new_version_reexports_and_archives_old(self):
        self.run_sync()
        self.api.design_version = "urn:v:design?version=4"
        stats = self.run_sync()
        self.assertEqual(stats.downloaded, 1)
        self.assertTrue((self.design / "wip" / "_versions" / "Bench Vise.v3.f3d").exists())
        self.assertIn("4.f3d", (self.design / "wip" / "Bench Vise.f3d").read_text())
        self.assertEqual([e["version_number"] for e in self.history(self.design)], [3, 4])

    def test_added_format_fetches_only_missing(self):
        self.run_sync()
        stats = self.run_sync(formats=("native", "step", "obj"))  # obj not offered by cloud -> skipped silently
        self.assertEqual(stats.downloaded, 1)
        self.assertEqual(self.api.export_calls[-1], ("urn:v:design?version=3", "step"))
        self.assertTrue((self.design / "wip" / "Bench Vise.step").exists())

    def test_dry_run_writes_nothing(self):
        stats = self.run_sync(dry_run=True)
        self.assertEqual(stats.downloaded, 0)
        self.assertEqual(os.listdir(self.root), [fs.STORE_MARKER])
        self.assertEqual(self.api.export_calls, [])

    def test_refuses_a_folder_that_is_not_a_store(self):
        self.marker.unlink()
        with self.assertRaises(fs.StoreError) as ctx:
            self.run_sync()
        self.assertIn("is not an MCAD_base store", str(ctx.exception))
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(self.api.export_calls, [])

    def test_refuses_a_root_that_does_not_exist(self):
        # An unmounted share: the sync must not build the tree on the local disk.
        missing = Path(self.tmp.name) / "not-mounted"
        with self.assertRaises(fs.StoreError):
            fs.Syncer(fs.Config(client_id="x", root=missing), self.api, formats=["native"]).run()
        self.assertFalse(missing.exists())

    def test_refuses_a_store_at_another_schema(self):
        self.marker.write_text(json.dumps({"schema": fs.STORE_SCHEMA + 1}))
        with self.assertRaises(fs.StoreError) as ctx:
            self.run_sync()
        self.assertIn(f"schema {fs.STORE_SCHEMA + 1}", str(ctx.exception))
        self.assertEqual(os.listdir(self.root), [fs.STORE_MARKER])
        self.assertEqual(self.api.export_calls, [])

    def test_cloud_rename_moves_the_folder_without_reexport(self):
        self.run_sync()
        (self.design / "released" / "rev-a").mkdir(parents=True)
        (self.design / "released" / "rev-a" / "SHA256SUMS").write_text("frozen")
        self.api.design_name = "Bench Vise Mk2"
        stats = self.run_sync()
        renamed = self.project / "Bench Vise Mk2"
        self.assertFalse(self.design.exists())
        self.assertTrue((renamed / "wip" / "Bench Vise Mk2.f3d").exists())
        self.assertEqual((renamed / "released" / "rev-a" / "SHA256SUMS").read_text(), "frozen")
        self.assertEqual(stats.downloaded, 0)
        self.assertEqual(len(self.api.export_calls), 1)
        info = json.loads((renamed / "design.json").read_text())
        self.assertEqual(info["name"], "Bench Vise Mk2")
        self.assertEqual(info["wip"]["files"], ["Bench Vise Mk2.f3d"])
        self.assertEqual(self.history(renamed)[-1]["event"], "moved")
        self.assertEqual(self.run_sync().downloaded, 0)  # and it stays settled

    def test_cloud_move_between_folders_moves_the_folder(self):
        self.run_sync()
        self.api.design_folder = "f_sub"
        stats = self.run_sync()
        self.assertFalse(self.design.exists())
        self.assertTrue((self.project / "Fixtures" / "Bench Vise" / "wip" / "Bench Vise.f3d").exists())
        self.assertEqual(stats.downloaded, 0)

    def test_dry_run_reports_a_move_without_moving(self):
        self.run_sync()
        self.api.design_name = "Bench Vise Mk2"
        self.run_sync(dry_run=True)
        self.assertTrue((self.design / "wip" / "Bench Vise.f3d").exists())
        self.assertFalse((self.project / "Bench Vise Mk2").exists())

    def test_cloud_delete_marks_and_keeps_then_restores(self):
        self.run_sync()
        self.api.design_present = False
        self.run_sync()
        self.assertTrue((self.design / "DELETED_IN_CLOUD").exists())
        self.assertTrue((self.design / "wip" / "Bench Vise.f3d").exists())
        self.assertEqual(self.history(self.design)[-1]["event"], "deleted_in_cloud")
        self.run_sync()
        self.assertEqual(len(self.history(self.design)), 2)  # marked once, not every night
        self.api.design_present = True
        self.run_sync()
        self.assertFalse((self.design / "DELETED_IN_CLOUD").exists())
        self.assertEqual(self.history(self.design)[-1]["event"], "restored_in_cloud")

    def test_filtered_run_never_marks_deletes(self):
        self.run_sync()
        self.api.design_present = False
        self.run_sync(project_filter=["Shop/Projects"])
        self.assertFalse((self.design / "DELETED_IN_CLOUD").exists())

    def test_same_name_items_get_separate_folders(self):
        self.run_sync()
        info_path = self.design / "design.json"
        info = json.loads(info_path.read_text())
        info["fusion_item_id"] = "some_other_item"
        info_path.write_text(json.dumps(info))
        (self.root / ".fusion-sync" / "manifest.json").unlink()
        self.run_sync()
        tagged = [p.name for p in self.project.iterdir() if p.name.startswith("Bench Vise [")]
        self.assertEqual(len(tagged), 1)
        self.assertEqual(json.loads(info_path.read_text())["fusion_item_id"], "some_other_item")

    def test_multiple_top_folders_keep_their_names(self):
        self.api.top_folder_names = ["Project Files", "Plans"]
        self.run_sync()
        self.assertTrue((self.project / "Project Files" / "Bench Vise" / "wip" / "Bench Vise.f3d").exists())


class HelperTests(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(fs.safe_name("a/b:c*d"), "a_b_c_d")
        self.assertEqual(fs.safe_name("  trailing. "), "trailing")
        self.assertEqual(fs.safe_name(""), "_unnamed")

    def test_safe_name_avoids_names_windows_reserves(self):
        self.assertEqual(fs.safe_name("CON"), "CON_")
        self.assertEqual(fs.safe_name("nul.step"), "nul_.step")
        self.assertEqual(fs.safe_name("COM1"), "COM1_")
        self.assertEqual(fs.safe_name("lpt9.tar.gz"), "lpt9_.tar.gz")
        for ordinary in ("Console", "COM10", "Connector.f3d", "aux bracket"):
            self.assertEqual(fs.safe_name(ordinary), ordinary)

    def test_choose_formats(self):
        self.assertEqual(fs.choose_formats(["native"], ["f3d", "f3z", "step"], "design"), ["f3d", "f3z"])
        self.assertEqual(fs.choose_formats(["native"], ["f3z"], "design"), ["f3z"])
        self.assertEqual(fs.choose_formats(["native"], ["f3z"], "design", stored=True), ["f3d", "f3z"])
        self.assertEqual(fs.choose_formats(["f3d"], ["f3z"], "design", stored=True), ["f3d"])
        self.assertEqual(fs.choose_formats(["native"], ["pdf"], "drawing", stored=True), ["pdf"])
        self.assertEqual(fs.choose_formats(["native"], ["f3d", "step"], "design"), ["f3d"])
        self.assertEqual(fs.choose_formats(["native"], [], "design"), ["f3d"])
        self.assertEqual(fs.choose_formats(["native", "step", "step"], ["f3d", "step"], "design"), ["f3d", "step"])
        self.assertEqual(fs.choose_formats(["native"], ["pdf", "dwg"], "drawing"), ["pdf"])
        self.assertEqual(fs.choose_formats(["stl"], ["f3d"], "design"), [])

    def test_item_kind(self):
        self.assertEqual(fs.item_kind("versions:autodesk.fusion360:Design"), "design")
        self.assertEqual(fs.item_kind("items:autodesk.fusion360:Drawing"), "drawing")
        self.assertEqual(fs.item_kind("versions:autodesk.core:File"), "other")

    def test_resolve_storage_parses_urn(self):
        class StubAuth:
            pass
        api = fs.Aps(StubAuth())
        seen = {}
        def fake_get(url):
            seen["url"] = url
            return {"url": "https://s3.example/signed"}
        api.get = fake_get
        out = api.resolve_storage("urn:adsk.objects:os.object:wip.dm.prod/abc.f3d")
        self.assertEqual(out, "https://s3.example/signed")
        self.assertIn("/oss/v2/buckets/wip.dm.prod/objects/abc.f3d/signeds3download", seen["url"])
        self.assertEqual(api.resolve_storage("https://already.signed/x"), "https://already.signed/x")
        api.resolve_storage("https://developer.api.autodesk.com/oss/v2/buckets/wip.dm.prod/objects/abc.f3d?scopes=global")
        self.assertTrue(seen["url"].endswith("/oss/v2/buckets/wip.dm.prod/objects/abc.f3d/signeds3download"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
