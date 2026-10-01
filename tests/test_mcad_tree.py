#!/usr/bin/env python3
"""Tests for mcad_tree: scaffolds into a temp folder and checks the result."""

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

    def test_init_creates_tree_and_passes_check(self):
        changed = mt.init_tree(self.root, out=quiet)
        self.assertGreater(changed, 0)
        for rel in mt.DIRS:
            self.assertTrue((self.root / rel).is_dir(), rel)
        self.assertEqual(json.loads((self.root / mt.MARKER).read_text())["schema"], mt.SCHEMA_VERSION)
        self.assertTrue((self.root / "SCHEMA.md").exists())
        problems, notes = mt.check_tree(self.root)
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])

    def test_init_is_idempotent(self):
        mt.init_tree(self.root, out=quiet)
        marker = (self.root / mt.MARKER).read_text()
        self.assertEqual(mt.init_tree(self.root, out=quiet), 0)
        self.assertEqual((self.root / mt.MARKER).read_text(), marker)

    def test_dry_run_writes_nothing(self):
        changed = mt.init_tree(self.root, dry_run=True, out=quiet)
        self.assertGreater(changed, 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_content_is_left_alone(self):
        (self.root / "Supporting").mkdir()
        (self.root / "Supporting" / "NPTExternal.xml").write_text("<x/>")
        (self.root / "README.md").write_text("mine")
        mt.init_tree(self.root, out=quiet)
        self.assertEqual((self.root / "README.md").read_text(), "mine")
        self.assertEqual((self.root / "Supporting" / "NPTExternal.xml").read_text(), "<x/>")
        problems, notes = mt.check_tree(self.root)
        self.assertEqual(problems, [])
        self.assertIn("unmanaged at the root: Supporting", notes)
        self.assertTrue(any(n.startswith("README.md differs") for n in notes))

    def test_update_docs_rewrites_only_when_asked(self):
        mt.init_tree(self.root, out=quiet)
        (self.root / "mirror" / "README.md").write_text("stale")
        self.assertEqual(mt.init_tree(self.root, out=quiet), 0)
        self.assertEqual(mt.init_tree(self.root, update_docs=True, out=quiet), 1)
        self.assertNotEqual((self.root / "mirror" / "README.md").read_text(), "stale")

    def test_missing_root_is_refused(self):
        with self.assertRaises(mt.TreeError):
            mt.init_tree(self.root / "not-mounted", out=quiet)
        self.assertFalse((self.root / "not-mounted").exists())

    def test_newer_schema_is_refused(self):
        (self.root / mt.MARKER).write_text(json.dumps({"schema": mt.SCHEMA_VERSION + 1}))
        with self.assertRaises(mt.TreeError):
            mt.init_tree(self.root, out=quiet)

    def test_check_flags_missing_and_bad_names(self):
        mt.init_tree(self.root, out=quiet)
        (self.root / "print" / "queue").rmdir()
        (self.root / "released" / "BM 0042").mkdir()
        (self.root / "mirror" / "Kevin's Hub").mkdir()  # cloud names are exempt
        (self.root / "nc" / ".DS_Store").write_text("")  # client litter is ignored
        problems, _ = mt.check_tree(self.root)
        self.assertIn("missing folder: print/queue/", problems)
        self.assertIn("name breaks the naming rule: released/BM 0042", problems)
        self.assertEqual(len(problems), 2)

    def test_check_accepts_schema_names(self):
        mt.init_tree(self.root, out=quiet)
        rev = self.root / "released" / "bm-0042" / "rev-a"
        rev.mkdir(parents=True)
        for name in ("SHA256SUMS", "CHANGELOG.md", "manifest.json", "bm-0042_rev-a.3mf"):
            (rev / name).write_text("")
        (self.root / "released" / "bm-0042" / "CURRENT").write_text("rev-a\n")
        problems, _ = mt.check_tree(self.root)
        self.assertEqual(problems, [])

    def test_check_uninitialized(self):
        problems, _ = mt.check_tree(self.root)
        self.assertTrue(any("not been initialized" in p for p in problems))

    def test_add_machine_and_printer(self):
        mt.init_tree(self.root, out=quiet)
        rels = ["nc/{name}", "nc/_prove-out/{name}"]
        self.assertEqual(mt.add_dirs(self.root, "haas-vf2", rels, out=quiet), 2)
        self.assertTrue((self.root / "nc" / "_prove-out" / "haas-vf2").is_dir())
        self.assertEqual(mt.add_dirs(self.root, "haas-vf2", rels, out=quiet), 0)
        with self.assertRaises(mt.TreeError):
            mt.add_dirs(self.root, "Haas VF2", rels, out=quiet)
        with self.assertRaises(mt.TreeError):
            mt.add_dirs(self.root, "_prove-out", rels, out=quiet)

    def test_add_requires_initialized_tree(self):
        with self.assertRaises(mt.TreeError):
            mt.add_dirs(self.root, "haas-vf2", ["nc/{name}"], out=quiet)


if __name__ == "__main__":
    unittest.main()
