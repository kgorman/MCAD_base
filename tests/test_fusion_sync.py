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
        self.export_calls = []
        self.files = {}  # href -> bytes

    def hubs(self):
        return [{"id": "h1", "attributes": {"name": "Kevin's Hub"}}]

    def projects(self, hub_id):
        return [{"id": "p1", "attributes": {"name": "Shop/Projects"}}]  # slash must be sanitized

    def top_folders(self, hub_id, project_id):
        return [{"id": "f_root", "attributes": {"name": "Project Files"}}]

    def folder_contents(self, project_id, folder_id):
        if folder_id == "f_root":
            yield {"type": "folders", "id": "f_sub", "attributes": {"displayName": "Fixtures"}}
            yield {
                "type": "items", "id": "item_design",
                "attributes": {"displayName": "Bench Vise", "extension": {"type": "items:autodesk.fusion360:Design"}},
                "relationships": {"tip": {"data": {"id": self.design_version}}},
                "_tip": {
                    "id": self.design_version,
                    "attributes": {"displayName": "Bench Vise", "versionNumber": int(self.design_version[-1]),
                                   "extension": {"type": "versions:autodesk.fusion360:Design"}},
                    "relationships": {},
                },
            }
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
        self.root = Path(self.tmp.name) / "mirror"
        self.cfg = fs.Config(client_id="x", root=self.root)
        self.api = FakeAps()

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self, formats=("native",), dry_run=False):
        return fs.Syncer(self.cfg, self.api, formats=list(formats), dry_run=dry_run).run()

    def test_first_run_mirrors_tree(self):
        stats = self.run_sync()
        design = self.root / "Kevin's Hub" / "Shop_Projects" / "Project Files" / "Bench Vise.f3d"
        pdf = self.root / "Kevin's Hub" / "Shop_Projects" / "Project Files" / "Fixtures" / "vendor.pdf"
        self.assertTrue(design.exists(), design)
        self.assertTrue(pdf.exists(), pdf)
        self.assertEqual(stats.downloaded, 2)
        self.assertEqual(stats.scanned, 2)  # hidden item never counted
        self.assertEqual(self.api.export_calls, [("urn:v:design?version=3", "f3d")])
        manifest = json.loads((self.root / ".fusion-sync" / "manifest.json").read_text())
        self.assertEqual(manifest["items"]["item_design"]["version_number"], 3)

    def test_second_run_is_noop(self):
        self.run_sync()
        stats = self.run_sync()
        self.assertEqual(stats.downloaded, 0)
        self.assertEqual(stats.skipped, 2)
        self.assertEqual(len(self.api.export_calls), 1)

    def test_new_version_reexports_and_archives_old(self):
        self.run_sync()
        self.api.design_version = "urn:v:design?version=4"
        stats = self.run_sync()
        self.assertEqual(stats.downloaded, 1)
        archived = self.root / ".fusion-sync" / "_versions" / "Kevin's Hub" / "Shop_Projects" / "Project Files" / "Bench Vise.v3.f3d"
        self.assertTrue(archived.exists(), archived)
        current = self.root / "Kevin's Hub" / "Shop_Projects" / "Project Files" / "Bench Vise.f3d"
        self.assertIn("4.f3d", current.read_text())

    def test_added_format_fetches_only_missing(self):
        self.run_sync()
        stats = self.run_sync(formats=("native", "step", "obj"))  # obj not offered by cloud -> skipped silently
        self.assertEqual(stats.downloaded, 1)
        self.assertEqual(self.api.export_calls[-1], ("urn:v:design?version=3", "step"))
        self.assertTrue((self.root / "Kevin's Hub" / "Shop_Projects" / "Project Files" / "Bench Vise.step").exists())

    def test_dry_run_writes_nothing(self):
        stats = self.run_sync(dry_run=True)
        self.assertEqual(stats.downloaded, 0)
        self.assertFalse(self.root.exists() and any(self.root.rglob("*.f3d")))
        self.assertEqual(self.api.export_calls, [])


class HelperTests(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(fs.safe_name("a/b:c*d"), "a_b_c_d")
        self.assertEqual(fs.safe_name("  trailing. "), "trailing")
        self.assertEqual(fs.safe_name(""), "_unnamed")

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
