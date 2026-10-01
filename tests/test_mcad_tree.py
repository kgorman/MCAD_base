#!/usr/bin/env python3
"""Tests for mcad_tree: sets up a store in a temp folder and checks it."""

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mcad_tree as mt  # noqa: E402


def quiet(_line):
    pass


class McadTreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make_design(self, rel="Kevin's Hub/Bike/Headset Spacers", item_id="item_1", **info):
        design = self.root / rel
        (design / "wip").mkdir(parents=True)
        (design / "wip" / f"{design.name}.f3d").write_text("f3d")
        data = {"schema": 2, "fusion_item_id": item_id, "name": design.name, "kind": "design",
                "hub": "Kevin's Hub", "project": "Bike", "part_number": None,
                "wip": {"version_number": 3}}
        data.update(info)
        (design / "design.json").write_text(json.dumps(data))
        return design

    def make_revision(self, design, rev="rev-a", current=True):
        path = design / "released" / rev
        (path / "cad").mkdir(parents=True)
        (path / "cad" / "bm-0042.step").write_text("")
        (path / "manifest.json").write_text("{}")
        (path / "SHA256SUMS").write_text("")
        if current:
            (design / "released" / "CURRENT").write_text(rev + "\n")
        return path

    # -- init -------------------------------------------------------------- #

    def test_init_marks_store_and_passes_check(self):
        changed = mt.init_tree(self.root, out=quiet)
        self.assertEqual(changed, 3)  # README.md, SCHEMA.md, marker; no folders
        self.assertEqual(json.loads((self.root / mt.MARKER).read_text())["schema"], mt.SCHEMA_VERSION)
        self.assertEqual(mt.check_tree(self.root), ([], []))

    def test_init_is_idempotent(self):
        mt.init_tree(self.root, out=quiet)
        marker = (self.root / mt.MARKER).read_text()
        self.assertEqual(mt.init_tree(self.root, out=quiet), 0)
        self.assertEqual((self.root / mt.MARKER).read_text(), marker)

    def test_dry_run_writes_nothing(self):
        self.assertGreater(mt.init_tree(self.root, dry_run=True, out=quiet), 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_files_are_left_alone(self):
        (self.root / "README.md").write_text("mine")
        mt.init_tree(self.root, out=quiet)
        self.assertEqual((self.root / "README.md").read_text(), "mine")
        _, notes = mt.check_tree(self.root)
        self.assertTrue(any(n.startswith("README.md differs") for n in notes))
        self.assertEqual(mt.init_tree(self.root, update_docs=True, out=quiet), 1)
        self.assertNotEqual((self.root / "README.md").read_text(), "mine")

    def test_missing_root_is_refused(self):
        with self.assertRaises(mt.TreeError):
            mt.init_tree(self.root / "not-mounted", out=quiet)
        self.assertFalse((self.root / "not-mounted").exists())

    def test_other_schema_is_refused(self):
        (self.root / mt.MARKER).write_text(json.dumps({"schema": 1}))
        with self.assertRaises(mt.TreeError):
            mt.init_tree(self.root, out=quiet)

    # -- check ------------------------------------------------------------- #

    def test_check_uninitialized(self):
        problems, _ = mt.check_tree(self.root)
        self.assertTrue(any("not been initialized" in p for p in problems))

    def test_check_accepts_a_design_with_loose_material(self):
        mt.init_tree(self.root, out=quiet)
        design = self.make_design()
        self.make_revision(design)
        (design / "Photo of First Print.jpg").write_text("")   # cloud and human names are free
        (design / "builds" / "2026-09-27_p1s-01").mkdir(parents=True)
        (self.root / "Kevin's Hub" / "Bike" / ".DS_Store").write_text("")
        self.assertEqual(mt.check_tree(self.root), ([], []))

    def test_check_flags_design_without_identity_and_duplicates(self):
        mt.init_tree(self.root, out=quiet)
        self.make_design("Hub/Bike/A", item_id="same")
        self.make_design("Hub/Bike/B", item_id="same")
        self.make_design("Hub/Bike/C", item_id=None)
        problems, _ = mt.check_tree(self.root)
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("same Fusion item" in p for p in problems))
        self.assertTrue(any("no fusion_item_id" in p for p in problems))

    def test_check_flags_bad_revisions(self):
        mt.init_tree(self.root, out=quiet)
        design = self.make_design()
        rev = self.make_revision(design, current=False)
        (rev / "SHA256SUMS").unlink()
        (rev / "cad" / "Final FINAL.step").write_text("")
        (design / "released" / "Rev B").mkdir()
        problems, _ = mt.check_tree(self.root)
        text = "\n".join(problems)
        self.assertIn("missing SHA256SUMS", text)
        self.assertIn("name breaks the naming rule", text)
        self.assertIn("not a revision folder", text)
        self.assertIn("no CURRENT", text)
        self.assertEqual(len(problems), 4)

    def test_check_flags_current_pointing_nowhere(self):
        mt.init_tree(self.root, out=quiet)
        design = self.make_design()
        self.make_revision(design)
        (design / "released" / "CURRENT").write_text("rev-z\n")
        problems, _ = mt.check_tree(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("rev-z", problems[0])

    def test_check_notes_root_strays_and_cloud_deletes(self):
        mt.init_tree(self.root, out=quiet)
        design = self.make_design()
        (design / mt.DELETED_MARKER).write_text("")
        (self.root / "stray.txt").write_text("")
        (self.root / "_junk").mkdir()
        problems, notes = mt.check_tree(self.root)
        self.assertEqual(problems, [])
        self.assertEqual(len(notes), 3)

    # -- index ------------------------------------------------------------- #

    def test_index_lists_designs_by_part_number(self):
        mt.init_tree(self.root, out=quiet)
        self.make_design("Hub/Bike/Stem Cap", item_id="a")
        spacer = self.make_design("Hub/Bike/Headset Spacers", item_id="b", part_number="bm-0042")
        self.make_revision(spacer)
        self.assertEqual(mt.build_index(self.root, out=quiet), 1)
        with (self.root / "_index" / "parts.csv").open() as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual([r["part_number"] for r in rows], ["bm-0042", ""])
        self.assertEqual(rows[0]["path"], "Hub/Bike/Headset Spacers")
        self.assertEqual(rows[0]["current_revision"], "rev-a")
        self.assertEqual(rows[0]["wip_version"], "3")
        self.assertEqual(mt.build_index(self.root, out=quiet), 0)  # unchanged, not rewritten
        self.assertEqual(mt.check_tree(self.root), ([], []))       # _index is not a hub

    def test_index_requires_a_store(self):
        with self.assertRaises(mt.TreeError):
            mt.build_index(self.root, out=quiet)

    # -- outbox ------------------------------------------------------------ #

    def test_add_machine(self):
        mt.init_tree(self.root, out=quiet)
        self.assertEqual(mt.add_machine(self.root, "haas-vf2", out=quiet), 1)
        self.assertTrue((self.root / "_outbox" / "haas-vf2").is_dir())
        self.assertEqual(mt.add_machine(self.root, "haas-vf2", out=quiet), 0)
        with self.assertRaises(mt.TreeError):
            mt.add_machine(self.root, "Haas VF2", out=quiet)
        self.assertEqual(mt.check_tree(self.root), ([], []))


if __name__ == "__main__":
    unittest.main()
