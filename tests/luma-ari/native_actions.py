# SPDX-License-Identifier: Apache-2.0
"""Server-only acceptance: actual broker, SDK, workspace sandbox and Valet.

Run under a private dbus-run-session with a disposable HOME and XDG roots.
The broker fixture supplies test identities and consent; its actual policy,
dispatch and request protocol run unchanged. No real apps or account are used.
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for part in ('src/luma-ari','src/luma-platform/sdk','src/luma-platform/broker','src/luma-installer'):
    sys.path.insert(0,str(ROOT/part))
spec = importlib.util.spec_from_file_location('broker_fixture',ROOT/'tests/unit/test_luma_semantic_broker_dbus.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
from gi.repository import Gio, GLib
from ari.tools import workspace, applications


class AriActions(fixture.SessionBusTests):
    def test_actual_ari_adapter_on_private_broker(self):
        publication = self.broker_call(self.provider_connection,'RegisterSurface',GLib.Variant.new_tuple(
            GLib.Variant('s','org.projectluma.Notes'), GLib.Variant('s','notes'),
            fixture.vardict(fixture.semantic_surface()), GLib.Variant('o',self.provider_path)), '(s)').unpack()[0]
        code = f'''
import json
from ari.tools import applications as a
before = a.app_surfaces({{}})
grant = a.request_app_access({{'application_id':'org.projectluma.Notes'}})
surfaces = a.app_surfaces({{}})
created = a.app_action({{'publication_id':{publication!r},'object_id':'notes','action_id':'notes.create'}})
try:
    a.app_action({{'publication_id':{publication!r},'object_id':'notes','action_id':'invented'}})
except RuntimeError:
    refused = True
else:
    refused = False
a.broker().revoke_access('org.projectluma.Notes')
after = a.app_surfaces({{}})
print(json.dumps(dict(before=before,grant=grant,surfaces=surfaces,created=created,refused=refused,after=after)))
'''
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE|Gio.SubprocessFlags.STDERR_PIPE)
        launcher.setenv('PYTHONPATH',os.pathsep.join(str(ROOT/p) for p in
            ('src/luma-ari','src/luma-platform/sdk','src/luma-platform/broker')),True)
        process = launcher.spawnv((sys.executable,'-c',code))
        loop = GLib.MainLoop(); result = {}
        def done(value, asynchronous, *_):
            try:
                ok,out,err = value.communicate_utf8_finish(asynchronous)
                result.update(ok=ok,out=out,err=err)
            finally:
                loop.quit()
        process.communicate_utf8_async(None,None,done,None)
        timer = GLib.timeout_add_seconds(40,lambda: (process.force_exit(),loop.quit(),False)[-1])
        loop.run()
        if GLib.MainContext.default().find_source_by_id(timer): GLib.source_remove(timer)
        self.assertEqual(process.get_exit_status(),0,result)
        data = json.loads(result['out'])
        self.assertEqual(data['before']['data'],[])
        self.assertTrue(data['grant']['ok'])
        self.assertEqual(len(data['surfaces']['data']),1)
        self.assertTrue(data['created']['ok'])
        self.assertTrue(data['refused'])
        self.assertEqual(data['after']['data'],[])
        self.assertEqual(self.provider_calls,[('notes','notes.create')])


class ProjectAndInstall(unittest.TestCase):
    def test_mcp_stdio_project_creation_and_source_roundtrip(self):
        from ari.mcp import Client
        tool = Client('workspace',[sys.executable,str(ROOT/'src/luma-ari/ari/tools/workspace.py')],
            env={**os.environ,'PYTHONPATH':os.pathsep.join(str(ROOT/p) for p in ('src/luma-ari','src/luma-platform/sdk'))})
        try:
            tool.start()
            self.assertTrue(tool.call('create_project',dict(project='mcp-app',name='MCP app',application_id='org.example.MCPApp'))['ok'])
            created = tool.call('write_project_file',dict(project='mcp-app',path='message.txt',content='Hello over MCP'))
            self.assertTrue(created['ok'])
            read = tool.call('read_project_file',dict(project='mcp-app',path='message.txt'))
            self.assertEqual(read['data']['content'],'Hello over MCP')
        finally:
            tool.stop()

    def test_actual_sdk_edit_check_build_and_sandbox_boundaries(self):
        self.assertNotEqual(Path.home(),Path(os.environ['ARI_REAL_HOME']))
        args = dict(project='calculator',name='Calculator',application_id='org.example.AriCalculator')
        self.assertTrue(workspace.create_project({**args,'preview':True})['ok'])
        self.assertFalse((workspace.root()/'calculator').exists())
        self.assertTrue(workspace.create_project(args)['ok'])
        tree = workspace.list_project({'project':'calculator'})
        self.assertIn('src/main.py',tree['data'])
        from luma_sdk.validation import validate_manifest
        import tomllib
        project = workspace.project_path('calculator')
        self.assertEqual(validate_manifest(tomllib.loads((project/'luma-app.toml').read_text()),project),[])
        self.assertTrue(workspace.write_project_file(dict(project='calculator',path='tests/test_math.py',
            content='import unittest\nclass Math(unittest.TestCase):\n def test_sum(self): self.assertEqual(2+2,4)\n'))['ok'])
        result = workspace.run_project(dict(project='calculator',command=['python3','-m','unittest','discover','-s','tests']))
        self.assertTrue(result['ok'],result)
        self.assertIn('OK',result['data']['output'])
        result = workspace.run_project(dict(project='calculator',command=['python3','-m','py_compile','src/main.py']))
        self.assertTrue(result['ok'],result)
        # A command trying to read home/env/bus/network cannot see real values.
        secret = Path(os.environ['ARI_REAL_HOME'])/'ari-test-secret-marker'
        code = "import os,socket;from pathlib import Path;assert not Path("+repr(str(secret))+").exists();assert 'ARI_SECRET' not in os.environ;assert not Path('/run/user').exists();s=socket.socket();assert s.connect_ex(('127.0.0.1',9)) != 0;print('isolation PASS')"
        with patch.dict(os.environ,{'ARI_SECRET':'fixture-secret'}):
            result = workspace.run_project(dict(project='calculator',command=['python3','-c',code]))
        self.assertTrue(result['ok'],result)

    def test_actual_review_install_record_remove_and_retained_data(self):
        # A tiny script-only portable app exercises the actual installer without
        # pulling container images or executing any downloaded program.
        package = Path.home()/'FixtureApp.tar.gz'
        files = {'FixtureApp/AppRun':b'#!/bin/sh\nexit 0\n',
                 'FixtureApp/fixture.desktop':b'[Desktop Entry]\nType=Application\nName=Ari action fixture\nExec=AppRun\n'}
        with tarfile.open(package,'w:gz') as archive:
            for name,content in files.items():
                item=tarfile.TarInfo(name);item.size=len(content);item.mode=0o755 if name.endswith('AppRun') else 0o644
                archive.addfile(item,io.BytesIO(content))
        reviewed = applications.inspect_package({'path':str(package)})
        self.assertTrue(reviewed['ok'],reviewed)
        token = reviewed['data']['review_id']
        preview = applications.install_package({'review_id':token,'preview':True})
        self.assertIn('publisher',preview['detail'])
        result = applications.install_package({'review_id':token})
        self.assertTrue(result['ok'],result)
        identity = result['data']['application_id']
        from luma_installer.desktop import iter_records
        record = next(row for row in iter_records() if row['application_id']==identity)
        self.assertTrue(Path(record['root']).is_dir())
        retained = Path.home()/'.local/share/luma/installer/data'/record['sha256']/'keep.txt'
        retained.parent.mkdir(parents=True);retained.write_text('personal data')
        self.assertTrue(applications.remove_application({'application_id':identity,'preview':True})['ok'])
        self.assertTrue(Path(record['root']).is_dir())
        self.assertTrue(applications.remove_application({'application_id':identity})['ok'])
        self.assertFalse(Path(record['root']).exists())
        self.assertFalse(any(row['application_id']==identity for row in iter_records()))
        self.assertEqual(retained.read_text(),'personal data')


if __name__=='__main__':
    unittest.main(defaultTest=['AriActions.test_actual_ari_adapter_on_private_broker','ProjectAndInstall'],verbosity=2)
