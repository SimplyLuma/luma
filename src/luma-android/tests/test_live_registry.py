# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from luma_android.errors import RuntimeUnavailableError
from luma_android.live_registry import bounded_output, parse_live_packages, MAX_APPS
from luma_android.engine import WaydroidEngine


def record(package='org.example.App', name='Example'):
    return f'Name: {name}\npackageName: {package}\ncategories:\n\tandroid.intent.category.LAUNCHER\n'


class LiveRegistry(unittest.TestCase):
    def test_multiple_complete_live_records(self):
        self.assertEqual(parse_live_packages(record()+'\n'+record('org.other.App','Other')), {'org.example.App','org.other.App'})

    def test_malformed_duplicate_incomplete_and_empty_refused(self):
        for value in ('', record()+record(), record('../org.bad'), record('android'),
                      'packageName: org.example.App\ncategories:\n', 'Name: Example\n',
                      'Name: Example\npackageName: org.example.App\n',
                      record()+'unstructured cached text', record()+'Name: \n',
                      record()+'\t'+'x'*16384):
            with self.subTest(value=value[:60]), self.assertRaises(RuntimeUnavailableError):
                parse_live_packages(value)

    def test_record_limit(self):
        listing=''.join(record('org.app.P'+str(i)) for i in range(MAX_APPS))
        self.assertEqual(len(parse_live_packages(listing)), MAX_APPS)
        with self.assertRaises(RuntimeUnavailableError):
            parse_live_packages(listing+record('org.overflow.App'))

    def test_fresh_io_never_uses_cached_launcher_or_privileged_pm(self):
        engine=WaydroidEngine('/usr/bin/waydroid')
        with patch('luma_android.live_registry.bounded_output', return_value=record()) as live, \
             patch.object(engine,'applications_from_launchers',side_effect=AssertionError('cache used')), \
             patch.object(engine,'_run_as_android_package_manager',side_effect=AssertionError('pkexec used')):
            self.assertEqual(engine.live_launchable_packages(), {'org.example.App'})
            live.assert_called_once_with(['/usr/bin/waydroid','app','list'])
        with patch('luma_android.live_registry.bounded_output',side_effect=RuntimeUnavailableError('offline')), \
             patch.object(engine,'applications_from_launchers',return_value=['org.cached.App']):
            with self.assertRaises(RuntimeUnavailableError):engine.live_launchable_packages()

    def run_child(self, body, timeout=2):
        return bounded_output([sys.executable,'-I','-c',body],timeout)

    def test_real_pipes_success_exit_encoding_and_stream_bounds(self):
        self.assertEqual(self.run_child('print("fresh")'), 'fresh\n')
        for body in ('import sys;sys.exit(3)', 'import os;os.write(1,b"\\xff")',
                     'import os;os.write(1,b"x"*(4*1024*1024+1))',
                     'import os;os.write(2,b"x"*(64*1024+1))'):
            with self.subTest(body=body), self.assertRaises(RuntimeUnavailableError):self.run_child(body)

    def test_cleanup_keeps_unreaped_owned_leader_identity(self):
        real_killpg=os.killpg
        checked=[]
        def kill_owned(pid, sig):
            state=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[0]
            self.assertEqual(state, 'Z')
            checked.append(pid)
            real_killpg(pid,sig)
        with patch('luma_android.live_registry.os.killpg',side_effect=kill_owned):
            self.assertEqual(self.run_child('print("owned")'), 'owned\n')
            self.assertEqual(self.run_child('import os,time;pid=os.fork();(os.close(1),os.close(2),time.sleep(20)) if pid==0 else os._exit(0)'), '')
        self.assertEqual(len(checked),2)

    def test_closed_pipes_do_not_hide_still_running_query(self):
        before=time.monotonic()
        with self.assertRaises(RuntimeUnavailableError):
            self.run_child('import os,time;os.close(1);os.close(2);time.sleep(20)', .25)
        self.assertLess(time.monotonic()-before,2)

    def test_real_timeout_reaps_child_and_pipe_holding_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile=Path(directory)/'pid'
            body=f'import os,time;from pathlib import Path;Path({str(pidfile)!r}).write_text(str(os.getpid()));time.sleep(20)'
            before=time.monotonic()
            with self.assertRaises(RuntimeUnavailableError):self.run_child(body,.25)
            self.assertLess(time.monotonic()-before,2)
            self.assertFalse(Path('/proc/'+pidfile.read_text()).exists())
            body='import os,time;pid=os.fork();os._exit(0) if pid else time.sleep(20)'
            before=time.monotonic()
            with self.assertRaises(RuntimeUnavailableError):self.run_child(body,.25)
            self.assertLess(time.monotonic()-before,2)


if __name__=='__main__':unittest.main()
