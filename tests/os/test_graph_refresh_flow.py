# SPDX-License-Identifier: Apache-2.0
"""Run real sealed preparation/signer/refresher code against synthetic transports.

The optional root fixture never reads actual keys or contacts a publisher.
Podman/minisign and HTTP/rsync are explicitly fake routing transports: their
message hashes detect stale file selection, not cryptographic qualification.
"""
import copy
from datetime import datetime,timedelta,timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]
KEY_ID=next(line.partition('=')[2] for line in (ROOT/'config/os/release.env').read_text().splitlines() if line.startswith('LUMA_OS_GRAPH_KEY_ID='))
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
bucket_module=load('flow_bucket','scripts/os/lib/graph_bucket.py')
seal=load('flow_seal','scripts/depot/seal-signing-control.py')
prepare=load('flow_prepare','scripts/os/prepare-approved-graph-state.py')
refresh=load('flow_refresh','scripts/os/refresh-approved-graphs.py')
installer=load('flow_installer','scripts/os/install-approved-graph-refresh.py')
entry=load('flow_entry','scripts/os/graph-signer-entry.py')
def encoded(value):return (json.dumps(value,indent=2,sort_keys=True)+'\n').encode()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def timestamp(hours):return (datetime.now(timezone.utc)+timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
def signature(path,bucket):
    value=json.loads(path.read_text());Path(str(path)+'.minisig').write_text('fixture only\nnot a real signature\ntrusted comment: '+bucket.comment(value,KEY_ID)+'\nfixture-sha256='+sha(path)+'\n')

FIXTURE=os.environ.get('LUMA_GRAPH_FIXTURE_ROOT')
@unittest.skipUnless(os.getuid()==0 and FIXTURE,'needs explicitly owned root-only routing fixture')
class SealedRefreshFlow(unittest.TestCase):
    def setUp(self):
        parent=Path(FIXTURE)
        if not parent.is_absolute() or not parent.name.startswith('graph-routing-fixtures-'):
            raise ValueError('Dedicated graph-routing-fixtures-* root required')
        refresh.protected(parent)
        self.tmp=tempfile.TemporaryDirectory(prefix='case-',dir=parent);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.raw_run=subprocess.run;self.uploads=[]
        source=self.root/'source';source.mkdir()
        for relative in ('scripts/os','scripts/depot','config/os','config/desktop','image/os-tools'):
            shutil.copytree(ROOT/relative,source/relative,ignore=shutil.ignore_patterns('__pycache__'))
        self.control=self.root/'control';self.control_sha=seal.seal(source,self.control,graph=True)
        self.pipeline=self.root/'pipeline';(self.pipeline/'etc').mkdir(parents=True)
        (self.pipeline/'tmp').mkdir();(self.pipeline/'etc/storage.conf').write_text('fixture storage only\n')
        self.repo=self.pipeline/'publish/public-repo';self.repo.mkdir(parents=True)
        self.keys=self.root/'keys';(self.keys/'update-graph-minisign').mkdir(parents=True,mode=0o700)
        for name in ('luma-update-graph.pub','luma-update-graph.key'):
            path=self.keys/'update-graph-minisign'/name;path.write_text('SYNTHETIC NON-KEY FIXTURE\n');path.chmod(0o600)
        self.secrets=self.root/'secrets';self.secrets.mkdir();(self.secrets/'update-graph-minisign.password').write_text('SYNTHETIC NON-SECRET\n')
        self.origin=self.root/'origin';self.origin.mkdir();self.bin=self.root/'bin';self.bin.mkdir()
        fake=self.bin/'podman';fake.write_text(self.fake_podman());fake.chmod(0o500)
        self.cfg={'schema':'org.projectluma.approved-graph-refresh/v1','pipeline_root':str(self.pipeline),'keys_root':str(self.keys),
            'storage_config':str(self.pipeline/'etc/storage.conf'),'public_key_sha256':sha(self.keys/'update-graph-minisign/luma-update-graph.pub'),
            'tools_image':'a'*64,'key_id':KEY_ID,'workspace':str(self.root/'workspace'),'publication_lock':str(self.root/'publication.lock'),
            'origin_key':str(self.root/'origin.key'),'origin_destination':'publisher@fixture.invalid:',
            'channels':{'nightly':{'repository_path':str(self.repo),'public_repository_url':'https://dl.simplyluma.com/os/repo'}}}
        Path(self.cfg['origin_key']).write_text('SYNTHETIC NON-KEY\n');Path(self.cfg['origin_key']).chmod(0o600)

    def fake_podman(self):
        return '''#!/usr/bin/python3
import hashlib,json,pathlib,sys
args=sys.argv[1:]
if args[0]=='image':print('a'*64);sys.exit(0)
assert args[0]=='run'
mounts={}
for i,arg in enumerate(args[:-1]):
 if arg=='--volume':
  host,guest,mode=args[i+1].rsplit(':',2);mounts[guest]=host
def resolve(name):
 for guest,host in sorted(mounts.items(),key=lambda pair:-len(pair[0])):
  if name==guest or name.startswith(guest+'/'):return pathlib.Path(host+name[len(guest):])
 return pathlib.Path(name)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
if '--memory=2g' in args:
 path=resolve(args[-2]);comment=args[-1]
 assert comment.startswith('luma-update-graph channel=')
 pathlib.Path(str(path)+'.minisig').write_text('fixture only\\nnot a real signature\\ntrusted comment: '+comment+'\\nfixture-sha256='+sha(path)+'\\n')
else:
 path=resolve(args[args.index('-m')+1]);sig=resolve(args[args.index('-x')+1])
 assert 'fixture-sha256='+sha(path) in sig.read_text().splitlines()
'''

    def publication(self,arch='aarch64',target=''):
        self.bucket=bucket_module.GraphBucket(arch,target)
        self.cfg.update(architecture=arch,hardware_target=target,profile='full')
        self.release=dict(commit='b'*64,version='1.0.0-nightly.1',released_at=timestamp(-72),
            rollout=dict(start_at=timestamp(-72),start_percentage=1.0,duration_minutes=0),paused=False,
            deadend=False,deadend_reason=None,barrier=False,importance='normal',notes_url=None,summary='Fixture only',download_bytes_estimate=1)
        self.graph=dict(schema_version=2 if target else 1,channel='nightly',arch=arch,generated_at=timestamp(-48),releases=[self.release])
        if target:self.graph['hardware_target']=target
        gate=dict(commit=self.release['commit'],result='pass',fresh='pass',no_account='pass',update='not-applicable',rollback='not-applicable')
        provenance={'source':{'revision':'c'*40,'dirty':False}}
        delivery=dict(schema='org.projectluma.os-public-delivery/v1',commit=self.release['commit'],repository_url='https://dl.simplyluma.com/os/repo',complete_closure_readback=True,commit_signature_verified=True,summary_signature_verified=True)
        manifest=dict(commit=self.release['commit'],version=self.release['version'],published_utc=self.release['released_at'],channel='nightly',architecture=arch,
            hardware_target=target,ref=self.bucket.ref('nightly'),source_revision='c'*40,source_dirty=False,parent=None,
            gate={'result':'pass','sha256':hashlib.sha256(encoded(gate)).hexdigest()},provenance_sha256=hashlib.sha256(encoded(provenance)).hexdigest())
        path=self.bucket.release_manifest(self.repo,self.release['version']);path.parent.mkdir(parents=True)
        for name,value in [('manifest.json',manifest),('gate-result.json',gate),('provenance.json',provenance),('PUBLIC-DELIVERY.json',delivery)]:
            (path.parent/name).write_bytes(encoded(value))
        for suffix in ('.commit','.commitmeta'):
            obj=self.repo/'objects'/self.release['commit'][:2]/(self.release['commit'][2:]+suffix);obj.parent.mkdir(parents=True,exist_ok=True);obj.write_bytes(b'fixture signed record only')
        self.graphdir=self.bucket.directory(self.root/'graphs');self.graphdir.mkdir(parents=True)
        path=self.graphdir/'nightly.json';path.write_bytes(encoded(self.graph));signature(path,self.bucket)
        for prefix in self.bucket.public_prefixes():
            directory=self.origin/prefix;directory.mkdir(parents=True,exist_ok=True)
            for source in self.graphdir.iterdir():shutil.copy2(source,directory/source.name)
        self.config=self.root/'config.json';self.config.write_bytes(encoded(self.cfg))
        return path

    def prepare_state(self):
        self.state=self.root/'approved'
        with mock.patch.object(sys,'argv',['prepare',str(self.control),self.control_sha,str(self.config),str(self.graphdir),str(self.state),'nightly']):prepare.main()
        self.sealed=self.root/'sealed-approved';self.state_sha=seal.seal(self.state,self.sealed,inputs=True)

    def urlopen(self,request,timeout):
        url=request.full_url
        prefix='https://dl.simplyluma.com/'
        assert url.startswith(prefix)
        relative=url[len(prefix):]
        if relative.startswith('os/repo/objects/'):path=self.repo/relative.removeprefix('os/repo/')
        else:path=self.origin/relative
        return io.BytesIO(path.read_bytes())

    def run_transport(self,args,**kwargs):
        if args[0]=='podman':args=[str(self.bin/'podman'),*args[1:]]
        elif args[0]=='rsync':
            source,dest=Path(args[-2]),args[-1]
            assert dest.startswith('publisher@fixture.invalid:')
            target=self.origin/dest.split(':',1)[1]/source.name
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
            self.uploads.append((str(source),str(target)))
            return subprocess.CompletedProcess(args,0)
        elif args[0]=='/usr/bin/bash' and args[1].endswith('/sign-update-graph.sh'):
            kwargs['env']={**kwargs['env'],'PATH':str(self.bin)+':/usr/sbin:/usr/bin:/sbin:/bin','LUMA_OS_SECRETS':str(self.secrets)}
        return self.raw_run(args,**kwargs)

    def refresh_state(self):
        with mock.patch.object(sys,'argv',['refresh',str(self.control),self.control_sha,str(self.sealed),self.state_sha,'nightly']), \
             mock.patch.object(refresh.subprocess,'run',side_effect=self.run_transport), \
             mock.patch.object(refresh.urllib.request,'urlopen',side_effect=self.urlopen):refresh.main()

    def test_actual_prepare_sign_refresh_routes_desktop_and_registered_devices(self):
        # Root fixtures have real seals and proof hashes; only external signing
        # and transport are synthetic. Never contact keys, containers or origin.
        for arch,target in [('x86_64',''),('aarch64',''),('aarch64','fairphone,fp6'),('aarch64','unihertz,titan2e')]:
            with self.subTest(arch=arch,target=target):
                if hasattr(self,'bucket'):self.tearDown();self.setUp()
                self.publication(arch,target);self.prepare_state();self.refresh_state()
                canonical=self.bucket.directory(Path(self.cfg['workspace'])/'nightly')/'nightly.json'
                self.assertNotEqual(json.loads(canonical.read_text())['generated_at'],self.graph['generated_at'])
                self.assertEqual(canonical.parts.count(arch),1)
                for prefix in self.bucket.public_prefixes():
                    self.assertEqual((self.origin/prefix/'nightly.json').read_bytes(),canonical.read_bytes())
                self.assertTrue(all(Path(source).parent==canonical.parent for source,_ in self.uploads))

    def test_signed_cross_device_policy_is_refused_before_upload(self):
        self.publication('aarch64','fairphone,fp6');self.prepare_state()
        wrong={**self.graph,'hardware_target':'unihertz,titan2e'}
        path=self.origin/self.bucket.public_prefix()/'nightly.json';path.write_bytes(encoded(wrong))
        signature(path,bucket_module.GraphBucket('aarch64','unihertz,titan2e'))
        with self.assertRaises(ValueError):self.refresh_state()
        self.assertEqual(self.uploads,[])

    def test_old_parent_file_cannot_be_verified_uploaded_instead_of_signed_arm_bucket(self):
        self.publication();self.prepare_state()
        parent=Path(self.cfg['workspace'])/'nightly';parent.mkdir(parents=True)
        stale=parent/'nightly.json';stale.write_bytes(encoded(self.graph));signature(stale,self.bucket)
        self.refresh_state()
        self.assertEqual(stale.read_bytes(),encoded(self.graph))
        self.assertTrue(self.uploads)
        self.assertTrue(all(Path(source).parent==parent/'aarch64' for source,_ in self.uploads))

    def test_changed_sealed_inputs_are_refused(self):
        self.publication();self.prepare_state()
        path=self.sealed/'approved.json';path.chmod(0o600);path.write_text('{}\n');path.chmod(0o400)
        with self.assertRaises(ValueError):self.refresh_state()
        self.assertEqual(self.uploads,[])

    def test_x86_legacy_policy_change_is_not_overwritten(self):
        self.publication('x86_64');self.prepare_state()
        path=self.origin/'os/graph/nightly.json';changed=copy.deepcopy(self.graph);changed['releases'][0]['paused']=True
        path.write_bytes(encoded(changed));signature(path,self.bucket)
        with self.assertRaises(ValueError):self.refresh_state()
        self.assertEqual(self.uploads,[])

    def test_legacy_and_explicit_signer_entry_use_sealed_target_selection(self):
        self.publication();self.prepare_state()
        class Captured(Exception):pass
        invocations=[]
        def execute(*args):invocations.append(args);raise Captured()
        device_source=self.root/'device-input.json'
        device_source.write_bytes(encoded({**self.graph,'schema_version':2,'hardware_target':'fairphone,fp6'}))
        base=['entry',str(self.control),self.control_sha,'a'*64,str(self.pipeline),'nightly']
        for extra,arch in [([], 'x86_64'),(['--arch','aarch64','--profile','arm-preview'],'aarch64'),
                           (['--hardware-target','fairphone,fp6','--source',str(device_source),'--source-sha256',sha(device_source)],'aarch64')]:
            with mock.patch.object(sys,'argv',base+extra),mock.patch.object(entry.os,'execve',side_effect=execute),self.assertRaises(Captured):entry.main()
            self.assertEqual(invocations[-1][2]['LUMA_TARGET_ARCHITECTURE'],arch)
        with mock.patch.object(sys,'argv',base+['--hardware-target','fairphone,fp6']),self.assertRaises(ValueError):entry.main()

    def test_device_installers_generate_distinct_private_units(self):
        units=self.root/'units';units.mkdir()
        real_path=Path
        def fixture_path(value):return units if value=='/etc/systemd/system' else real_path(value)
        for target in ('fairphone,fp6','unihertz,titan2e'):
            if hasattr(self,'bucket'):
                # Reuse the same synthetic publisher while admitting another
                # immutable device state under its own directory.
                self.tearDown();self.setUp();units.mkdir(exist_ok=True)
            self.publication('aarch64',target);self.prepare_state()
            with mock.patch.object(sys,'argv',['install',str(self.control),self.control_sha,str(self.sealed),self.state_sha,'nightly','--apply']), \
                 mock.patch.object(installer,'Path',side_effect=fixture_path), \
                 mock.patch.object(installer.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as commands:
                installer.main()
            unit=units/(self.bucket.unit_name('nightly')+'.service')
            self.assertTrue(unit.is_file())
            self.assertIn(str(self.sealed),unit.read_text())
            self.assertFalse(any('luma-beta-os-graph-refresh.timer' in call.args[0] for call in commands.call_args_list))
        self.assertTrue((units/'luma-approved-os-graph-refresh-aarch64-fairphone-fp6-nightly.service').exists())
        self.assertTrue((units/'luma-approved-os-graph-refresh-aarch64-unihertz-titan2e-nightly.service').exists())

if __name__=='__main__':unittest.main()
