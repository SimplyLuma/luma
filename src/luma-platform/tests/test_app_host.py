# SPDX-License-Identifier: Apache-2.0
"""Real Unix sockets, fork/pidfds, native/Python clients, and normal fallback."""
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

KIT = Path(os.environ.get('LUMA_APPKIT_TEST_PATH', Path(__file__).resolve().parents[1] / 'appkit'))
CLIENT = os.environ.get('LUMA_APP_LAUNCH')


def _host_admits_this_runner():
    # The host never forks for a launcher under seccomp or no_new_privs (a
    # container CI runner has both); there every launch runs in its own
    # interpreter, which is the contract these tests then check instead.
    try:
        lines = Path('/proc/self/status').read_text().splitlines()
    except OSError:
        return False
    status = dict(line.split(':', 1) for line in lines if ':' in line)
    return status.get('Seccomp', '').strip() == '0' and status.get('NoNewPrivs', '').strip() == '0'


ADMITTED = _host_admits_this_runner()


class AppHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = dict(os.environ, PYTHONPATH=str(KIT), XDG_RUNTIME_DIR=str(self.root))
        self.environment.pop('LUMA_APP_HOST', None)
        self.app = self.root / 'application.py'
        self.app.write_text('''from luma_appkit.apphost import handoff
handoff()
import json,os,sys
print(json.dumps({'pid':os.getpid(),'parent':os.getppid(),'argv':sys.argv,'cwd':os.getcwd(),
 'value':os.environ.get('TEST_LUMA_VALUE'),'nosite':sys.flags.no_user_site,
 'stdin':sys.stdin.read() if '--input' in sys.argv else ''}),flush=True)
if '--wait' in sys.argv:
 import time
 time.sleep(60)
raise SystemExit(23)
''')
        self.host = None

    def tearDown(self):
        if self.host:
            self.host.terminate()
            self.host.communicate(timeout=3)
        self.temp.cleanup()

    def start(self, nosite=True, warm=False):
        flags = ['-sP'] if nosite else []
        self.host = subprocess.Popen([sys.executable, *flags, '-c',
            f'from luma_appkit.apphost import serve;raise SystemExit(serve(warm={warm!r}))'],
            env=self.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.socket = self.root / 'luma/app-host.sock'
        for _ in range(100):
            if self.socket.exists(): break
            if self.host.poll() is not None: self.fail(self.host.communicate()[1].decode())
            time.sleep(.02)
        self.assertTrue(self.socket.exists())
        # Publication of the bound socket precedes selector/self-pipe setup.
        # A completed protocol exchange establishes readiness for FD accounting.
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as probe:
            probe.settimeout(8)
            probe.connect(str(self.socket)); probe.send(b"{}")
            self.assertEqual(probe.recv(32), b"")
        self.assertEqual(self.socket.stat().st_mode & 0o777, 0o600)

    def run_app(self, native=False, extra=(), environment=None):
        command = [CLIENT, str(self.app)] if native else [sys.executable, '-sP', str(self.app)]
        return subprocess.run([*command, *extra], input='sample input', text=True, capture_output=True,
            cwd=self.root, env=environment or self.environment, timeout=4)

    def test_python_client_preserves_arguments_environment_cwd_and_exit(self):
        self.start()
        self.environment['TEST_LUMA_VALUE'] = 'quote " slash \\ newline\n café'
        result = self.run_app(extra=['--input', 'argument with spaces'])
        self.assertEqual(result.returncode, 23, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['parent'], self.host.pid if ADMITTED else os.getpid())
        self.assertEqual(data['value'], self.environment['TEST_LUMA_VALUE'])
        self.assertEqual(data['stdin'], 'sample input')
        self.assertEqual(data['cwd'], str(self.root))
        self.assertEqual(data['argv'][-1], 'argument with spaces')

    @unittest.skipUnless(CLIENT, 'native client path required')
    def test_native_client_preserves_contract(self):
        self.start()
        self.environment['TEST_LUMA_VALUE'] = 'native " \\ \n café'
        result = self.run_app(True, ['--input', 'a"b\\c'])
        self.assertEqual(result.returncode, 23, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['parent'], self.host.pid if ADMITTED else os.getpid())
        self.assertEqual(data['value'], self.environment['TEST_LUMA_VALUE'])
        self.assertEqual(data['argv'][-1], 'a"b\\c')
        self.assertEqual(data['stdin'], 'sample input')
        self.assertEqual(data['nosite'], 1)

    @unittest.skipUnless(CLIENT, 'native client path required')
    def test_absent_host_and_agents_use_original_interpreter(self):
        result = self.run_app(True)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(json.loads(result.stdout)['parent'], os.getpid())
        self.start()
        result = self.run_app(True, ['--agent'])
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(json.loads(result.stdout)['parent'], os.getpid())

    @unittest.skipUnless(CLIENT, 'native client path required')
    def test_interpreter_and_linker_mismatch_fall_back(self):
        self.start(nosite=False)
        result = self.run_app(True)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(json.loads(result.stdout)['parent'], os.getpid())
        self.host.terminate(); self.host.communicate(timeout=3)
        self.start()
        env = dict(self.environment, PYTHONWARNINGS='ignore')
        result = self.run_app(True, environment=env)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(json.loads(result.stdout)['parent'], os.getpid())

    @unittest.skipUnless(CLIENT, 'native client path required')
    def test_confined_launcher_cannot_escape_through_host(self):
        self.start()
        command = [sys.executable, '-c',
            'import ctypes,os,sys;assert ctypes.CDLL(None).prctl(38,1,0,0,0)==0;os.execv(sys.argv[1],sys.argv[1:])',
            CLIENT, str(self.app)]
        result = subprocess.run(command, env=self.environment, text=True, capture_output=True, timeout=4)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(json.loads(result.stdout)['parent'], os.getpid())

    def test_shutdown_finishes_executor_writes_and_exit_handlers(self):
        self.start()
        pending = self.root / 'pending'
        handler = self.root / 'handler'
        self.app.write_text(f"""from luma_appkit.apphost import handoff
handoff()
import atexit,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
pool=ThreadPoolExecutor(max_workers=1)
pool.submit(lambda:(time.sleep(.1),Path({str(pending)!r}).write_text('saved')))
atexit.register(lambda:Path({str(handler)!r}).write_text('closed'))
raise SystemExit(23)
""")
        result = self.run_app(native=bool(CLIENT))
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(pending.read_text(), 'saved')
        self.assertEqual(handler.read_text(), 'closed')

    def test_import_environment_mismatch_uses_original_interpreter(self):
        self.start()
        result = self.run_app(native=bool(CLIENT),
            environment=dict(self.environment, LUMA_PHONE_APPLICATION_ID='org.projectluma.PrivateFixture'))
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(json.loads(result.stdout)['parent'], os.getpid())

    @unittest.skipUnless(ADMITTED, 'the host refuses a seccomp or no_new_privs launcher before this protocol step')
    def test_abandoned_launch_never_executes_application(self):
        self.start()
        marker = self.root / 'executed'
        script = self.root / 'marker.py'
        script.write_text(f'from pathlib import Path;Path({str(marker)!r}).touch()')
        helper = """import os,socket,sys
from luma_appkit.apphost import PROTOCOL,_send,_receive,_interpreter
connection=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET)
connection.connect(sys.argv[1])
cwd=os.open('.',os.O_PATH|os.O_DIRECTORY)
_send(connection,{'protocol':PROTOCOL,'argv':[sys.argv[2]],'env':dict(os.environ),
 'interpreter':_interpreter()},[0,1,2,cwd])
reply,fds=_receive(connection,1)
assert 'pid' in reply
for fd in fds:os.close(fd)
connection.close();os.close(cwd)
"""
        result = subprocess.run([sys.executable, '-sP', '-c', helper, str(self.socket), str(script)],
            env=self.environment, cwd=self.root, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        time.sleep(.2)
        self.assertFalse(marker.exists())
        self.assertIsNone(self.host.poll())

    def test_malformed_requests_do_not_leak_descriptors(self):
        self.start()
        before = len(list(Path(f'/proc/{self.host.pid}/fd').iterdir()))
        for _ in range(12):
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
                client.connect(str(self.socket))
                client.sendmsg([b'{bad-json'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                    struct.pack('4i', 0, 1, 2, 0))])
                self.assertEqual(client.recv(32), b'')
        time.sleep(.1)
        self.assertEqual(len(list(Path(f'/proc/{self.host.pid}/fd').iterdir())), before)

    @unittest.skipUnless(CLIENT, 'native client path required')
    def test_termination_reaches_app_and_host_has_no_idle_polling(self):
        self.start()
        client = subprocess.Popen([CLIENT, str(self.app), '--wait'], env=self.environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        data = json.loads(client.stdout.readline())
        client.terminate(); client.communicate(timeout=3)
        for _ in range(40):
            if not Path(f'/proc/{data["pid"]}').exists(): break
            time.sleep(.025)
        self.assertFalse(Path(f'/proc/{data["pid"]}').exists())
        before = Path(f'/proc/{self.host.pid}/stat').read_text().split()[13:15]
        time.sleep(1.2)
        after = Path(f'/proc/{self.host.pid}/stat').read_text().split()[13:15]
        self.assertEqual(before, after)
        self.host.terminate(); self.host.communicate(timeout=2)
        self.assertFalse(self.socket.exists())
        self.host = None


@unittest.skipUnless(CLIENT and os.environ.get('LUMA_APP_HOST_RUNTIME'), 'private GTK display required')
class DisplayHostTests(AppHostTests):
    # Reuse setup and cleanup, but not the basic protocol cases.
    def setUp(self):
        super().setUp()
        self.app.write_text('''import gi,os,json
from luma_appkit.apphost import handoff
handoff()
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
app=Gtk.Application(application_id='org.projectluma.LaunchFixture')
def activate(app):
 window=Gtk.ApplicationWindow(application=app,title='Launch regression fixture')
 button=Gtk.Button(label='Ready');window.set_child(button)
 button.connect('clicked',lambda *_:app.quit())
 def mapped(*_):
  clock=window.get_surface().get_frame_clock();handler=None
  def painted(*_):
   clock.disconnect(handler)
   windows=list(Gtk.Window.get_toplevels())
   print(json.dumps({'pid':os.getpid(),'mapped':sum(w.get_mapped() for w in windows),
    'hidden':sum(not w.get_mapped() for w in windows),'oom':open('/proc/self/oom_score_adj').read().strip()}),flush=True)
   GLib.idle_add(lambda:(button.emit('clicked'),False)[1])
  handler=clock.connect('after-paint',painted)
 window.connect('map',mapped);window.present()
app.connect('activate',activate)
app.run([])
raise SystemExit(23)
''')

    def wait_log(self, needle):
        import select
        buffer=b'';deadline=time.monotonic()+8
        while needle.encode() not in buffer and time.monotonic()<deadline:
            if select.select([self.host.stderr],[],[],.1)[0]:
                part=os.read(self.host.stderr.fileno(),16384)
                if not part:break
                buffer+=part
        self.assertIn(needle.encode(),buffer,buffer.decode(errors='replace'))

    def test_display_spare_draws_real_window_and_exits(self):
        self.start(warm=True);self.wait_log('display spare ready')
        result=self.run_app(True)
        self.assertEqual(result.returncode,23,result.stderr)
        state=json.loads(result.stdout)
        self.assertEqual(state['mapped'],1)
        self.assertEqual(state['oom'], Path('/proc/self/oom_score_adj').read_text().strip())
        self.assertEqual(state['hidden'],1)
        self.wait_log('using display-ready spare')

    def test_display_environment_mismatch_uses_fresh_process(self):
        self.start(warm=True);self.wait_log('display spare ready')
        result=self.run_app(True,environment=dict(self.environment,GDK_DEBUG='no-portals'))
        self.assertEqual(result.returncode,23,result.stderr)
        self.assertEqual(json.loads(result.stdout)['hidden'],0)


def load_tests(loader, suite, pattern):
    tests = loader.loadTestsFromTestCase(AppHostTests)
    for name in ('test_display_spare_draws_real_window_and_exits',
                 'test_display_environment_mismatch_uses_fresh_process'):
        tests.addTest(DisplayHostTests(name))
    return tests


if __name__ == '__main__':
    unittest.main(verbosity=2)
