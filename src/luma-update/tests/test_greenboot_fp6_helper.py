# SPDX-License-Identifier: Apache-2.0
"""Execute maintained health helper; isolate only filesystem/tool read boundaries."""
import os, pathlib, shlex, shutil, subprocess, tempfile, unittest
DEFAULT = pathlib.Path(__file__).resolve().parents[1] / "data/greenboot/luma-greenboot-common.sh"
HELPER = pathlib.Path(os.environ.get("LUMA_GREENBOOT_HELPER_UNDER_TEST", DEFAULT))
class NativeFP6Health(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=pathlib.Path(self.tmp.name)
        self.marker=self.root/"fp6-marker"; self.marker.touch()
        self.booted=self.root/"ostree-booted"; self.booted.touch()
        self.cmdline=self.root/"cmdline"; self.cmdline.write_text("androidboot.slot_suffix=_b")
        self.tool=self.root/"qbootctl"
        self.tool.write_text("""#!/bin/bash
case "$1" in
-c) printf 'Current slot: %s\n' "${CURRENT:-_b}";;
-b|-n)
  case "$2" in a|b) ;; *) echo "Expected slot not '$2'" >&2; exit 1;; esac
  if [ "$1" = -b ]; then printf 'SLOT _%s: is marked bootable\n' "$2"
  else
    if [ -n "${FAIL:-}" ]; then echo attribute-error >&2; exit 1; fi
    printf 'SLOT _%s: is %smarked successful\n' "$2" "${NOT_SUCCESSFUL-not }"
  fi;;
*) exit 1;;
esac
""")
        self.tool.chmod(0o755)
        self.bin=self.root/"bin";self.bin.mkdir()
        (self.bin/"logger").write_text("#!/bin/bash\nexit 0\n");(self.bin/"logger").chmod(0o755)
        text=HELPER.read_text()
        for original,path in [("/usr/lib/greenboot/fp6-qbootctl",self.marker),("/run/ostree-booted",self.booted),("/proc/cmdline",self.cmdline),("/usr/bin/qbootctl",self.tool)]:text=text.replace(original,str(path))
        self.helper=self.root/"helper.sh";self.helper.write_text(text)
    def tearDown(self):self.tmp.cleanup()
    def check(self,**extra):
        env=os.environ.copy();env.update(extra);env["PATH"]=str(self.bin)+os.pathsep+"/usr/bin:/bin"
        return subprocess.run([shutil.which("bash"),"-c", ". "+shlex.quote(str(self.helper))+"; luma_check_failed 'synthetic required check failed'"],env=env,capture_output=True,text=True,timeout=5)
    def test_trial_failure_never_passes(self):self.assertEqual(self.check().returncode,1)
    def test_stable_failure_preserves_no_auto_reboot_policy(self):self.assertEqual(self.check(NOT_SUCCESSFUL="").returncode,0)
    def test_missing_ostree_marker_refuses(self):self.booted.unlink();self.assertEqual(self.check().returncode,1)
    def test_missing_backend_refuses(self):self.tool.unlink();self.assertEqual(self.check().returncode,1)
    def test_failed_native_query_refuses(self):self.assertEqual(self.check(FAIL="1").returncode,1)
    def test_current_slot_mismatch_refuses(self):self.assertEqual(self.check(CURRENT="_a").returncode,1)
    def test_missing_cmdline_slot_refuses(self):self.cmdline.write_text("ro");self.assertEqual(self.check().returncode,1)
    def test_duplicate_slot_refuses(self):self.cmdline.write_text("androidboot.slot_suffix=_a androidboot.slot_suffix=_b");self.assertEqual(self.check().returncode,1)
    def test_unknown_slot_refuses(self):self.cmdline.write_text("androidboot.slot_suffix=_c");self.assertEqual(self.check().returncode,1)
    def test_unknown_success_response_refuses(self):self.assertEqual(self.check(NOT_SUCCESSFUL="invalid ").returncode,1)
class RealNativeFP6QueryContract(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("LUMA_TEST_REAL_FP6_QBOOTCTL") == "1", "explicit real FP6 read-only query test")
    def test_helper_matches_native_success_query(self):
        current=subprocess.check_output(["/usr/bin/qbootctl","-c"],text=True).strip()
        self.assertIn(current,("Current slot: _a","Current slot: _b"))
        arg=current[-1]
        reply=subprocess.check_output(["/usr/bin/qbootctl","-n",arg],text=True).strip()
        self.assertIn(reply,(f"SLOT _{arg}: is marked successful",f"SLOT _{arg}: is not marked successful"))
        result=subprocess.run(["/usr/bin/bash","-c",". "+shlex.quote(str(HELPER))+"; luma_fp6_trial_boot"],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,1 if reply==f"SLOT _{arg}: is marked successful" else 0,result.stderr)
if __name__=="__main__":unittest.main()
