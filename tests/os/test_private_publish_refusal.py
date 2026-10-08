#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Execute the proposed publisher's real admission before any signing/ref write."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]

class PrivatePublishRefusal(unittest.TestCase):
    def test_actual_private_refusal_precedes_transaction(self):
        with tempfile.TemporaryDirectory(prefix='private-publish-refusal-') as tmp:
            work=Path(tmp);build=work/'builds/20261008.999';build.mkdir(parents=True)
            for name in ('gate-result.json','provenance.json','sbom.spdx.json','packages-installed.tsv','os-release'):
                (build/name).write_text('{}\n')
            (build/'export.env').write_text('LUMA_EXPORT_REF=luma/44/x86_64/nightly\nLUMA_EXPORT_CANDIDATE='+'1'*64+'\nLUMA_EXPORT_PARENT=\n')
            env=dict(os.environ,LUMA_OS_ROOT=str(work),LUMA_OS_REQUIRE_VOLUME='0',LUMA_OS_PROTECTED_VM='luma-test-private-refusal-nonexistent')
            for dirty,snapshot,checks,reason in [
                ('true','','','dirty source checkout'),
                ('false','2'*64,'3'*64,'private source snapshot'),
                ('false','','3'*64,'private source snapshot')]:
                with self.subTest(dirty=dirty,snapshot=bool(snapshot),checks=bool(checks)):
                    (build/'build.env').write_text('LUMA_OS_CHANNEL=nightly\nLUMA_SOURCE_DIRTY='+dirty+'\nLUMA_SOURCE_SNAPSHOT_SHA256='+snapshot+'\nLUMA_RELEASE_CHECKS_SHA256='+checks+'\n')
                    result=subprocess.run(['bash',str(ROOT/'scripts/os/publish-channel.sh'),'--build-id','20261008.999','--dry-run'],env=env,text=True,capture_output=True)
                    self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                    self.assertIn('refusing to publish',result.stderr)
                    self.assertIn(reason,result.stderr)
                    self.assertFalse((work/'publish').exists(),'publication directory was created before refusing private source')
                    self.assertFalse((work/'locks').exists(),'transaction lock was entered before refusing private source')

if __name__=='__main__':
    if os.geteuid()!=0:raise SystemExit('Run actual root pipeline admission in an isolated fixture.')
    unittest.main()
