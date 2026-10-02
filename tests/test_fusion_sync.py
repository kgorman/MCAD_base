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
                         ["design.json", "history.jsonl", "photo.jpg", "released", "wip"])
        self.assertEqual((self.design / "released" / "rev-a" / "SHA256SUMS").read_text(), "frozen")
        self.assertEqual((self.design / "photo.jpg").read_text(), "loose")

    def test_second_run_is_noop(self):
        self.run_sync()
        stats = self.run_sync()
        self.assertEqual(stats.downloaded, 0)
        self.assertEqual(stats.skipped, 2)
        self.assertEqual(len(self.api.export_calls), 1)
        self.assertEqual(len(self.history(self.design)), 1)

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
        self.assertEqual(fs.choose_formats(["native"], ["f3d", "f3z", "step"], "design"), ["f3z"])
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
