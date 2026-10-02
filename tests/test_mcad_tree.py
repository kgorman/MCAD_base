#!/usr/bin/env python3
"""Tests for mcad_tree: sets up a store in a temp folder and checks it."""

import csv
import json
import sys
import tempfile
import zipfile
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

    def make_design(self, rel="Kenny's Hub/Bike/Headset Spacers", item_id="item_1", **info):
        design = self.root / rel
        (design / "wip").mkdir(parents=True)
        (design / "wip" / f"{design.name}.f3d").write_text("f3d")
        data = {"schema": 2, "fusion_item_id": item_id, "name": design.name, "kind": "design",
                "hub": "Kenny's Hub", "project": "Bike", "part_number": None,
                "wip": {"version_number": 3}}
        data.update(info)
        (design / "design.json").write_text(json.dumps(data))
        return design

    def make_revision(self, design, rev="rev-a", current=True, proven_on=("bambu-p1s",)):
        path = design / "released" / rev
        (path / "cad").mkdir(parents=True)
        (path / "cad" / "bm-0042.step").write_text("")
        for model in proven_on:
            (path / "build" / "fdm" / model).mkdir(parents=True)
            (path / "build" / "fdm" / model / f"bm-0042_{rev}.gcode").write_text("")
        (path / "manifest.json").write_text(json.dumps({
            "approval": {"reviewed_by": "reviewer@example.com", "approved_by": "owner@example.com"},
            "process": {"primary": "fdm", "proven_on": list(proven_on)}}))
        self.freeze(path)
        if current:
            (design / "released" / "CURRENT").write_text(rev + "\n")
        return path

    def freeze(self, rev):
        """Write SHA256SUMS over everything now in the revision, as the release tool will."""
        lines = [f"{mt.file_digest(rev / name)}  {name}\n" for name in sorted(mt.revision_files(rev))]
        (rev / "SHA256SUMS").write_text("".join(lines))

    def make_build(self, design, name="2026-09-27_p1s-01_b0173", nonconformance=None, **build):
        path = design / "builds" / name
        path.mkdir(parents=True)
        data = {"revision": "rev-a", "machine_model": "bambu-p1s",
                "result": "accepted", "quantity": {"built": 4, "accepted": 4},
                "inspection": {"inspected_by": "qc@example.com"}, "accepted_by": "qc@example.com"}
        data.update(build)
        (path / "build.json").write_text(json.dumps(data))
        if nonconformance is not None:
            (path / "nonconformance.json").write_text(json.dumps(nonconformance))
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
        self.make_build(design)
        (self.root / "Kenny's Hub" / "Bike" / ".DS_Store").write_text("")
        self.assertEqual(mt.check_tree(self.root), ([], []))

    def test_check_flags_two_folders_claiming_one_fusion_item(self):
        mt.init_tree(self.root, out=quiet)
        self.make_design("Hub/Bike/A", item_id="same")
        self.make_design("Hub/Bike/B", item_id="same")
        self.make_design("Hub/Bike/C", item_id=None)   # no Fusion id is fine: not every design is synced
        problems, _ = mt.check_tree(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("same Fusion item", problems[0])

    def test_a_design_made_by_hand_is_checked_and_indexed(self):
        mt.init_tree(self.root, out=quiet)
        design = self.root / "Brackets" / "Motor Mount"     # plain mkdir, no design.json, no Fusion
        (design / "wip").mkdir(parents=True)
        (design / "wip" / "motor-mount.sldprt").write_text("")
        self.assertEqual([d.name for d in mt.find_designs(self.root)], ["Motor Mount"])
        self.assertEqual(mt.check_tree(self.root), ([], []))

        rev = self.make_revision(design)
        (rev / "manifest.json").write_text("{}")             # released by hand, sign-offs forgotten
        problems, _ = mt.check_tree(self.root)
        self.assertEqual(len(problems), 2)
        self.assertIn("approval.reviewed_by: Brackets/Motor Mount/released/rev-a", problems[0])

        mt.build_index(self.root, out=quiet)
        with (self.root / "_index" / "parts.csv").open() as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual([(r["name"], r["path"], r["current_revision"]) for r in rows],
                         [("Motor Mount", "Brackets/Motor Mount", "rev-a")])

    def test_a_cloud_folder_named_wip_is_not_a_design(self):
        mt.init_tree(self.root, out=quiet)
        self.make_design("Hub/Bike/wip/Stem Cap", item_id="a")   # Fusion folder that happens to be called wip
        self.assertEqual([d.name for d in mt.find_designs(self.root)], ["Stem Cap"])

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

    def test_check_flags_revision_without_review_and_approval(self):
        mt.init_tree(self.root, out=quiet)
        design = self.make_design()
        rev = self.make_revision(design)
        (rev / "manifest.json").write_text(json.dumps({"approval": {"reviewed_by": "reviewer@example.com"}}))
        problems, _ = mt.check_tree(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("approval.approved_by", problems[0])

        (rev / "manifest.json").write_text(json.dumps({"approval": {
            "reviewed_by": "reviewer@example.com", "approved_by": "owner@example.com",
            "review_record": "reviews/design-review.md"}}))
        problems, _ = mt.check_tree(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("review record that is not there", problems[0])
        (rev / "reviews").mkdir()
        (rev / "reviews" / "design-review.md").write_text("")
        self.freeze(rev)
        self.assertEqual(mt.check_tree(self.root), ([], []))

    def test_check_flags_proven_model_without_a_program(self):
        mt.init_tree(self.root, out=quiet)
        design = self.make_design()
        rev = self.make_revision(design)
        (rev / "manifest.json").write_text(json.dumps({
            "approval": {"reviewed_by": "reviewer@example.com", "approved_by": "owner@example.com"},
            "process": {"primary": "fdm", "proven_on": ["bambu-p1s", "prusa-mk4", "haas-vf2"]}}))
        (rev / "cam" / "haas-vf2").mkdir(parents=True)   # subtractive programs count too
        (rev / "cam" / "haas-vf2" / "o0042_op10_rev-a.nc").write_text("")
        self.freeze(rev)
        problems, _ = mt.check_tree(self.root)
        text = "\n".join(problems)
        self.assertIn("proven on 'prusa-mk4' but the revision holds no G-code or NC program for it", text)
        self.assertEqual(len(problems), 1)

    def test_check_finds_missing_and_added_files(self):
        mt.init_tree(self.root, out=quiet)
        design = self.make_design()
        rev = self.make_revision(design)
        manifest = json.loads((rev / "manifest.json").read_text())
        manifest["files"] = {"cad/bm-0042.step": {}, "drawings/bm-0042_rev-a.pdf": {}}
        (rev / "manifest.json").write_text(json.dumps(manifest))
        (rev / "build" / "fdm" / "bambu-p1s" / "bm-0042_rev-a.gcode").unlink()
        (rev / "build" / "fdm" / "bambu-p1s" / "profile.json").write_text("{}")   # a profile is not a program
        problems, _ = mt.check_tree(self.root)
        text = "\n".join(problems)
        self.assertIn("manifest lists a file that is not there (drawings/bm-0042_rev-a.pdf)", text)
        self.assertIn("proven on 'bambu-p1s' but the revision holds no G-code or NC program for it", text)
        self.assertIn("SHA256SUMS lists a file that is not there (build/fdm/bambu-p1s/bm-0042_rev-a.gcode)", text)
        self.assertIn("file is not in SHA256SUMS (build/fdm/bambu-p1s/profile.json)", text)
        self.assertEqual(len(problems), 4)

    def test_check_wants_gcode_inside_a_3mf(self):
        mt.init_tree(self.root, out=quiet)
        rev = self.make_revision(self.make_design())
        folder = rev / "build" / "fdm" / "bambu-p1s"
        (folder / "bm-0042_rev-a.gcode").unlink()
        with zipfile.ZipFile(folder / "bm-0042_rev-a.3mf", "w") as zf:   # project saved before slicing
            zf.writestr("3D/3dmodel.model", "")
        self.freeze(rev)
        problems, _ = mt.check_tree(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("proven on 'bambu-p1s' but the revision holds no G-code or NC program for it", problems[0])

        with zipfile.ZipFile(folder / "bm-0042_rev-a.3mf", "a") as zf:   # the sliced file Bambu Studio exports
            zf.writestr("Metadata/plate_1.gcode", "")
        self.freeze(rev)
        self.assertEqual(mt.check_tree(self.root), ([], []))

    def test_check_requires_cad_in_a_design_revision(self):
        mt.init_tree(self.root, out=quiet)
        for name, item_id, kind in (("Spacer", "a", "design"), ("Spacer Drawing", "b", "drawing")):
            rev = self.make_revision(self.make_design(f"Hub/Bike/{name}", item_id=item_id, kind=kind))
            (rev / "cad" / "bm-0042.step").unlink()
            self.freeze(rev)
        problems, _ = mt.check_tree(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("no CAD file under cad/: Hub/Bike/Spacer/released/rev-a", problems[0])

    def test_verify_catches_a_file_changed_after_release(self):
        mt.init_tree(self.root, out=quiet)
        rev = self.make_revision(self.make_design())
        (rev / "cad" / "bm-0042.step").write_text("edited after release")
        self.assertEqual(mt.check_tree(self.root), ([], []))          # check hashes nothing
        problems, _ = mt.check_tree(self.root, hashes=True)
        self.assertEqual(len(problems), 1)
        self.assertIn("does not match its checksum (cad/bm-0042.step)", problems[0])

    def test_check_flags_builds_that_do_not_say_what_ran(self):
        mt.init_tree(self.root, out=quiet)
        design = self.make_design()
        self.make_revision(design)
        self.make_build(design, "2026-09-27_no-revision", revision=None)
        self.make_build(design, "2026-09-28_wrong-revision", revision="rev-z")
        self.make_build(design, "2026-09-29_no-model", machine_model=None)
        problems, _ = mt.check_tree(self.root)
        text = "\n".join(problems)
        self.assertIn("does not name the revision it ran", text)
        self.assertIn("names 'rev-z', which is not a revision", text)
        self.assertIn("does not name the machine model", text)
        self.assertEqual(len(problems), 3)

    def test_check_treats_first_build_on_an_unproven_model_as_first_article(self):
        mt.init_tree(self.root, out=quiet)
        design = self.make_design()
        self.make_revision(design)
        self.make_build(design, "2026-10-01_mk4-01", machine_model="prusa-mk4", result="pending")
        problems, notes = mt.check_tree(self.root)
        self.assertEqual(problems, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("first-article inspection, rev-a is not proven on prusa-mk4", notes[0])

        # Accepted with no inspection record: the model is not proven by a signature alone.
        unsigned = self.make_build(design, "2026-10-02_mk4-01", machine_model="prusa-mk4")
        problems, _ = mt.check_tree(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("first build of rev-a on prusa-mk4 was accepted without an inspection record", problems[0])

        # With the record it passes, and later builds on that model need nothing extra.
        (unsigned / "inspection.pdf").write_text("")
        data = json.loads((unsigned / "build.json").read_text())
        data["inspection"]["record"] = "inspection.pdf"
        (unsigned / "build.json").write_text(json.dumps(data))
        self.make_build(design, "2026-10-03_mk4-02", machine_model="prusa-mk4")
        problems, notes = mt.check_tree(self.root)
        self.assertEqual(problems, [])
        self.assertEqual(len(notes), 1)   # only the earlier pending build

    def test_check_flags_builds_without_acceptance(self):
        mt.init_tree(self.root, out=quiet)
        design = self.make_design()
        self.make_revision(design)
        (design / "builds" / "2026-09-27_no-record").mkdir(parents=True)
        self.make_build(design, "2026-09-28_bad-result", result="ok")
        self.make_build(design, "2026-09-29_unsigned", inspection={"record": "inspection.pdf"}, accepted_by=None)
        self.make_build(design, "2026-09-30_pending", result="pending")
        problems, notes = mt.check_tree(self.root)
        text = "\n".join(problems)
        self.assertIn("build has no build.json", text)
        self.assertIn("build result is 'ok'", text)
        self.assertIn("no inspection.inspected_by", text)
        self.assertIn("inspection record that is not there", text)
        self.assertIn("no accepted_by", text)
        self.assertEqual(len(problems), 5)
        self.assertEqual(len(notes), 1)
        self.assertIn("awaiting inspection", notes[0])

    def test_check_requires_a_disposition_for_rejected_parts(self):
        mt.init_tree(self.root, out=quiet)
        design = self.make_design()
        self.make_revision(design)
        self.make_build(design, "2026-09-27_failed", result="rejected")
        self.make_build(design, "2026-09-28_two-of-four", quantity={"built": 4, "accepted": 2},
                        nonconformance={"description": "Layer shift at 14 mm", "disposition": "bin"})
        problems, _ = mt.check_tree(self.root)
        text = "\n".join(problems)
        self.assertIn("rejected parts but no nonconformance.json", text)
        self.assertIn("disposition is 'bin'", text)
        self.assertIn("no decided_by", text)
        self.assertEqual(len(problems), 3)

        self.make_build(design, "2026-09-29_scrapped", result="rejected", nonconformance={
            "description": "Warped off the bed", "disposition": "scrap", "decided_by": "qc@example.com"})
        problems, _ = mt.check_tree(self.root)
        self.assertEqual(len(problems), 3)  # the complete record adds none

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
