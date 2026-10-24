#!/usr/bin/env python3
"""Prepare an explicit server transport around the frozen guest behavior driver.

No VM is launched here. The extracted setup/seat/driver/input/result assertions
are byte-for-byte the reviewed R5 host behavior block. New generated receipt
assembly lives outside it; historical Mac metadata never enters server evidence.
"""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path

START = "ssh=['ssh','-n','-T'"
END = " qpath=out/'nightly-installed-results/result.json'"

# The original behavior block intentionally uses its original deadlines and
# disposable user. Only the actual signed checker capsule input path changes.
OLD_COPY = "copy(w/'installed-checks','/var/tmp/nightly-installed-checks/')"
NEW_COPY = "copy(fixture/'checks','/var/tmp/nightly-installed-checks/')"

PRELUDE = r'''
import hashlib,json,os,subprocess,time,threading,struct,sys,shlex,importlib.util
from pathlib import Path
assert len(sys.argv)==3,'Require frozen runner configuration and independently supplied SHA256'
config_path=Path(sys.argv[1]); config_body=config_path.read_bytes()
assert config_path.stat().st_uid==0 and not config_path.stat().st_mode&0o222
assert hashlib.sha256(config_body).hexdigest()==sys.argv[2]
config=json.loads(config_body)
module_path=Path(config['provider_module'])
assert hashlib.sha256(module_path.read_bytes()).hexdigest()==config['provider_module_sha256']
spec=importlib.util.spec_from_file_location('server_provider',module_path)
provider=importlib.util.module_from_spec(spec);spec.loader.exec_module(provider)
fixture=Path(config['fixture']);expected=config['expected']
p,prepared_sha,expected_heads=provider.validate_prepared(fixture,expected)
assert not (fixture/'QUALIFIED.json').exists() and (fixture/'INSTALLED.json').exists()
# The full previous receipt chain is validated by final CLOSED acceptance.
# Before guest assertions, bind actual booted storage to the prepared inputs.
assert config['qmp_transport']==p['qmp_transport']
if config['qmp_transport']=='libvirt-system-qmp/v1':
 bridge=provider.ServerLibvirtInputBridge(p['producer_binding']['domain_name'],p['producer_binding']['domain_uuid'],config['qemu_pid'],config['qemu_start_ticks'],config['qemu_invocation'],peer_uid=config['qemu_uid'])
else:
 bridge=provider.ServerInputBridge(Path(config['qmp_socket']),config['qemu_pid'],config['qemu_start_ticks'],config['qemu_invocation'],peer_uid=config['qemu_uid'],capture_directory=Path(config['capture_directory']))
assert config['qemu_invocation']==json.loads((fixture/'qemu-invocation.json').read_text())
installed_before=json.loads(provider.regular_bytes(fixture/'INSTALLED.json',owner_uid=0,immutable=True))
history=json.loads(provider.regular_bytes(fixture/'launch-history.json',owner_uid=0,immutable=True))
active_body=provider.regular_bytes(fixture/history[-1]['transition_file'],owner_uid=0,immutable=True)
assert hashlib.sha256(active_body).hexdigest()==installed_before['active_launch_sha256']==history[-1]['transition_sha256']
active_launch=json.loads(active_body)
assert (config['qemu_pid'],config['qemu_start_ticks'])==(active_launch['qemu_pid'],active_launch['qemu_start_ticks'])
assert config['qemu_uid']==p['producer_binding']['qemu_uid']
blocks=bridge.execute('query-block')
for path,fmt,readonly in ((p['iso_path'],'raw',True),(p['target_disk_path'],'qcow2',False)):
 matches=[row['inserted'] for row in blocks if row.get('inserted',{}).get('file')==path]
 assert len(matches)==1,('Actual server block device missing',path)
 inserted=matches[0];assert inserted['drv']==fmt and inserted['ro'] is readonly
 if fmt=='qcow2':
  assert inserted['image']['virtual-size']==provider.VIRTUAL_BYTES and not inserted['image'].get('backing-filename')
free=os.statvfs(Path(p['target_disk_path']).parent)
assert free.f_bavail*free.f_frsize>=provider.FLOOR_BYTES
w=Path(config['workspace']);build_id=p['build_id'];native=p
run=p['fixture_token'];domain=config['domain'];g={'commit':p['candidate'],'run_id':run}
key=Path(config['ssh_private_key']);port=str(config['ssh_port'])
assert key.is_file() and port.isdecimal() and 1024<=int(port)<=65535
out=fixture/'execution';out.mkdir(mode=0o700)
expected_file=fixture/'signed-heads.json'
shot_counter=0
def snapshot(path):
 global shot_counter
 shot_counter+=1;tmp=out/('qmp-shot-'+str(shot_counter)+'.png')
 size=bridge.snapshot(tmp);os.replace(tmp,path);return size
def key_event(key):bridge.key(key)
def click_event(x,y):
 width,height=snapshot(out/'before-input.png')
 assert 0<=x<width and 0<=y<height
 bridge.click(x,y,width,height)
 (out/'hardware-click-qmp.json').write_text(json.dumps({'result':'sent','x':x,'y':y,'width':width,'height':height,'vm':domain})+'\n')
def qmp(action,*args):
 if action=='status':return json.dumps(bridge.execute('query-status'))
 assert action=='key' and args==('meta_l-s',),'Unreviewed original shell shortcut'
 bridge.execute('send-key',{'keys':[{'type':'qcode','data':code} for code in ('meta_l','s')],'hold-time':80})
 return '{}'
'''

FINALE = r'''
 # Re-read every actual outcome and freeze without fabricating a Mac provider.
 qpath=out/'nightly-installed-results/result.json';q=json.loads(qpath.read_text())
 assert q['source_revision']==p['source_revision']
 provider.validate_rows(q,expected_heads)
 dispatch=json.loads((out/'driver-dispatch.json').read_text());assert dispatch['returncode']==0
 assert hashlib.sha256((fixture/'PREPARED.json').read_bytes()).hexdigest()==prepared_sha
 provider.validate_prepared(fixture,expected)
 installed=json.loads((fixture/'INSTALLED.json').read_text())
 assert provider.identity(Path(p['target_disk_path']))==installed['disk_identity']
 proofs={'guest-result.json':qpath.read_bytes(),
         'driver-outcome.json':{'returncode':dispatch['returncode'],'timed_out':False,'results_present':True},
         'runtime-heads.json':json.loads((out/'runtime-heads.json').read_text())['actual_installed_runtime_heads'],
         'native-host-headers.json':json.loads((out/'native-host-floor-rpm-headers.json').read_text())['actual_installed_native_components']}
 for file in (out/'nightly-installed-results').iterdir():
  if file.is_file() and file.name!='result.json':proofs['guest-'+file.name]=file.read_bytes()
 for file in out.glob('packaged-*.raw'):proofs[file.name]=file.read_bytes()
 for file in (out/'active-seat.txt',out/'driver-dispatch.json',out/'driver.log',out/'candidate-health.txt',out/'rpm-ostree-status.json'):
  proofs['actual-'+file.name]=file.read_bytes()
 proofs['runner-configuration.json']=config_path.read_bytes()
 proofs['actual-server-blocks.json']=blocks
 provider.write_stage(fixture,expected,'QUALIFIED','PASS','INSTALLED',proofs,
                      disk_identity=installed['disk_identity'],unsigned_modifications=[],
                      architecture='aarch64',results=q['results'],qmp_transport=p['qmp_transport'],
                      active_launch_sha256=installed['active_launch_sha256'],
                      guest_result_sha256=hashlib.sha256(qpath.read_bytes()).hexdigest(),
                      existing_Depot_update=json.loads((out/'depot-existing-update/result.json').read_text()))
 print('PASS server TCG installed guest behavior; CLOSED custody still required',flush=True)
finally:
 # The normally admitted lifecycle controller owns clean shutdown and archival.
 # This process never releases another producer's reservation or reports CLOSED.
 pass
'''


def prepare_runner(original: Path, original_sha256: str, destination: Path):
    original = Path(original)
    body = original.read_bytes()
    if hashlib.sha256(body).hexdigest() != original_sha256:
        raise ValueError('Frozen original driver digest differs')
    source = body.decode()
    if source.count(START) != 1 or source.count(END) != 1 or source.count(OLD_COPY) != 1:
        raise ValueError('Reviewed original behavior block boundaries differ')
    block = source[source.index(START):source.index(END)]
    if block.count('timeout=1800') != 1:
        raise ValueError('Original driver timeout contract changed')
    adapted = block.replace(OLD_COPY, NEW_COPY)
    result = PRELUDE + adapted + FINALE
    ast.parse(result)
    # Reversible textual patch is the complete adapter for this behavior block.
    if adapted.replace(NEW_COPY, OLD_COPY) != block:
        raise ValueError('Original behavior assertions changed')
    forbidden = ('mac_ssh', 'MAC-STAGED', 'native_mac_vm', 'local_storage', 'hvf')
    if any(value in result for value in forbidden):
        raise ValueError('Historical Mac transport metadata leaked')
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(result.encode());stream.flush();os.fsync(fd)
    finally:
        os.close(fd)
    return {'result': 'PREPARED_ONLY_NOT_GUEST_EXECUTED',
            'original_sha256': original_sha256,
            'original_behavior_block_sha256': hashlib.sha256(block.encode()).hexdigest(),
            'adapted_behavior_block_sha256': hashlib.sha256(adapted.encode()).hexdigest(),
            'runner_sha256': hashlib.sha256(result.encode()).hexdigest(),
            'behavior_patch': {'from': OLD_COPY, 'to': NEW_COPY},
            'assertions_and_deadlines_preserved': True}


def main():
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--original', type=Path, required=True)
    p.add_argument('--original-sha256', required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(prepare_runner(args.original, args.original_sha256, args.out), indent=2))


if __name__ == '__main__':
    main()
