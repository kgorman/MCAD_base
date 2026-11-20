#!/usr/bin/env python3
"""Tests for mcad_release and the release commands in mcad_tree."""

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mcad_release as mr  # noqa: E402
import mcad_tree as mt  # noqa: E402


def quiet(_line):
    pass


class ReleaseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "store"
        self.root.mkdir()
        mt.init_tree(self.root, out=quiet)
        self.sources = Path(self.tmp.name) / "sources"
        self.sources.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def make_design(self, rel="Hub/Bike/Headset Spacers", **info):
        design = self.root / rel
        for name in ("wip", "released", "jobs"):
            (design / name).mkdir(parents=True)
        if info:
            data = {"schema": 2, "name": design.name, "kind": "design"}
            data.update(info)
            (design / "design.json").write_text(json.dumps(data))
        return design

    def source(self, name, content=b"bytes"):
        path = self.sources / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def cad_spec(self, name="bracket.step"):
        return mr.FileSpec(src=str(self.source(name)), dest=f"cad/{name}")

    def approval(self):
        return mr.Approval(reviewed_by="alex", approved_by="jamie")

    def release(self, design_rel, files=None, **kw):
        kw.setdefault("part_number", "bm-0042")
        kw.setdefault("reason", "first")
        kw.setdefault("released_by", "alex")
        kw.setdefault("approval", self.approval())
        return mr.create_release(self.root, design_rel, files or [self.cad_spec()], **kw)

    def problems(self):
        problems, _notes = mt.check_tree(self.root)
        return problems


class CreateReleaseTest(ReleaseTestCase):
    def test_happy_path_passes_check(self):
        design = self.make_design()
        result = self.release("Hub/Bike/Headset Spacers")
        self.assertEqual(result["revision"], "rev-a")
        self.assertTrue((design / "released" / "rev-a" / "SHA256SUMS").exists())
        self.assertEqual((design / "released" / "CURRENT").read_text(), "rev-a")
        self.assertEqual(self.problems(), [])
        manifest = json.loads((design / "released" / "rev-a" / "manifest.json").read_text())
        self.assertEqual(manifest["approval"], {"reviewed_by": "alex", "approved_by": "jamie"})
        self.assertEqual(manifest["files"], {"cad/bracket.step": {}})
        history = [json.loads(l) for l in (design / "history.jsonl").read_text().splitlines()]
        self.assertEqual([h["event"] for h in history], ["released"])

    def test_revision_is_read_only_after_release(self):
        design = self.make_design()
        self.release("Hub/Bike/Headset Spacers")
        cad = design / "released" / "rev-a" / "cad" / "bracket.step"
        self.assertFalse(cad.stat().st_mode & stat.S_IWUSR)

    def test_second_release_obsoletes_the_first(self):
        design = self.make_design()
        self.release("Hub/Bike/Headset Spacers")
        result = self.release("Hub/Bike/Headset Spacers", reason="second")
        self.assertEqual(result["revision"], "rev-b")
        self.assertEqual(result["obsoleted"], "rev-a")
        self.assertTrue((design / "released" / "rev-a" / "OBSOLETE").exists())
        self.assertEqual((design / "released" / "CURRENT").read_text(), "rev-b")
        self.assertEqual(self.problems(), [])
        history = [json.loads(l)["event"] for l in (design / "history.jsonl").read_text().splitlines()]
        self.assertEqual(history, ["released", "released", "obsoleted"])

    def test_frozen_revision_is_refused(self):
        design = self.make_design()
        self.release("Hub/Bike/Headset Spacers", revision="rev-a")
        with self.assertRaises(mr.RevisionFrozenError) as ctx:
            self.release("Hub/Bike/Headset Spacers", revision="rev-a", reason="again")
        self.assertEqual(ctx.exception.code, "revision_frozen")
        self.assertNotIn("again", (design / "released" / "rev-a" / "manifest.json").read_text())

    def test_no_cad_file_is_refused(self):
        self.make_design()
        spec = mr.FileSpec(src=str(self.source("readme.txt")), dest="docs/readme.txt")
        with self.assertRaises(mr.ReleaseStructureError):
            self.release("Hub/Bike/Headset Spacers", [spec])

    def test_proven_on_needs_a_program(self):
        self.make_design()
        with self.assertRaises(mr.ReleaseStructureError):
            self.release("Hub/Bike/Headset Spacers", process=mr.Process(proven_on=["haas-vf2"]))
        nc = mr.FileSpec(src=str(self.source("o0001.nc", b"(nc)")), dest="cam/haas-vf2/o0001.nc")
        result = self.release("Hub/Bike/Headset Spacers", [self.cad_spec(), nc],
                              process=mr.Process(proven_on=["haas-vf2"]))
        self.assertEqual(result["revision"], "rev-a")
        self.assertEqual(self.problems(), [])

    def test_bad_destinations_are_refused(self):
        self.make_design()
        for dest, error in (("cad/Bracket.STEP", mr.NamingRuleError), ("cad/has space.step", mr.NamingRuleError),
                            ("../escape.step", mr.PathTraversalError), ("/abs/x.step", mr.PathTraversalError)):
            with self.assertRaises(error, msg=dest):
                self.release("Hub/Bike/Headset Spacers",
                             [mr.FileSpec(src=str(self.source("bracket.step")), dest=dest)])

    def test_design_traversal_is_refused(self):
        with self.assertRaises(mr.PathTraversalError):
            self.release("../../etc")

    def test_missing_design_and_source(self):
        with self.assertRaises(mr.NotFoundError):
            self.release("Hub/Nope")
        self.make_design()
        with self.assertRaises(mr.NotFoundError):
            self.release("Hub/Bike/Headset Spacers",
                         [mr.FileSpec(src=str(self.sources / "nope.step"), dest="cad/nope.step")])

    def test_required_fields(self):
        self.make_design()
        with self.assertRaises(mr.InvalidInputError):
            self.release("Hub/Bike/Headset Spacers", part_number="")
        with self.assertRaises(mr.InvalidInputError):
            self.release("Hub/Bike/Headset Spacers", approval=mr.Approval(reviewed_by="", approved_by="x"))
        with self.assertRaises(mr.InvalidInputError):
            mr.create_release(self.root, "Hub/Bike/Headset Spacers", [], "bm-0042", "r", "alex", self.approval())

    def test_dicts_are_accepted_for_inputs(self):
        self.make_design()
        result = mr.create_release(
            self.root, "Hub/Bike/Headset Spacers",
            [{"src": str(self.source("bracket.step")), "dest": "cad/bracket.step", "metadata": {"tool": "x"}}],
            "bm-0042", "first", "alex", {"reviewed_by": "alex", "approved_by": "alex"},
            process={"primary": "cnc"})
        self.assertEqual(result["manifest"]["files"]["cad/bracket.step"], {"tool": "x"})
        self.assertEqual(result["manifest"]["process"], {"primary": "cnc"})

    def test_dry_run_writes_nothing(self):
        design = self.make_design()
        result = self.release("Hub/Bike/Headset Spacers", dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["revision"], "rev-a")
        self.assertEqual(result["manifest"]["part_number"], "bm-0042")
        self.assertFalse((design / "released" / "rev-a").exists())
        self.assertFalse((design / "history.jsonl").exists())

    def test_resumes_a_crashed_attempt(self):
        design = self.make_design()
        stray = design / "released" / "rev-a"
        stray.mkdir()
        (stray / "leftover.txt").write_text("from a crash")
        result = self.release("Hub/Bike/Headset Spacers", revision="rev-a")
        self.assertTrue(result["resumed_incomplete_revision"])
        self.assertFalse((stray / "leftover.txt").exists())
        self.assertEqual(self.problems(), [])

    def test_lock_refuses_a_second_release(self):
        design = self.make_design()
        lock = design / "released" / mr.LOCK_FILENAME
        lock.write_text('{"pid": 1, "operation": "release", "revision": "rev-a"}')
        with self.assertRaises(mr.ReleaseInProgressError) as ctx:
            self.release("Hub/Bike/Headset Spacers")
        self.assertEqual(ctx.exception.details["lock"]["pid"], 1)
        self.assertIn("Headset Spacers", str(mr.release_status(self.root, "Hub/Bike/Headset Spacers")))
        self.assertTrue(mr.release_status(self.root, "Hub/Bike/Headset Spacers")["locked"])
        with self.assertRaises(mr.InvalidInputError):
            mr.clear_release_lock(self.root, "Hub/Bike/Headset Spacers")
        cleared = mr.clear_release_lock(self.root, "Hub/Bike/Headset Spacers", force=True)
        self.assertTrue(cleared["cleared"])
        self.assertEqual(self.release("Hub/Bike/Headset Spacers")["revision"], "rev-a")
        self.assertFalse(lock.exists())

    def test_lock_is_released_after_failure(self):
        design = self.make_design()
        # a src that vanishes between planning and writing: simulate with an unreadable src
        src = self.source("bracket.step")
        spec = mr.FileSpec(src=str(src), dest="cad/bracket.step")
        plan = mr.plan_release(self.root, "Hub/Bike/Headset Spacers", [spec], "bm-0042", "x", "alex",
                               self.approval())
        src.unlink()
        with self.assertRaises(mr.ReleaseError):
            mr.write_release(self.root, plan)
        self.assertFalse((design / "released" / mr.LOCK_FILENAME).exists())
        self.assertEqual(mr.release_status(self.root, "Hub/Bike/Headset Spacers")["incomplete_revisions"], ["rev-a"])

    @unittest.skipIf(os.geteuid() == 0, "root bypasses permission bits")
    def test_os_permission_denied_is_its_own_error(self):
        design = self.make_design()
        released = design / "released"
        mode = released.stat().st_mode
        released.chmod(stat.S_IREAD | stat.S_IEXEC)
        try:
            with self.assertRaises(mr.OSPermissionError) as ctx:
                self.release("Hub/Bike/Headset Spacers")
            self.assertEqual(ctx.exception.code, "os_permission_denied")
        finally:
            released.chmod(mode)

    def test_outbox_gets_machine_files(self):
        self.make_design()
        mt.add_machine(self.root, "haas-vf2", out=quiet)
        nc = mr.FileSpec(src=str(self.source("o0001.nc", b"(nc)")), dest="cam/haas-vf2/o0001_rev-a.nc")
        gcode = mr.FileSpec(src=str(self.source("x.gcode", b";g")), dest="build/fdm/bambu-p1s/x_rev-a.gcode")
        result = self.release("Hub/Bike/Headset Spacers", [self.cad_spec(), nc, gcode])
        self.assertEqual(result["outbox_updated"], ["haas-vf2/o0001_rev-a.nc"])
        self.assertTrue((self.root / "_outbox" / "haas-vf2" / "o0001_rev-a.nc").exists())
        self.assertEqual(self.problems(), [])

    def test_markforged_print_file_counts_as_a_program(self):
        self.make_design()
        mfp = mr.FileSpec(src=str(self.source("x.mfp", b"mfp")), dest="build/fff/markforged-mark-two/x_rev-a.mfp")
        result = self.release("Hub/Bike/Headset Spacers", [self.cad_spec(), mfp],
                              process=mr.Process(proven_on=["markforged-mark-two"]))
        self.assertEqual(result["manifest"]["process"]["proven_on"], ["markforged-mark-two"])
        self.assertEqual(self.problems(), [])

    def test_abandon_release(self):
        design = self.make_design()
        stray = design / "released" / "rev-a"
        stray.mkdir()
        (stray / "x").write_text("")
        with self.assertRaises(mr.NotFoundError):
            mr.abandon_release(self.root, "Hub/Bike/Headset Spacers", "rev-b")
        self.assertEqual(mr.abandon_release(self.root, "Hub/Bike/Headset Spacers", "rev-a")["abandoned"], "rev-a")
        self.assertFalse(stray.exists())
        self.release("Hub/Bike/Headset Spacers", revision="rev-a")
        with self.assertRaises(mr.RevisionFrozenError):
            mr.abandon_release(self.root, "Hub/Bike/Headset Spacers", "rev-a")

    def test_next_revision_names(self):
        self.assertEqual(mr.next_revision_name(self.root / "nowhere"), "rev-a")
        released = self.root / "released"
        for name in ["rev-" + chr(c) for c in range(ord("a"), ord("z") + 1)]:
            (released / name).mkdir(parents=True)
        self.assertEqual(mr.next_revision_name(released), "rev-aa")

    def test_error_payload(self):
        payload = mr.RevisionFrozenError("rev-a is frozen", revision="rev-a").to_payload()
        self.assertEqual(payload, {"ok": False, "code": "revision_frozen", "message": "rev-a is frozen",
                                   "details": {"revision": "rev-a"}})


class ReleaseFromWipTest(ReleaseTestCase):
    def make_synced_design(self):
        design = self.make_design(fusion_item_id="urn:item", hub="Hub", project="Bike",
                                  part_number="bm-0042", description="Spacer, headset",
                                  wip={"version_id": "urn:v?version=14", "version_number": 14})
        (design / "wip" / "Headset Spacers.f3d").write_bytes(b"f3d")
        (design / "wip" / "Headset Spacers.step").write_bytes(b"step")
        (design / "wip" / "Headset Spacers v3.stl").write_bytes(b"stl")
        (design / "wip" / "_versions").mkdir()
        (design / "wip" / "_versions" / "Headset Spacers v2.f3d").write_bytes(b"old")
        return design

    def test_generator_src_folder_keeps_its_path(self):
        self.assertEqual(mr.place_wip_file("src/Part.py"), "src/part.py")
        self.assertEqual(mr.place_wip_file("src/params.json"), "src/params.json")

    def test_wip_is_routed_renamed_and_sourced(self):
        design = self.make_synced_design()
        result = mr.release_from_wip(self.root, "Hub/Bike/Headset Spacers", "first", "alex", self.approval())
        self.assertEqual(result["files"], ["cad/headset-spacers.f3d", "cad/headset-spacers.step",
                                           "mesh/headset-spacers-v3.stl"])
        manifest = json.loads((design / "released" / "rev-a" / "manifest.json").read_text())
        self.assertEqual(manifest["part_number"], "bm-0042")
        self.assertEqual(manifest["description"], "Spacer, headset")
        self.assertEqual(manifest["source"]["fusion_version_id"], "urn:v?version=14")
        self.assertEqual(manifest["files"]["cad/headset-spacers.f3d"], {"format": "f3d"})
        self.assertNotIn("process", manifest)
        self.assertEqual(self.problems(), [])

    def test_structured_wip_keeps_its_place_and_proves_models(self):
        design = self.make_synced_design()
        gcode = design / "wip" / "build" / "fdm" / "bambu-p1s"
        gcode.mkdir(parents=True)
        (gcode / "Headset Spacers.gcode").write_bytes(b";FLAVOR")
        result = mr.release_from_wip(self.root, "Hub/Bike/Headset Spacers", "first", "alex", self.approval(),
                                     process=mr.Process(primary="fdm", material="PETG"))
        self.assertIn("build/fdm/bambu-p1s/headset-spacers.gcode", result["files"])
        self.assertEqual(result["manifest"]["process"],
                         {"primary": "fdm", "material": "PETG", "proven_on": ["bambu-p1s"]})
        self.assertEqual(self.problems(), [])

    def test_unplaceable_file_stops_unless_skipped(self):
        design = self.make_synced_design()
        (design / "wip" / "notes.txt").write_text("x")
        (design / "wip" / "loose.gcode").write_text(";g")
        with self.assertRaises(mr.InvalidInputError) as ctx:
            mr.release_from_wip(self.root, "Hub/Bike/Headset Spacers", "first", "alex", self.approval())
        self.assertEqual(ctx.exception.details["unplaced"], ["loose.gcode", "notes.txt"])
        result = mr.release_from_wip(self.root, "Hub/Bike/Headset Spacers", "first", "alex", self.approval(),
                                     skip_unknown=True)
        self.assertEqual(result["skipped"], ["loose.gcode", "notes.txt"])
        self.assertEqual(self.problems(), [])

    def test_extra_file_can_place_a_program(self):
        design = self.make_synced_design()
        nc = self.source("o0042.nc", b"(nc)")
        result = mr.release_from_wip(self.root, "Hub/Bike/Headset Spacers", "first", "alex", self.approval(),
                                     extra_files=[mr.FileSpec(src=str(nc), dest="cam/haas-vf2/o0042_rev-a.nc")])
        self.assertEqual(result["manifest"]["process"]["proven_on"], ["haas-vf2"])
        self.assertEqual(self.problems(), [])

    def test_part_number_is_required_somewhere(self):
        design = self.make_design()
        (design / "wip" / "x.step").write_bytes(b"s")
        with self.assertRaises(mr.InvalidInputError):
            mr.release_from_wip(self.root, "Hub/Bike/Headset Spacers", "first", "alex", self.approval())
        result = mr.release_from_wip(self.root, "Hub/Bike/Headset Spacers", "first", "alex", self.approval(),
                                     part_number="bm-1")
        self.assertEqual(result["manifest"]["part_number"], "bm-1")

    def test_empty_wip(self):
        self.make_design()
        with self.assertRaises(mr.InvalidInputError):
            mr.release_from_wip(self.root, "Hub/Bike/Headset Spacers", "first", "alex", self.approval(),
                                part_number="bm-1")

    def test_slug(self):
        self.assertEqual(mr.slug("Headset Spacers v3.STL"), "headset-spacers-v3.stl")
        self.assertEqual(mr.slug("o0042_op10 (rev A).nc"), "o0042_op10-rev-a.nc")
        self.assertEqual(mr.slug("README"), "readme")
        self.assertEqual(mr.slug("..weird.."), "weird")


class CliTest(ReleaseTestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                mt.main(list(argv))
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def test_release_from_wip_by_command(self):
        design = self.make_design(part_number="bm-0042")
        (design / "wip" / "Bracket.step").write_bytes(b"s")
        code, out, _ = self.run_cli("release", str(self.root), "Hub/Bike/Headset Spacers",
                                    "--by", "alex@example.com", "--reason", "first", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("Would release rev-a", out)
        self.assertIn("Nothing was written", out)
        self.assertFalse((design / "released" / "rev-a").exists())

        code, out, _ = self.run_cli("release", str(self.root), "Hub/Bike/Headset Spacers",
                                    "--by", "alex@example.com", "--reason", "first", "--json")
        self.assertEqual(code, 0, out)
        result = json.loads(out)
        self.assertEqual(result["revision"], "rev-a")
        self.assertEqual(result["manifest"]["approval"],
                         {"reviewed_by": "alex@example.com", "approved_by": "alex@example.com"})
        self.assertEqual(self.problems(), [])

        code, out, err = self.run_cli("release", str(self.root), "Hub/Bike/Headset Spacers",
                                      "--by", "alex", "--reason", "again", "--revision", "rev-a")
        self.assertEqual(code, 2)
        self.assertIn("error (revision_frozen)", err)

    def test_release_error_as_json(self):
        code, out, _ = self.run_cli("release", str(self.root), "Hub/Nope", "--by", "a", "--reason", "r", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["code"], "not_found")

    def test_no_wip_with_files(self):
        self.make_design()
        cad = self.source("bracket.step")
        nc = self.source("o1.nc", b"(nc)")
        code, out, _ = self.run_cli("release", str(self.root), "Hub/Bike/Headset Spacers", "--no-wip",
                                    "--file", f"{cad}=cad/bracket.step", "--file", f"{nc}=cam/haas-vf2/o1.nc",
                                    "--part-number", "bm-1", "--by", "alex", "--reason", "r",
                                    "--reviewed-by", "bo", "--approved-by", "cy", "--json")
        self.assertEqual(code, 0, out)
        result = json.loads(out)
        self.assertEqual(result["manifest"]["approval"], {"reviewed_by": "bo", "approved_by": "cy"})
        self.assertEqual(result["files"], ["cad/bracket.step", "cam/haas-vf2/o1.nc"])
        self.assertEqual(self.problems(), [])

    def test_status_and_abandon_commands(self):
        design = self.make_design()
        (design / "released" / "rev-a").mkdir()
        code, out, _ = self.run_cli("release-status", str(self.root), "Hub/Bike/Headset Spacers")
        self.assertEqual(code, 0)
        self.assertIn("incomplete revisions: rev-a", out)
        code, out, _ = self.run_cli("abandon-release", str(self.root), "Hub/Bike/Headset Spacers", "rev-a", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["abandoned"], "rev-a")
        self.assertFalse((design / "released" / "rev-a").exists())


if __name__ == "__main__":
    unittest.main()
