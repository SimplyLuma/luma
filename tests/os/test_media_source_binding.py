#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Real Git archive/check extraction policy, without VM/transport claims."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]

class MediaSourceBinding(unittest.TestCase):
    def test_old_media_override_selects_target_gate_only(self):
        with tempfile.TemporaryDirectory(prefix='luma-clean-media-policy-') as tmp:
            work=Path(tmp);repo=work/'git-fixture';repo.mkdir()
            def git(*args):
                return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()
            git('init','--quiet')
            git('config','user.name','Isolated policy fixture');git('config','user.email','fixture@invalid')
            paths=('tests/os/image-contract.sh','tests/smoke/desktop.sh','config/desktop/inputs.env','config/os/release.env','config/boot/apply-grub-policy.sh','tests/os/gate/target.sh')
            for name in paths:
                p=repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('original-base\n')
            git('add','.');git('commit','--quiet','-m','Synthetic isolated original media fixture')
            old=git('rev-parse','HEAD')
            for name in paths:(repo/name).write_text('target-update\n')
            git('add','.');git('commit','--quiet','-m','Synthetic isolated future gate fixture')
            new=git('rev-parse','HEAD')
            media=(ROOT/'scripts/os/verify-media.sh').read_text()
            # Execute actual maintained clean-media archive call and actual check-selection code.
            archive_call=next(line for line in media.splitlines()if line.startswith('luma_os_release_files '))
            function=media.split('release_check() {',1)[1].split('\n  scp -q',1)[0]
            shell=work/'exercise.sh'
            shell.write_text('set -euo pipefail\n. "$1/scripts/os/lib/common.sh"\nluma_os_repo_root=$2\nmedia_source=$3\nchecks_source=$4\nrelease_files=$5\nmedia_build_dir=\nLUMA_SOURCE_DIRTY=false\nLUMA_SOURCE_SNAPSHOT_SHA256=\n'+archive_call+'\nout=$6\nvm=$6\nresults=$6/results\nfailures=0\nselect_check() {\n'+function+'\n}\nselect_check target target\n')
            selected=work/'selected';selected.mkdir()
            result=subprocess.run(['bash',str(shell),str(ROOT),str(repo),old,new,str(work/'release-checks'),str(selected)],text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual((work/'release-checks/tests/os/image-contract.sh').read_text(),'original-base\n')
            self.assertEqual((selected/'target.sh').read_text(),'target-update\n')
            self.assertEqual((work/'release-checks/tests/os/gate/target.sh').read_text(),'original-base\n')

if __name__=='__main__':
    if os.geteuid()!=0:raise SystemExit('Run policy controls as root in an isolated fixture.')
    unittest.main()
