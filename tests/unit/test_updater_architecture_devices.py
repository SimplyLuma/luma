# SPDX-License-Identifier: Apache-2.0
"""Execute shared graph routing and rendering without keys or publication."""
from pathlib import Path
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "scripts/os/lib"
sys.path.insert(0, str(LIB))
from desktop_paths import manifest_architecture
from devices import namespace, ref

class UpdateTargets(unittest.TestCase):
    def route(self, *args, arch="x86_64"):
        script = ROOT / "scripts/os/sign-update-graph.sh"
        source = script.read_text().split("luma_os_require_tools curl python3 install", 1)[0]
        source = source.replace('. "$(dirname -- "$0")/lib/common.sh"', r'''
LUMA_OS_ROOT=/fixture/os
LUMA_OS_SECRETS=/fixture/secrets
LUMA_OS_MAJOR=1
LUMA_OS_CHANNELS="nightly beta stable"
LUMA_OS_ARCH=''' + arch + r'''
luma_os_die() { printf '%s\n' "$*" >&2; exit 1; }
luma_os_select_architecture() { case "$1" in x86_64|aarch64) LUMA_OS_ARCH=$1;; *) luma_os_die bad-arch;; esac; }
luma_os_channel_ref() { case "$1" in nightly|beta|stable) printf 'luma/1/%s/%s\n' "$LUMA_OS_ARCH" "$1";; *) luma_os_die bad-channel;; esac; }
''')
        source += '\nprintf "%s\\n" "$LUMA_OS_ARCH" "$ref" "$graph_dir" "${target_args[*]}" "${graph_dirs[*]}"\n'
        return subprocess.run(["bash", "-c", source, str(script), *args], capture_output=True, text=True, timeout=5)

    def test_desktop_arch_paths_and_x86_legacy_alias(self):
        for arch in ("x86_64", "aarch64"):
            result = self.route("--channel", "nightly", arch=arch)
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = result.stdout.splitlines()
            self.assertEqual(rows[:4], [arch, f"luma/1/{arch}/nightly", f"/fixture/os/publish/graph/{arch}", ""])
            expected = f"/fixture/os/publish/graph/{arch}" + (" /fixture/os/publish/graph" if arch == "x86_64" else "")
            self.assertEqual(rows[4], expected)

    def test_phone_namespaces_are_disjoint_from_desktop(self):
        with tempfile.NamedTemporaryFile() as graph:
            for target, slug in (("fairphone,fp6", "fairphone-fp6"), ("unihertz,titan2e", "unihertz-titan2e")):
                result = self.route("--channel", "beta", "--hardware-target", target, "--source", graph.name)
                self.assertEqual(result.returncode, 0, result.stderr)
                rows = result.stdout.splitlines()
                self.assertEqual(rows[:4], ["aarch64", f"luma/1/aarch64/{slug}/beta", f"/fixture/os/publish/graph/aarch64/{slug}", f"--hardware-target {target}"])
                self.assertEqual(rows[4], rows[2])

    def test_unknown_target_explicit_wrong_arch_and_missing_phone_source_refuse(self):
        for args in (("--channel", "nightly", "--hardware-target", "../bad"),
                     ("--channel", "nightly", "--hardware-target", "fairphone,fp6", "--arch", "x86_64"),
                     ("--channel", "nightly", "--hardware-target", "unihertz,titan2e"),
                     ("--channel", "unknown")):
            self.assertNotEqual(self.route(*args).returncode, 0, args)

    def test_native_signing_boundary_is_unchanged(self):
        path = "scripts/os/sign-update-graph.sh"
        original = subprocess.check_output(["git", "show", "94f9e9827c2b17e23738c4b442ac42c53c979951:" + path], cwd=ROOT, text=True)
        actual = (ROOT / path).read_text()
        def boundary(text):
            return text.split("luma_os_graph_tools() {", 1)[1].split("\n}\n", 1)[0]
        self.assertEqual(boundary(actual), boundary(original))
        self.assertIn('flock -w 60 8', actual)
        self.assertIn('previous graph signature rejected; nothing replaced', actual)
        self.assertIn('cmp -s "$previous.minisig" "$dir/$channel.json.minisig"', actual)

    def test_rendered_phone_graphs_do_not_offer_desktop_or_other_phone(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            for number, target in enumerate(("", "fairphone,fp6", "unihertz,titan2e"), 1):
                commit = str(number) * 64
                root = repo / "luma" / (namespace(target) if target else "releases/aarch64")
                if target:
                    root /= "releases"
                directory = root / "1.0.0-nightly.20261024.1"
                directory.mkdir(parents=True)
                manifest = {"version": directory.name, "channel": "nightly", "architecture": "aarch64", "ref": ref(target, "1", "nightly") if target else "luma/1/aarch64/nightly", "commit": commit, "published_utc": "2026-10-24T00:00:00Z", "build_id": "20261024.1"}
                if target:
                    manifest["hardware_target"] = target
                (directory / "manifest.json").write_text(json.dumps(manifest))
                (directory / "notes.json").write_text(json.dumps({"summary": "General bug fixes."}))
                obj = repo / "objects" / commit[:2] / (commit[2:] + ".commit")
                obj.parent.mkdir(parents=True)
                obj.write_text("fixture only: commit presence, not signature trust")
            for number, target in enumerate(("", "fairphone,fp6", "unihertz,titan2e"), 1):
                argv = [sys.executable, str(LIB / "graph_from_releases.py"), str(repo), "nightly", "aarch64"]
                if target:
                    argv += ["--hardware-target", target]
                run = subprocess.run(argv, capture_output=True, text=True, check=True, timeout=5)
                graph = json.loads(run.stdout)
                self.assertEqual([r["commit"] for r in graph["releases"]], [str(number) * 64])
                self.assertEqual(graph.get("hardware_target", ""), target)
                self.assertEqual(graph["schema_version"], 2 if target else 1)
                if target:
                    self.assertIn(namespace(target).removeprefix("targets/") + "/nightly/notes/", graph["releases"][0]["notes_url"])

    def test_client_and_publisher_device_tables_have_identical_buckets(self):
        from devices import load
        from luma_update.hardware import DEVICES
        producer = load()
        self.assertEqual(set(DEVICES), set(producer))
        for name, (board, arch, slug) in DEVICES.items():
            self.assertTrue(board)
            self.assertEqual((arch, slug), (producer[name]["arch"], producer[name]["slug"]))

    def test_publisher_required_label_inputs_exist(self):
        from devices import load
        for row in load().values():
            if row["required_labels"] != "-":
                path = ROOT / row["required_labels"]
                self.assertTrue(path.is_file(), path)
                self.assertTrue(any(line and not line.startswith("#") for line in path.read_text().splitlines()))

    def test_shared_updater_package_pins_are_consistent(self):
        import re
        spec = (ROOT / "packaging/rpm/luma-update.spec").read_text()
        version = re.search(r"^Version:\s+(\S+)", spec, re.M)[1]
        release = re.search(r"^Release:\s+(\S+)", spec, re.M)[1].removesuffix("%{?dist}")
        inputs = dict(line.split("=", 1) for line in (ROOT / "config/desktop/inputs.env").read_text().splitlines() if line.startswith("LUMA_UPDATE_"))
        self.assertEqual(inputs["LUMA_UPDATE_VERSION"], version)
        self.assertEqual(inputs["LUMA_UPDATE_RELEASE"], release)
        nevra = f"luma-update-{version}-{release}.fc44.noarch"
        self.assertEqual(inputs["LUMA_UPDATE_NEVRA"], nevra)
        self.assertIn(nevra, (ROOT / "config/desktop/packages.txt").read_text().splitlines())
        self.assertGreater(int(release.rsplit(".", 1)[1]), 22)

    def test_signed_manifest_cannot_lie_about_phone_ref(self):
        doc = {"architecture": "aarch64", "hardware_target": "unihertz,titan2e", "channel": "nightly", "ref": "luma/1/aarch64/fairphone-fp6/nightly"}
        with self.assertRaises(ValueError):
            manifest_architecture(doc)
        doc["ref"] = "luma/1/aarch64/unihertz-titan2e/nightly"
        self.assertEqual(manifest_architecture(doc), "aarch64")
