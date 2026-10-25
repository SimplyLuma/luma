# SPDX-License-Identifier: Apache-2.0
"""Exercise actual signed EOL commits in an isolated repository."""
import ast
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("retire_remote", ROOT / "scripts/depot/retire-remote-apps.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


@unittest.skipUnless(all(shutil.which(x) for x in ("flatpak", "ostree", "gpg")), "needs repository tools")
class Retirement(unittest.TestCase):
    def test_real_signed_retirement_preserves_payload_history_and_other_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            keys = root / "keys"
            keys.mkdir(mode=0o700)
            def run(*args):
                return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=120).strip()
            def ostree(*args):
                return run("ostree", f"--repo={repo}", *args)
            run("gpg", "--homedir", str(keys), "--batch", "--passphrase", "",
                "--quick-generate-key", "Isolated retirement fixture", "ed25519", "sign", "1d")
            fingerprint = next(row.split(":")[9] for row in run("gpg", "--homedir", str(keys),
                               "--with-colons", "--list-secret-keys").splitlines() if row.startswith("fpr:"))
            ostree("init", "--mode=archive-z2")
            ostree("config", "set", "core.min-free-space-size", "16MB")
            key = root / "public.gpg"
            key.write_bytes(subprocess.check_output(["gpg", "--homedir", str(keys), "--export", fingerprint]))
            ostree("remote", "add", f"--gpg-import={key}", "fixture", repo.as_uri())
            tree = root / "tree"
            (tree / "files").mkdir(parents=True)
            (tree / "files/preserved").write_text("keep these exact bytes\n")
            originals = {}
            for app, arch, branch in (("Displays", "aarch64", "beta"),
                                      ("Displays", "x86_64", "beta"),
                                      ("Displays", "aarch64", "nightly"),
                                      ("Viola", "x86_64", "beta")):
                (tree / "metadata").write_text(f"[Application]\nname=org.projectluma.{app}\n")
                ref = f"app/org.projectluma.{app}/{arch}/{branch}"
                originals[ref] = ostree("commit", f"--branch={ref}", f"--tree=dir={tree}", "--subject=fixture")
            records = policy.retire(repo, fingerprint, keys)
            self.assertEqual(len(records), 3)
            for row in records:
                self.assertTrue(row["changed"])
                self.assertEqual(row["previous"], originals[row["ref"]])
                self.assertEqual(ostree("rev-parse", row["commit"] + "^"), row["previous"])
                self.assertEqual(ostree("diff", row["previous"], row["commit"]), "")
                ostree("show", "--gpg-verify-remote=fixture", row["commit"])
                self.assertEqual(ostree("show", "--print-metadata-key=ostree.endoflife", row["commit"]),
                                 "'Display controls are in Settings.'")
                self.assertTrue(ostree("show", "--list-detached-metadata-keys", row["commit"]))
            other = "app/org.projectluma.Viola/x86_64/beta"
            self.assertEqual(ostree("rev-parse", other), originals[other])
            again = policy.retire(repo, fingerprint, keys)
            self.assertEqual([r["commit"] for r in again], [r["commit"] for r in records])
            self.assertFalse(any(row["changed"] for row in again))
            # The maintained descriptor generator must withdraw old descriptors
            # across both CPUs without deleting the EOL refs or unrelated app.
            site = root / "site"
            (site / "apps").mkdir(parents=True)
            (site / "apps/org.projectluma.Displays.flatpakref").write_text("stale")
            run("python3", str(ROOT / "scripts/depot/generate-remote-files.py"), "--repo", str(repo),
                "--site", str(site), "--gpg-key", str(key))
            self.assertFalse(list((site / "apps").glob("org.projectluma.Displays*.flatpakref")))
            self.assertEqual({r["app_id"] for r in json.loads((site / "apps/index.json").read_text())["apps"]},
                             {"org.projectluma.Viola"})
            self.assertEqual(set(ostree("refs").splitlines()), set(originals))

    def test_signed_host_and_remote_retirement_sets_agree(self):
        tree = ast.parse((ROOT / "src/luma-installer/luma_installer/retired_apps.py").read_text())
        value = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "RETIRED" for t in n.targets))
        self.assertEqual(set(policy.RETIRED), ast.literal_eval(value.args[0]))


if __name__ == "__main__":
    unittest.main()
