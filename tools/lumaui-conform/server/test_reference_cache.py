#!/usr/bin/env python3
"""Run with: python3 -m unittest tools/lumaui-conform/server/test_reference_cache.py"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


MODULE = Path(__file__).with_name("reference-cache.py")
spec = importlib.util.spec_from_file_location("reference_cache", MODULE)
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


class ReferenceCacheTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.design = self.root / "design"
        self.design.mkdir()
        (self.design / "index.html").write_text("<main>v70</main>")
        (self.design / "asset.svg").write_text("<svg/>")
        self.scenario = self.root / "scenario.json"
        self.data = {
            "app": "test", "spec": {"url": "index.html", "clock": "2026-09-25T12:00:00", "init_script": ["Math.random=()=>0.5"], "accent_hue": 258},
            "states": [{"name": "idle", "spec": [{"click": "#open"}]}, {"name": "menu", "spec": [{"click": "#menu"}]}],
            "phone_states": ["idle"], "gtk": {"build": "old"},
        }
        self.write_scenario()
        self.capture = self.root / "spec_capture.js"
        self.capture.write_text("// capture v1")
        self.loader = self.root / "playwright.js"
        self.loader.write_text("// loader v1")
        self.image = "sha256:" + "a" * 64
        self.store = self.root / "cache"
        self.output = self.root / "out"
        self.output.mkdir()

    def write_scenario(self):
        self.scenario.write_text(json.dumps(self.data))

    def identity(self, theme="dark", phone=False, image=None):
        return cache.prepare(self.design, self.scenario, self.capture, self.loader, image or self.image, theme, phone)

    def capture_output(self, identity):
        p = identity["provenance"]
        (self.output / "spec-meta.json").write_text(json.dumps({"theme": p["theme"], "phone": p["phone"], "states": [{"name": name} for name in p["states"]]}))
        for name in p["states"]:
            (self.output / f"spec-{name}.json").write_text(json.dumps({"state": name}))
            (self.output / f"spec-{name}.png").write_bytes(cache.PNG + b"image")

    def test_complete_hit_and_no_native_artifacts(self):
        identity = self.identity()
        self.capture_output(identity)
        (self.output / "gtk-idle.json").write_text("native output")
        cache.publish(self.store, identity, self.output)
        destination = self.root / "restored"
        self.assertTrue(cache.restore(self.store, identity, destination))
        self.assertEqual(set(p.name for p in destination.iterdir()), set(cache.filenames(identity)))
        self.assertEqual(len(identity["provenance"]["states"]), 2)

    def test_changed_reference_inputs_miss_but_gtk_change_hits(self):
        original = self.identity()
        self.capture_output(original)
        cache.publish(self.store, original, self.output)

        self.data["gtk"]["build"] = "new native source"
        self.write_scenario()
        self.assertEqual(self.identity()["key"], original["key"])
        for mutate in (
            lambda: (self.design / "asset.svg").write_text("<svg id='changed'/>") ,
            lambda: self.data["spec"].update(clock="2026-09-25T12:01:00"),
            lambda: self.data["spec"].update(init_script=["Math.random=()=>0.9"]),
            lambda: self.data["states"][0]["spec"].append({"key": "Enter"}),
            lambda: self.data["spec"].update(accent_hue=168),
            lambda: self.capture.write_text("// capture v2"),
            lambda: self.loader.write_text("// loader v2"),
        ):
            mutate()
            self.write_scenario()
            changed = self.identity()
            self.assertNotEqual(changed["key"], original["key"])
            self.assertFalse(cache.restore(self.store, changed, self.root / "miss"))
        self.assertNotEqual(self.identity(theme="light")["key"], original["key"])
        self.assertNotEqual(self.identity(phone=True)["key"], original["key"])
        self.assertNotEqual(self.identity(image="sha256:" + "b" * 64)["key"], original["key"])

    def test_linked_asset_target_changes_key(self):
        (self.design / "index-link.html").symlink_to("index.html")
        first = self.identity()["key"]
        (self.design / "index.html").write_text("<main>updated</main>")
        self.assertNotEqual(self.identity()["key"], first)

    def test_partial_and_corrupt_entries_miss(self):
        identity = self.identity()
        self.capture_output(identity)
        entry = cache.cache_entry(self.store, identity)
        # An interrupted publisher leaves only a hidden staging directory.
        stage = self.store / ".reference-interrupted"
        stage.mkdir(parents=True)
        (stage / "spec-meta.json").write_bytes((self.output / "spec-meta.json").read_bytes())
        self.assertFalse(cache.restore(self.store, identity, self.root / "interrupted"))
        entry.mkdir(parents=True)
        (entry / "spec-meta.json").write_bytes((self.output / "spec-meta.json").read_bytes())
        self.assertFalse(cache.restore(self.store, identity, self.root / "partial"))
        cache.publish(self.store, identity, self.output)
        self.assertTrue(cache.restore(self.store, identity, self.root / "complete"))
        (entry / "spec-idle.png").write_bytes(cache.PNG + b"tampered")
        self.assertFalse(cache.restore(self.store, identity, self.root / "corrupt"))

    def test_incomplete_capture_cannot_publish(self):
        identity = self.identity()
        self.capture_output(identity)
        (self.output / "spec-menu.png").unlink()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            cache.publish(self.store, identity, self.output)
        self.assertFalse(cache.restore(self.store, identity, self.root / "miss"))

    def test_empty_state_inventory_fails(self):
        self.data["states"] = []
        self.write_scenario()
        with self.assertRaisesRegex(ValueError, "no valid"):
            self.identity()

    def test_run_hit_still_captures_native_and_compares(self):
        self.data["states"] = self.data["states"][:1]
        self.write_scenario()
        identity = self.identity()
        self.capture_output(identity)
        cache.publish(self.store, identity, self.output)
        identity_file = self.root / "identity.json"
        identity_file.write_text(json.dumps(identity))

        harness = self.root / "harness"
        (harness / "server").mkdir(parents=True)
        (harness / "scenarios").mkdir()
        shutil.copy2(MODULE, harness / "server/reference-cache.py")
        shutil.copy2(MODULE.parent.parent / "run.sh", harness / "run.sh")
        shutil.copy2(self.scenario, harness / "scenarios/test.json")
        (harness / "gtk_capture.sh").write_text("#!/bin/sh\necho gtk >> \"$TRACE\"\n")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        node = bin_dir / "node"
        node.write_text("#!/bin/sh\ncase \"$1\" in *spec_capture.js) echo spec >> \"$TRACE\"; exit 17 ;; *compare.js) echo compare >> \"$TRACE\" ;; esac\n")
        node.chmod(0o755)
        trace = self.root / "trace"
        env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], TRACE=str(trace),
                   LUMAUI_CONFORM_HOST="local", LUMAUI_CONFORM_REPO=str(self.root),
                   LUMAUI_CONFORM_OUT=str(self.root / "reports"), LUMAUI_CONFORM_STAMP="stamp",
                   LUMAUI_CONFORM_REFERENCE_CACHE=str(self.store), LUMAUI_CONFORM_REFERENCE_IDENTITY=str(identity_file))
        result = subprocess.run(["sh", str(harness / "run.sh"), "test", "--theme", "dark"], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trace.read_text().splitlines(), ["gtk", "compare"])
        self.assertTrue((self.root / "reports/test/stamp-dark/spec-idle.png").is_file())


if __name__ == "__main__":
    unittest.main()
