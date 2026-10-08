#!/usr/bin/python3 -B
# SPDX-License-Identifier: Apache-2.0
"""Real file/publication boundaries and interrupted first-handoff behavior."""
from pathlib import Path
import hashlib,json,os,sys,tempfile,unittest,importlib.machinery,importlib.util
REPO=Path(__file__).resolve().parents[2]
loader=importlib.machinery.SourceFileLoader('baseline',str(REPO/'image/luma-desktop/rootfs/usr/libexec/luma-app-baseline'))
spec=importlib.util.spec_from_loader(loader.name,loader);baseline=importlib.util.module_from_spec(spec);loader.exec_module(baseline)
input_loader=importlib.machinery.SourceFileLoader('baseline_input',str(REPO/'scripts/os/lib/app_baseline.py'))
input_spec=importlib.util.spec_from_loader(input_loader.name,input_loader);baseline_input=importlib.util.module_from_spec(input_spec);input_loader.exec_module(baseline_input)

class Ref:
    def __init__(self,commit):self.commit=commit
    def get_commit(self):return self.commit

class Handoff(unittest.TestCase):
    def setUp(self):
        # These root-owned boundaries must execute in the normal root builder,
        # rather than mocking ownership checks away on a user's /tmp directory.
        self.assertEqual(os.geteuid(),0)
        self.tmp=tempfile.TemporaryDirectory(prefix='handoff-',dir=sys.argv[1]);self.home=Path(self.tmp.name);self.root=self.home/'baseline';self.root.mkdir();self.state=self.home/'state'
        self.apps=json.loads((REPO/'config/os/first-party-app-baseline.json').read_text())['applications']
        (self.root/'shipping.json').write_bytes((REPO/'config/os/first-party-app-baseline.json').read_bytes())
        roles={'schema':'org.projectluma.first-party-app-roles/v1','roles':{a:'signed-system-flatpak' for a in self.apps}}
        self.write('roles.json',roles)
        # The test fixture uses the actual maintained public key, not a fake key
        # admitted by weakening the production issuer boundary.
        import base64,configparser
        config=configparser.ConfigParser();config.read(REPO/'config/os/flatpak/luma.flatpakrepo')
        (self.root/'luma-depot.gpg').write_bytes(base64.b64decode(config['Flatpak Repo']['GPGKey']))
        refs=[f'runtime/{n}/x86_64/44' for n in sorted(baseline.RUNTIMES)]+[f'app/{a}/x86_64/beta' for a in self.apps]
        self.rows=[]
        for ref in refs:
            body=ref.encode();h=hashlib.sha256(body).hexdigest();(self.root/(h+'.flatpak')).write_bytes(body)
            self.rows.append({'ref':ref,'commit':h,'sha256':h,'bytes':len(body),'file':h+'.flatpak'})
        self.manifest={'schema':'org.projectluma.offline-app-baseline/v1','architecture':'x86_64','selection_scope':'os-shipping','shipping_contract_sha256':baseline.digest(self.root/'shipping.json'),'role_contract_sha256':baseline.digest(self.root/'roles.json'),'public_key_sha256':baseline.digest(self.root/'luma-depot.gpg'),'bundles':self.rows}
        self.write('manifest.json',self.manifest);self.installed={};self.calls=[]
    def tearDown(self):self.tmp.cleanup()
    def write(self,name,value):(self.root/name).write_text(json.dumps(value)+'\n')
    def install(self,path,key,row):self.calls.append(row['ref']);self.installed[row['ref']]=Ref(row['commit'])
    def verify(self,ref,expected):self.assertIn(expected,self.installed)
    def run_handoff(self,install=None):return baseline.handoff(self.root,self.state,'x86_64',lambda:self.installed,self.verify,install or self.install)
    def test_changed_bundle_refused_before_any_install(self):
        (self.root/self.rows[0]['file']).write_bytes(b'changed')
        with self.assertRaises(ValueError):self.run_handoff()
        self.assertEqual(self.calls,[])
    def test_linked_input_and_marker_refused(self):
        p=self.root/self.rows[0]['file'];p.unlink();p.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):self.run_handoff()
        self.assertEqual(self.calls,[])
        p.unlink();p.write_bytes(self.rows[0]['ref'].encode());self.run_handoff();marker=self.state/'installed-v1.json';marker.unlink();marker.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):self.run_handoff()
    def test_newer_existing_app_preserved_and_later_removal_not_reseeded(self):
        app=next(r for r in self.rows if r['ref'].startswith('app/'));self.installed[app['ref']]=Ref('e'*64)
        self.assertEqual(self.run_handoff(),'completed');self.assertNotIn(app['ref'],self.calls)
        self.assertEqual(self.installed[app['ref']].get_commit(),'e'*64)
        self.installed.pop(app['ref']);self.calls.clear();self.assertEqual(self.run_handoff(),'already-handed-off');self.assertEqual(self.calls,[])
    def test_interruption_preserves_successful_transactions_and_resumes_only_missing(self):
        def fail(path,key,row):
            if len(self.calls)==2:raise RuntimeError('interrupted transaction')
            self.install(path,key,row)
        with self.assertRaises(RuntimeError):self.run_handoff(fail)
        self.assertFalse((self.state/'installed-v1.json').exists());first=set(self.calls);self.calls.clear();self.run_handoff();self.assertFalse(first&set(self.calls))
    def test_foreign_existing_signature_refusal_never_replaces_it(self):
        row=self.rows[0];self.installed[row['ref']]=Ref(row['commit'])
        self.verify=lambda *args:(_ for _ in ()).throw(ValueError('foreign signature'))
        with self.assertRaises(ValueError):self.run_handoff()
        self.assertEqual(self.calls,[]);self.assertFalse((self.state/'installed-v1.json').exists())
    def test_optional_or_incomplete_ref_closure_refused(self):
        self.manifest['bundles']=self.rows[:-1];self.write('manifest.json',self.manifest)
        with self.assertRaises(ValueError):self.run_handoff()
        self.manifest['bundles']=self.rows;self.write('manifest.json',self.manifest)
        shipping=json.loads((self.root/'shipping.json').read_text());shipping['applications'][0]='org.projectluma.Write';self.write('shipping.json',shipping)
        with self.assertRaises(ValueError):self.run_handoff()
        self.assertEqual(self.calls,[])
    def test_linked_state_ancestor_refused(self):
        target=self.home/'other';target.mkdir();link=self.home/'linked';link.symlink_to(target,target_is_directory=True);self.state=link/'state'
        with self.assertRaises(ValueError):self.run_handoff()
        self.assertFalse((target/'state').exists())
    def test_marker_publication_is_exclusive_and_keeps_original(self):
        self.run_handoff();marker=self.state/'installed-v1.json';original=marker.read_bytes()
        with self.assertRaises(ValueError):baseline.finish(self.state,'a'*64,{})
        self.assertEqual(marker.read_bytes(),original)
    def test_os_input_admits_exact_files_and_records_bundle_identity(self):
        admitted=baseline_input.admit(self.root,REPO/'config/os/first-party-app-baseline.json')
        self.assertEqual(admitted['applications'],24);self.assertEqual(admitted['runtimes'],3)
        self.assertEqual(admitted['bundles'],self.rows)
        self.assertEqual(admitted['bundle_bytes'],sum(r['bytes'] for r in self.rows))
        self.assertEqual(admitted['manifest_sha256'],baseline.digest(self.root/'manifest.json'))
    def test_os_input_refuses_a_different_source_selection(self):
        other=self.home/'other-shipping.json';other.write_text('{"schema":"different"}')
        with self.assertRaises(ValueError):baseline_input.admit(self.root,other)
    def test_os_input_refuses_corrupt_bundle_before_composition(self):
        (self.root/self.rows[-1]['file']).write_bytes(b'corrupt input')
        with self.assertRaises(ValueError):baseline_input.admit(self.root,REPO/'config/os/first-party-app-baseline.json')
    def test_os_input_refuses_unexpected_extra_input(self):
        (self.root/'unexpected').write_text('not part of the baseline')
        with self.assertRaises(ValueError):baseline_input.admit(self.root,REPO/'config/os/first-party-app-baseline.json')

if __name__=='__main__':
    parent=sys.argv[1];unittest.main(argv=[sys.argv[0]])
