#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise summary-signing expiry/failure through the actual channel helper.

Only external OSTree/key operations are fixtures. This does not claim real
cryptographic verification; the normal publication's fresh-client check does.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
DRIVER=r'''set -euo pipefail
. "$1"
unlocked=1
luma_os_channel_head() { printf '%s\n' old; }
luma_os_delta_plan() { :; }
luma_os_log() { :; }
luma_os_die() { exit 1; }
luma_os_gpg_unlock() {
 printf '%s\n' unlock >> "$EVENTS"
 [[ "$FAIL_UNLOCK" == 0 ]] || return 23
 unlocked=1
}
luma_os_gpg_fingerprint() { printf '%s\n' fixture-key; }
luma_os_gpg_home() { printf '%s\n' /fixture/keyring; }
luma_os_verify_as_client() { printf '%s\n' client-verified >> "$EVENTS"; }
ostree() {
 case "$1" in
 rev-parse) printf '%s\n' old;;
 fsck) :;;
 reset) unlocked=0; printf '%s\n' agent-expired >> "$EVENTS";;
 summary)
   [[ "$unlocked" == 1 ]] || return 42
   [[ " $* " == *" --gpg-sign=fixture-key "* ]] || return 43
   printf '%s\n' signed-summary >> "$EVENTS";;
 *) return 44;;
 esac
}
LUMA_OS_EMPTY_DELTA=0
luma_os_advance_channel /fixture/repo fixture/ref new
'''

class SummarySigning(unittest.TestCase):
    def run_helper(self, fail):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        events=Path(tmp.name)/'events'
        env={**os.environ,'EVENTS':str(events),'FAIL_UNLOCK':str(int(fail))}
        result=subprocess.run(['bash','-c',DRIVER,'fixture',
                               str(ROOT/'scripts/os/lib/channel.sh')],
                              env=env,text=True,capture_output=True)
        return result,events.read_text().splitlines()
    def test_agent_expired_before_summary_is_unlocked_again(self):
        result,events=self.run_helper(False)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(events,['agent-expired','unlock','signed-summary','client-verified'])
        self.assertEqual(result.stdout.strip(),'[]')
    def test_failed_unlock_never_reports_unsigned_summary_success(self):
        result,events=self.run_helper(True)
        self.assertEqual(result.returncode,23,result.stderr)
        self.assertEqual(events,['agent-expired','unlock'])
        self.assertEqual(result.stdout,'')

if __name__=='__main__':unittest.main()
