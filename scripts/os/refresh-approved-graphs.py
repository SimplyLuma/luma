#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Refresh only administrator-sealed, already published beta/nightly policies.

Usage: refresh-approved-graphs.py CONTROL CONTROL_SHA STATE STATE_SHA CHANNEL [--check]
The release operator replaces the immutable STATE and pinned unit after each
qualified publication. A changed canonical policy fails closed in the interim.
No Hub token, unsigned graph discovery, app update, or OS payload publication.
"""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import urllib.request

def protected(path):
    for member in (path, *path.parents):
        st = member.lstat()
        if st.st_uid or st.st_mode & 0o022 or stat.S_ISLNK(st.st_mode):
            raise ValueError('Unsafe administrator-owned refresh input')

def module(path, name):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def release_evidence(manifest_path, record):
    """Read the exact evidence hashes recorded by normal state preparation."""
    evidence = {}
    for name in ('delivery', 'gate', 'provenance'):
        hash_key = name + '_sha256'
        if hash_key in record:
            filename = {'delivery': 'PUBLIC-DELIVERY.json', 'gate': 'gate-result.json',
                        'provenance': 'provenance.json'}[name]
            original = manifest_path.parent / filename
            protected(original)
            if digest(original) != record[hash_key]:
                raise ValueError('Publication evidence changed')
            evidence[name] = json.loads(original.read_text())
    return evidence

def main():
    if os.getuid() or len(sys.argv) not in (6,7):
        raise ValueError('Root-only usage: CONTROL CONTROL_SHA STATE STATE_SHA CHANNEL')
    control,control_sha,state,state_sha,channel=sys.argv[1:6]
    check_only=len(sys.argv)==7
    if check_only and sys.argv[6]!='--check': raise ValueError('Unsupported refresh mode')
    if channel not in ('beta','nightly') or any(not re.fullmatch('[a-f0-9]{64}',s) for s in (control_sha,state_sha)):
        raise ValueError('Unsupported channel or unsealed refresh identity')
    control,state=Path(control),Path(state)
    protected(control); protected(state)
    verifier=module(control/'scripts/depot/seal-signing-control.py','refresh_seal')
    verifier.verify(control,control_sha)
    verifier.verify(state,state_sha,'org.projectluma.signing-inputs/v1')
    checks=module(control/'scripts/os/lib/approved_graph.py','approved_graph')
    cfg=json.loads((state/'approved.json').read_text())
    if cfg['schema']!='org.projectluma.approved-graph-refresh/v1':
        raise ValueError('Unsupported approved state')
    paths=module(control/'scripts/os/lib/desktop_paths.py','desktop_paths')
    architecture=paths.architecture(cfg.get('architecture','x86_64'))
    profile=cfg.get('profile','full')
    hardware_target=cfg.get('hardware_target','')
    bucket=module(control/'scripts/os/lib/graph_bucket.py','refresh_bucket').GraphBucket(architecture,hardware_target)
    targets=module(control/'scripts/os/lib/desktop_target.py','desktop_target')
    targets.profile(control,architecture,profile)
    selected=cfg['channels'][channel]
    pipeline=Path(cfg['pipeline_root']); keys=Path(cfg['keys_root'])
    public_key=keys/'update-graph-minisign/luma-update-graph.pub'
    for path in (pipeline,keys,public_key,Path(cfg['storage_config'])): protected(path)
    if digest(public_key)!=cfg['public_key_sha256']:
        raise ValueError('Graph signing key differs from installed client trust')
    if (pipeline/'etc/storage.conf').read_bytes()!=Path(cfg['storage_config']).read_bytes():
        raise ValueError('Graph tools storage differs from the qualified cache')
    image=cfg['tools_image']
    if not re.fullmatch('[a-f0-9]{64}',image): raise ValueError('Unpinned graph tools image')
    environment={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8',
                 'PYTHONDONTWRITEBYTECODE':'1','LUMA_OS_ROOT':str(pipeline),'LUMA_OS_KEYS':str(keys),
                 'TMPDIR':str(pipeline/'tmp'),'LUMA_OS_GRAPH_TOOLS_IMAGE':image,
                 'CONTAINERS_STORAGE_CONF':cfg['storage_config'],
                 'LUMA_TARGET_ARCHITECTURE':architecture,'LUMA_OS_PROFILE':profile}
    def verify(data_dir,name):
        for path in (data_dir,data_dir/name,data_dir/(name+'.minisig')):protected(path)
        subprocess.run(['podman','run','--rm','--pull=never','--network=none','--read-only',
            '--cap-drop=ALL','--security-opt=no-new-privileges','--security-opt=label=disable',
            '--cpus=1','--memory=1g','--pids-limit=128','--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=32m',
            '--volume',str(public_key)+':/graph.pub:ro','--volume',str(data_dir)+':/graph:ro',
            '--entrypoint=/usr/bin/minisign',image,'-V','-p','/graph.pub',
            '-m','/graph/'+name,'-x','/graph/'+name+'.minisig'],env=environment,check=True,
            stdout=subprocess.DEVNULL)
        document=json.loads((data_dir/name).read_text())
        bucket.validate_graph(document,channel)
        comment='trusted comment: '+bucket.comment(document,cfg['key_id'])
        if comment not in (data_dir/(name+'.minisig')).read_text().splitlines():
            raise ValueError('Signature does not bind the expected graph channel, date, and installed key')
    approved_path=state/selected['graph']; verify(approved_path.parent,approved_path.name)
    approved=json.loads(approved_path.read_text())
    bucket.validate_graph(approved,channel)
    normal_repo=Path(subprocess.check_output(['/usr/bin/bash','-c',
        '. "$1"; luma_os_channel_repo "$2"','approved-refresh',
        str(control/'scripts/os/lib/common.sh'),channel],env=environment,text=True).strip())
    repo=Path(selected['repository_path'])
    if repo!=normal_repo: raise ValueError('Approved source differs from the normal channel repository')
    public_repository_url=selected['public_repository_url']
    if public_repository_url not in ('https://dl.simplyluma.com/os/repo','https://dl.simplyluma.com/os-preview/repo'):
        raise ValueError('Unsupported explicit public OS repository URL')
    records=selected['releases']
    if set(records)!=set(r['commit'] for r in approved['releases']): raise ValueError('Incomplete approved release state')
    for release in approved['releases']:
        record=records[release['commit']]
        path=bucket.release_manifest(repo,release['version'])
        protected(path)
        if digest(path)!=record['manifest_sha256']: raise ValueError('Published release manifest changed')
        manifest=json.loads(path.read_text())
        if manifest['channel']!=channel: raise ValueError('Published release channel changed')
        evidence=release_evidence(path,record)
        if 'gate' in evidence and record['gate_sha256']!=manifest.get('gate',{}).get('sha256'):
            raise ValueError('Passing gate hash differs from the genuine publication manifest')
        if 'provenance' in evidence and record['provenance_sha256']!=manifest.get('provenance_sha256'):
            raise ValueError('Source provenance differs from the genuine publication manifest')
        checks.admitted_release(release,manifest,architecture=architecture,hardware_target=hardware_target,public_repository_url=public_repository_url,**evidence)
        commit=repo/'objects'/release['commit'][:2]/(release['commit'][2:]+'.commit')
        if not commit.is_file(): raise ValueError('Approved release backend is missing; preserve the old feed')
        if not release['paused']:
            # Read only the small signed commit records, not the OS payload.
            # A historical delivery receipt alone cannot hide a dead route.
            for suffix in ('.commit','.commitmeta'):
                relative='objects/'+release['commit'][:2]+'/'+release['commit'][2:]+suffix
                expected=repo/relative;protected(expected)
                request=urllib.request.Request(public_repository_url+'/'+relative,
                    headers={'Cache-Control':'no-cache','User-Agent':'Luma-approved-graph-refresh/1'})
                with urllib.request.urlopen(request,timeout=20) as response:
                    actual=response.read(16*1024*1024+1)
                if len(actual)>16*1024*1024 or actual!=expected.read_bytes():
                    raise ValueError('The public signed commit backend is unavailable or changed')
    workspace=Path(cfg['workspace']); workspace.mkdir(mode=0o700,exist_ok=True);protected(workspace)
    publication_lock=Path(cfg['publication_lock'])
    protected(publication_lock.parent)
    with publication_lock.open('a') as lock:
        protected(publication_lock)
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix=channel+'-',dir=workspace) as work:
            work=Path(work)
            def served():
                snapshots={}
                for index,prefix in enumerate(bucket.public_prefixes()):
                    data_dir=work if index==0 else work/'legacy'
                    data_dir.mkdir(mode=0o700,exist_ok=True)
                    for suffix in ('','.minisig'):
                        name=channel+'.json'+suffix
                        request=urllib.request.Request('https://dl.simplyluma.com/'+prefix+'/'+name,
                                  headers={'Cache-Control':'no-cache','User-Agent':'Luma-approved-graph-refresh/1'})
                        with urllib.request.urlopen(request,timeout=20) as response:
                            data=response.read(1024*1024+1)
                        if len(data)>1024*1024: raise ValueError('Oversized public graph')
                        (data_dir/name).write_bytes(data)
                    verify(data_dir,channel+'.json')
                    current=json.loads((data_dir/(channel+'.json')).read_text())
                    bucket.validate_graph(current,channel)
                    checks.same_policy(approved,current)
                    snapshots[prefix]=(current,data_dir)
                return snapshots
            snapshots=served()
            current=snapshots[bucket.public_prefix()][0]
            target_args=['--hardware-target',hardware_target] if hardware_target else []
            if check_only:
                subprocess.run(['/usr/bin/bash',str(control/'scripts/os/sign-update-graph.sh'),
                    '--arch',architecture,'--profile',profile,*target_args,'--channel',channel,'--source',str(approved_path),'--graph-dir',str(work/'check-graph'),
                    '--no-sync','--dry-run'],env=environment,check=True)
                print('Approved '+channel+' refresh check PASS; no graph installed or uploaded')
                return
            graph_root=bucket.refresh_root(workspace,channel)
            graphdirs=bucket.directories(graph_root)
            graphdir=graphdirs[0]
            old=bucket.previous_refresh_directory(workspace,channel)
            for previous_dir in dict.fromkeys([*graphdirs,old]):
                previous=previous_dir/(channel+'.json')
                if previous.exists():
                    verify(previous_dir,channel+'.json')
                    previous_graph=json.loads(previous.read_text())
                    bucket.validate_graph(previous_graph,channel)
                    # A prior admitted state may contain an older release policy.
                    # Public snapshots, not this historical cache, must match approval.
                    if current['generated_at'] < previous_graph['generated_at']:
                        raise ValueError('The canonical graph predates the last successful refresh')
            for index,directory in enumerate(graphdirs):
                directory.mkdir(mode=0o700,parents=True,exist_ok=True);protected(directory)
                if not (directory/(channel+'.json')).exists():
                    served_dir=snapshots[bucket.public_prefixes()[index]][1]
                    for suffix in ('','.minisig'):
                        (directory/(channel+'.json'+suffix)).write_bytes((served_dir/(channel+'.json'+suffix)).read_bytes())
            subprocess.run(['/usr/bin/bash',str(control/'scripts/os/sign-update-graph.sh'),
                '--arch',architecture,'--profile',profile,*target_args,'--channel',channel,'--source',str(approved_path),'--graph-dir',str(graph_root),'--no-sync'],
                env=environment,check=True)
            for directory in graphdirs:
                verify(directory,channel+'.json')
                bucket.validate_graph(json.loads((directory/(channel+'.json')).read_text()),channel)
                checks.same_policy(approved,json.loads((directory/(channel+'.json')).read_text()))
            for suffix in ('','.minisig'):
                if any((directory/(channel+'.json'+suffix)).read_bytes()!=(graphdir/(channel+'.json'+suffix)).read_bytes()
                       for directory in graphdirs):
                    raise ValueError('Canonical and legacy signed graph bytes differ')
            served()  # A newer publication must not be overwritten after signing.
            key=Path(cfg['origin_key']); protected(key)
            if stat.S_IMODE(key.stat().st_mode)!=0o600: raise ValueError('Publisher key mode must be0600')
            destination=cfg['origin_destination']
            if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:',destination): raise ValueError('Invalid publisher destination')
            ssh='ssh -i '+shlex.quote(str(key))+' -oBatchMode=yes -oConnectTimeout=20 -oStrictHostKeyChecking=yes'
            files=[graphdir/(channel+'.json'+s) for s in ('.minisig','')]
            for prefix in bucket.public_prefixes():
                for path in files:
                    subprocess.run(['rsync','--delay-updates','--partial-dir=.rsync-partial','--timeout=60',
                        '--mkpath','-e',ssh,str(path),destination+prefix+'/'],env=environment,check=True)
            actual_snapshots=served()
            actual=actual_snapshots[bucket.public_prefix()][0]
            for _current,data_dir in actual_snapshots.values():
                for path in files:
                    if path.read_bytes()!=(data_dir/path.name).read_bytes():
                        raise ValueError('Public refresh readback differs')
            receipt={'schema':'org.projectluma.approved-graph-refresh-receipt/v1','channel':channel,'architecture':architecture,'hardware_target':hardware_target,'profile':profile,
                     'generated_at':actual['generated_at'],'control_sha256':control_sha,'state_sha256':state_sha,
                     'files':{p.name:digest(p) for p in files},'public_signature_and_readback':'PASS'}
            receipt_dir=bucket.directory(workspace);receipt_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
            temp=receipt_dir/(channel+'-LAST.json.partial'); temp.write_text(json.dumps(receipt,indent=2)+'\n')
            temp.replace(receipt_dir/(channel+'-LAST.json'));print(json.dumps(receipt))

if __name__=='__main__': main()
