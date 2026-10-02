#!/usr/bin/env python3
"""The two tools together: a store set up by mcad_tree and filled by fusion_sync must pass check."""

import contextlib
import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fusion_sync as fs  # noqa: E402
import mcad_tree as mt  # noqa: E402
from test_fusion_sync import FakeAps  # noqa: E402


def quiet(_line):
    pass


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.api = FakeAps()
        mt.init_tree(self.root, out=quiet)

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return fs.Syncer(fs.Config(client_id="x", root=self.root), self.api, formats=["native"]).run()

    def test_tools_agree_on_names(self):
        for name in ("DESIGN_FILE", "HISTORY_FILE", "WIP_DIRNAME", "DELETED_MARKER"):
            self.assertEqual(getattr(fs, name), getattr(mt, name), name)
        self.assertEqual(fs.STORE_MARKER, mt.MARKER)
        self.assertEqual(fs.STORE_SCHEMA, mt.SCHEMA_VERSION)
        self.assertEqual(fs.STORE_SCHEMA, mt.SCHEMA_VERSION)

    def test_synced_store_passes_check_and_indexes(self):
        self.sync()
        self.assertEqual(mt.check_tree(self.root), ([], []))
        self.assertEqual(len(list(mt.find_designs(self.root))), 2)

        design = self.root / "Kenny's Hub" / "Shop_Projects" / "Bench Vise"
        info = json.loads((design / "design.json").read_text())
        info["part_number"] = "bv-0001"
        (design / "design.json").write_text(json.dumps(info))
        mt.build_index(self.root, out=quiet)
        with (self.root / "_index" / "parts.csv").open() as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["part_number"], "bv-0001")
        self.assertEqual(rows[0]["path"], "Kenny's Hub/Shop_Projects/Bench Vise")
        self.assertEqual(rows[0]["wip_version"], "3")

        self.sync()  # the index and docs at the root do not confuse the next sync
        self.assertEqual(mt.check_tree(self.root), ([], []))

    def test_sync_leaves_a_persons_files_in_wip_alone(self):
        self.sync()
        wip = self.root / "Kenny's Hub" / "Shop_Projects" / "Bench Vise" / "wip"
        (wip / "jaw-insert_test.3mf").write_text("mine")
        (wip / "notes.txt").write_text("mine")
        self.api.design_version = "urn:v:design?version=4"
        self.sync()                                    # a new cloud version replaces only the export
        self.assertEqual((wip / "jaw-insert_test.3mf").read_text(), "mine")
        self.assertEqual((wip / "notes.txt").read_text(), "mine")
        self.assertEqual(mt.check_tree(self.root), ([], []))

    def test_cloud_delete_shows_up_in_check_and_index(self):
        self.sync()
        self.api.design_present = False
        self.sync()
        problems, notes = mt.check_tree(self.root)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["deleted in the cloud, kept here: Kenny's Hub/Shop_Projects/Bench Vise"])
        mt.build_index(self.root, out=quiet)
        with (self.root / "_index" / "parts.csv").open() as fh:
            flagged = [r["name"] for r in csv.DictReader(fh) if r["deleted_in_cloud"]]
        self.assertEqual(flagged, ["Bench Vise"])


if __name__ == "__main__":
    unittest.main()
