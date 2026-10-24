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


# Compose integration needs the full repository and stays outside the standalone RPM.
sys.path.insert(0, str(ROOT / "src/luma-update/tests"))
import fakes
from fakes import graph_doc
from luma_update.config import Settings
from luma_update.engine import Engine
from luma_update.hardware import FP6, TITAN2E


class ComposedUpdatePolicies(unittest.TestCase):
    def test_actual_desktop_compose_and_phone_policies_fetch_separate_graphs(self):
        import subprocess
        import sys
        from luma_update.config import load_settings
        root = ROOT
        cases = (("x86_64", "", "", b""),
                 ("aarch64", "", "", b""),
                 ("aarch64", FP6, "config/mobile/fp6-physical/overlay/etc/luma/update.conf", b"fairphone,fp6\0qcom,milos\0"),
                 ("aarch64", TITAN2E, "src/luma-titan2e-platform/luma-update.conf", b"unihertz,titan2-elite\0mediatek,mt6878\0"))
        for arch, target, policy, compatible in cases:
            with self.subTest(arch=arch, target=target):
                rig = fakes.Rig()
                self.addCleanup(rig.close)
                rig.paths.vendor_config.parent.mkdir(parents=True, exist_ok=True)
                rig.paths.vendor_config.write_bytes((root / "src/luma-update/data/config/update.conf").read_bytes())
                if policy:
                    rig.paths.admin_config.parent.mkdir(parents=True, exist_ok=True)
                    rig.paths.admin_config.write_bytes((root / policy).read_bytes())
                    dt = rig.paths.device_tree_compatible
                    dt.parent.mkdir(parents=True, exist_ok=True)
                    dt.write_bytes(compatible)
                else:
                    # Execute the maintained compose block, redirecting ONLY its
                    # absolute output into this private fixture root.
                    text = (root / "image/luma-desktop/scripts/configure-system.sh").read_text()
                    body = text.split("<<'PYGRAPH'\n", 1)[1].split("\nPYGRAPH", 1)[0]
                    body = body.replace("Path('/usr/lib/luma/update.conf')", "Path(" + repr(str(rig.paths.vendor_config)) + ")")
                    profile = "arm-preview" if arch == "aarch64" else "full"
                    subprocess.run([sys.executable, "-", str(root), arch, profile], input=body, text=True, check=True, timeout=10)
                rig.settings = load_settings(rig.paths)
                self.assertEqual(rig.settings.hardware_target, target)
                expected = "https://dl.simplyluma.com/os/graph/" + arch
                if target:
                    expected += "/" + ("fairphone-fp6" if target == FP6 else "unihertz-titan2e")
                self.assertEqual(rig.settings.graph_url.format(arch=arch, channel="stable"), expected + "/stable.json")
                rig.backend = fakes.FakeRpmOstree(origin="luma:" + rig.settings.ref("stable", arch))
                doc = graph_doc([fakes.release("1.0.0", 1), fakes.release("1.0.1", 2)], arch=arch)
                if target:
                    doc.update(schema_version=2, hardware_target=target)
                rig.publish(doc)
                engine = Engine(rig.paths, rig.settings, rig.backend, rig.probes, rig.http,
                                clock=lambda: rig.now, arch=arch, boot_id=lambda: "fixture", greenboot_installed=True)
                self.assertEqual(engine.check().release.commit, fakes.commit(2))
                self.assertIn(expected + "/stable.json", rig.http.gets)
