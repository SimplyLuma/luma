# SPDX-License-Identifier: Apache-2.0
"""Execute the recipe's timestamp fallback under Git's real ownership guard."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]

class DisplayConfigRecipe(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.repo=Path(self.temporary.name)/'source';self.repo.mkdir()
        subprocess.run(['git','init','--quiet',str(self.repo)],check=True)
        (self.repo/'file').write_text('private timestamp fixture\n')
        subprocess.run(['git','-C',str(self.repo),'add','file'],check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=CI fixture','-c','user.email=ci@example.invalid','commit','--quiet','-m','timestamp fixture'],check=True)
        self.line=next(line for line in (ROOT/'scripts/packages/build-luma-display-config.sh').read_text().splitlines() if line.startswith('source_date_epoch='))
    def execute(self,**extra):
        environment=dict(os.environ);environment.pop('SOURCE_DATE_EPOCH',None)
        environment.update(GIT_TEST_ASSUME_DIFFERENT_OWNER='1',**extra)
        return subprocess.run(['bash','-e','-c','repo_root=$1\n'+self.line+'\nprintf "%s\\n" "$source_date_epoch"','timestamp-fixture',str(self.repo)],env=environment,text=True,capture_output=True)
    def test_foreign_owned_ci_checkout_uses_scoped_trust_only(self):
        environment={**os.environ,'GIT_TEST_ASSUME_DIFFERENT_OWNER':'1'}
        untrusted=subprocess.run(['git','-c','safe.directory=','-C',str(self.repo),'log','-1','--format=%ct'],env=environment,text=True,capture_output=True)
        self.assertEqual(untrusted.returncode,128);self.assertIn('dubious ownership',untrusted.stderr)
        expected=subprocess.check_output(['git','-C',str(self.repo),'log','-1','--format=%ct'],text=True).strip()
        actual=self.execute();self.assertEqual(actual.returncode,0,actual.stderr);self.assertEqual(actual.stdout.strip(),expected)
        still_untrusted=subprocess.run(['git','-c','safe.directory=','-C',str(self.repo),'log','-1','--format=%ct'],env=environment,text=True,capture_output=True)
        self.assertEqual(still_untrusted.returncode,128)
    def test_explicit_reproducible_timestamp_still_overrides_git(self):
        actual=self.execute(SOURCE_DATE_EPOCH='946684800')
        self.assertEqual(actual.returncode,0,actual.stderr);self.assertEqual(actual.stdout.strip(),'946684800')

if __name__=='__main__':unittest.main()
