#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create signed offline first-boot bundles from the complete maintained catalog.

This producer has no private key and publishes nothing. It admits only the
already-signed commits and summary, then uses Flatpak's supported bundle format.
An incomplete catalog cannot produce a public-beta baseline or assign roles.
"""
import argparse
import base64
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

BASE = 'https://dl.simplyluma.com'
FINGERPRINTS = frozenset({'069E001599679B49042FE40885FCC398C02A8BEE'})
RUNTIMES = ('org.projectluma.Platform','org.projectluma.Platform.GL.default','org.projectluma.Platform.Locale')


def run(*args, **kwargs):
    return subprocess.run(args,check=True,text=True,capture_output=True,timeout=600,**kwargs).stdout


def selected_applications(entries, shipping=None):
    by_id={entry['id']:entry for entry in entries}
    if len(by_id)!=len(entries) or len(by_id)!=30 or any(not entry.get('recipe') for entry in entries):
        raise ValueError('Every maintained app needs a unique identity and producer.')
    if shipping is None:return set(by_id)
    if shipping.get('schema')!='org.projectluma.os-app-baseline/v1':
        raise ValueError('Unsupported OS app selection contract.')
    names=shipping.get('applications')
    if not isinstance(names,list) or not names or any(not isinstance(name,str) for name in names):
        raise ValueError('OS app selection is empty or malformed.')
    if len(set(names))!=len(names) or set(names)-by_id.keys():
        raise ValueError('OS app selection contains duplicates or unmaintained identities.')
    optional={'org.projectluma.Write','org.projectluma.Grid','org.projectluma.Stage','org.projectluma.Session','org.projectluma.Reel','org.projectluma.Darkroom'}
    if set(names)&optional:
        raise ValueError('Office and creative applications are outside the OS baseline.')
    return set(names)


def require_capacity(path, minimum, next_logical_bytes=0):
    if minimum < 0 or next_logical_bytes < 0:
        raise ValueError('Baseline capacity bounds must be nonnegative.')
    info=os.statvfs(path)
    available=info.f_bavail*info.f_frsize
    # Supported OSTree bundles recompress the file payload and add tree/index
    # metadata. Reserve twice the exact logical payload plus a bounded 32 MiB
    # for metadata, without relying on an optimistic compression ratio.
    bound=next_logical_bytes*2+33554432 if next_logical_bytes else 0
    if available < minimum+bound:
        raise ValueError('Offline baseline would exceed its physical-space reserve.')
    return available


def ref_logical_bytes(repo, ref):
    rows=run('ostree','ls','-R','-C','--repo='+str(repo),ref,'/').splitlines()
    sizes=[]
    for row in rows:
        parts=row.split()
        if len(parts)<6 or parts[0][0] not in '-dl':
            raise ValueError('Unrecognized actual OSTree payload inventory.')
        value=int(parts[3])
        if value<0:raise ValueError('Negative OSTree payload size.')
        sizes.append(value)
    return sum(sizes)


def produce(repo,site,registry,output,arch,shipping=None,minimum_free_bytes=0):
    if minimum_free_bytes<0:raise ValueError('Baseline physical reserve must be nonnegative.')
    if output.exists():raise ValueError('Baseline producer output is immutable.')
    if arch not in {'x86_64','aarch64'}:raise ValueError('Unsupported baseline architecture.')
    entries=json.loads(registry.read_text())['applications']
    ids=selected_applications(entries,json.loads(shipping.read_text()) if shipping else None)
    refs=['runtime/'+name+'/'+arch+'/44' for name in RUNTIMES]+['app/'+name+'/'+arch+'/beta' for name in sorted(ids)]
    heads={ref:run('ostree','rev-parse','--repo='+str(repo),ref).strip() for ref in refs}
    if any(not re.fullmatch('[0-9a-f]{64}',commit) for commit in heads.values()):raise ValueError('Invalid baseline source commit.')
    public=(site/'keys/luma-depot.gpg').read_bytes()
    if len(public)>65536:raise ValueError('Invalid baseline public key.')
    with tempfile.TemporaryDirectory(prefix='luma-baseline-verify-') as temporary:
        tmp=Path(temporary);key=tmp/'key.gpg';key.write_bytes(public)
        keyhome=tmp/'keyhome';keyhome.mkdir(mode=0o700)
        text=run('gpg','--batch','--no-options','--homedir',str(keyhome),'--with-colons','--show-keys',str(key))
        primaries=set();primary=False
        for line in text.splitlines():
            if line.startswith('pub:'):primary=True
            elif line.startswith('sub:'):primary=False
            elif primary and line.startswith('fpr:'):primaries.add(line.split(':')[9]);primary=False
        if not primaries or not primaries<=FINGERPRINTS:raise ValueError('Baseline key includes an unmaintained issuer.')
        sigcheck=tmp/'sigcheck'
        run('ostree','init','--mode=bare-user','--repo='+str(sigcheck))
        run('ostree','remote','add','--repo='+str(sigcheck),'--set=gpg-verify=true','--set=gpg-verify-summary=true',
            '--gpg-import='+str(key),'baseline',repo.resolve().as_uri())
        # Pulling the actual remote summary plus each metadata commit makes
        # signature admission independent of the builder's manifest claims.
        run('ostree','remote','refs','--repo='+str(sigcheck),'baseline')
        for ref in refs:
            run('ostree','pull','--repo='+str(sigcheck),'--commit-metadata-only','baseline',ref)
            if run('ostree','rev-parse','--repo='+str(sigcheck),'baseline:'+ref).strip()!=heads[ref]:
                raise ValueError('Signed baseline changed during admission.')
        output.mkdir(mode=0o700,parents=True)
        rows=[]
        for index,ref in enumerate(refs):
            kind,name,_arch,branch=ref.split('/')
            target=output/('bundle-'+str(index)+'.flatpak')
            require_capacity(output,minimum_free_bytes,ref_logical_bytes(repo,heads[ref]))
            options=['flatpak','build-bundle','--arch='+arch,'--repo-url='+BASE+'/repo',
                     '--runtime-repo='+BASE+'/luma.flatpakrepo','--gpg-keys='+str(key)]
            if kind=='runtime':options.append('--runtime')
            run(*options,str(repo),str(target),name,branch)
            require_capacity(output,minimum_free_bytes)
            if run('ostree','rev-parse','--repo='+str(repo),ref).strip()!=heads[ref]:
                raise ValueError('Baseline source ref changed while bundling.')
            # GI checks the supported bundle's actual identity; neither its
            # filename nor builder-supplied JSON supplies that identity.
            import gi
            gi.require_version('Flatpak','1.0')
            from gi.repository import Flatpak,Gio
            bundle=Flatpak.BundleRef.new(Gio.File.new_for_path(str(target)))
            if bundle.format_ref()!=ref or bundle.get_commit()!=heads[ref]:raise ValueError('Bundle content identity differs.')
            with target.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
            row={'ref':ref,'commit':heads[ref],'sha256':sha,'bytes':target.stat().st_size,'file':sha+'.flatpak'}
            target.rename(output/row['file']);rows.append(row)
        (output/'luma-depot.gpg').write_bytes(public)
        roles=(json.dumps({'schema':'org.projectluma.first-party-app-roles/v1',
                          'roles':{name:'signed-system-flatpak' for name in sorted(ids)}},sort_keys=True,indent=2)+'\n').encode()
        (output/'roles.json').write_bytes(roles)
        if shipping:(output/'shipping.json').write_bytes(shipping.read_bytes())
        document={'schema':'org.projectluma.offline-app-baseline/v1','architecture':arch,'default_channel':'beta',
                  'public_key_sha256':hashlib.sha256(public).hexdigest(),'role_contract_sha256':hashlib.sha256(roles).hexdigest(),
                  'selection_scope':'os-shipping' if shipping else 'complete-maintained-catalog',
                  'shipping_contract_sha256':hashlib.sha256(shipping.read_bytes()).hexdigest() if shipping else None,
                  'bundles':rows}
        (output/'manifest.json').write_text(json.dumps(document,sort_keys=True,indent=2)+'\n')
        for file in output.iterdir():file.chmod(0o644)
        print(json.dumps({'result':'PASS','applications':len(ids),'runtimes':len(RUNTIMES),
              'manifest_sha256':hashlib.sha256((output/'manifest.json').read_bytes()).hexdigest(),
              'private_key_access':False,'public_sync_executed':False,'installed_qualification':False}))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True);parser.add_argument('--site',type=Path,required=True)
    parser.add_argument('--registry',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--arch',choices=('x86_64','aarch64'),required=True)
    parser.add_argument('--shipping-apps',type=Path,help='Reviewed OS app selection; excluded Office/creative apps remain optional.')
    parser.add_argument('--minimum-free-bytes',type=int,default=0,help='Retain this measured physical-space reserve before and after every bundle.')
    args=parser.parse_args();produce(args.repo,args.site,args.registry,args.output,args.arch,args.shipping_apps,args.minimum_free_bytes)
