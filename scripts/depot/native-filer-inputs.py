#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Validate the reviewed public native-Filer input archives; never execute inputs."""
import argparse,configparser,hashlib,json,os,re,tarfile
from pathlib import Path,PurePosixPath
import yaml

G=1024**3
def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def safe(name):
    p=PurePosixPath(name)
    assert name and str(p)==name and not p.is_absolute() and '..' not in p.parts
    assert all(32<=ord(c)<127 for c in name)
    return p
def extract(archive,destination,limit,maximum,allowed_dirs=frozenset()):
    assert not destination.exists();destination.mkdir(mode=0o755)
    seen={};seen_dirs=set();total=0
    with tarfile.open(archive,'r:') as tar:
        for member in tar:
            if member.isdir():
                name=member.name[:-1] if member.name.endswith('/') else member.name
                safe(name);assert name in allowed_dirs and name not in seen_dirs and member.size==0
                seen_dirs.add(name);(destination/name).mkdir(parents=True,exist_ok=True);continue
            name=str(safe(member.name));assert name not in seen
            assert member.isfile() and not member.issparse() and 0<=member.size<=limit
            total+=member.size;assert total<=limit and len(seen)<maximum
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
            h=hashlib.sha256();size=0
            with tar.extractfile(member) as source,target.open('xb') as output:
                while chunk:=source.read(1024**2):output.write(chunk);h.update(chunk);size+=len(chunk)
            assert size==member.size;target.chmod(0o444)
            seen[name]=dict(bytes=size,sha256=h.hexdigest())
    return seen
def validate(profile,root):
    mirror=root/'runtime';capsule=root/'filer'
    runtime_rows=extract(root/'runtime-input.tar',mirror,8*G,100000,frozenset(['repo','repo/objects','repo/tmp','repo/state','repo/refs','repo/refs/heads','repo/refs/remotes','repo/refs/mirrors','site']))
    assert (mirror/'INPUTS.json').stat().st_size<=32*1024**2
    descriptor=json.loads((mirror/'INPUTS.json').read_bytes())
    assert descriptor['schema']=='org.projectluma.depot.runtime-input/v1'
    assert descriptor['profile']==profile
    inventory=descriptor['files'];assert inventory=={k:v for k,v in runtime_rows.items() if k!='INPUTS.json'}
    allowed={'repo/config','repo/summary','repo/summary.sig','site/luma.flatpakrepo'}|{'repo/refs/heads/'+r for r in profile['refs']}
    objects=[]
    for path,row in inventory.items():
        if path in allowed:continue
        assert re.fullmatch(r'repo/objects/[0-9a-f]{2}/[0-9a-f]{62}\.(filez|dirtree|dirmeta|commit|commitmeta)',path),path
        if not path.endswith('.commitmeta'):objects.append(row)
    assert len(objects)==profile['reachable_object_count'] and sum(r['bytes'] for r in objects)==profile['reachable_stored_bytes']
    for relative,key in [('repo/summary','summary_sha256'),('repo/summary.sig','summary_signature_sha256'),('site/luma.flatpakrepo','flatpakrepo_sha256')]:assert digest(mirror/relative)==profile[key]
    for ref,commit in profile['refs'].items():assert (mirror/'repo/refs/heads'/ref).read_text().strip()==commit
    config=configparser.ConfigParser(interpolation=None);config.read(mirror/'repo/config')
    assert config['core']['mode']=='archive-z2' and config['core']['repo_version']=='1'
    assert not any(key in config['core'] for key in ['parent','remotes-config-dir'])
    for name in ['tmp','state','extensions','refs/remotes']:(mirror/'repo'/name).mkdir(parents=True,exist_ok=True)
    capsule_rows=extract(root/'filer-input.tar',capsule,256*1024**2,4)
    assert digest(capsule/'SOURCE-MANIFEST.json')==profile['filer_source_manifest_sha256']
    assert digest(capsule/'org.gnome.Nautilus.yml')==profile['filer_manifest_sha256']
    recipe=yaml.safe_load((capsule/'org.gnome.Nautilus.yml').read_bytes())
    declared=recipe['modules'][0]['sources'];assert len(declared)==1 and declared[0]['type']=='archive'
    declared=declared[0];name=str(safe(declared['path']));assert '/' not in name and name=='source-'+declared['sha256']+'.tar'
    assert set(capsule_rows)=={'SOURCE-MANIFEST.json','org.gnome.Nautilus.yml',name} and digest(capsule/name)==declared['sha256']
    manifest=(capsule/'SOURCE-MANIFEST.json').read_bytes();materials=json.loads(manifest)['materials'];seen=set()
    with tarfile.open(capsule/name,'r:') as tar:
        for member in tar:
            assert member.isfile() and member.name.startswith('source/') and member.name not in seen and member.size<=128*1024**2
            safe(member.name);seen.add(member.name);relative=member.name[7:];body=tar.extractfile(member).read()
            if relative=='SOURCE-MANIFEST.json':assert body==manifest
            else:
                row=materials[relative];assert len(body)==row['bytes'] and hashlib.sha256(body).hexdigest()==row['sha256'] and member.mode==row['mode']
        assert seen=={'source/'+n for n in materials}|{'source/SOURCE-MANIFEST.json'}
    return dict(result='PUBLIC_INPUT_BYTES_VERIFIED_NOT_BUILD_OR_SIGNATURE_ACCEPTANCE',profile=profile,runtime_files=len(runtime_rows),source_materials=len(materials),runtime_archive_sha256=digest(root/'runtime-input.tar'),filer_archive_sha256=digest(root/'filer-input.tar'))
def main():
    ap=argparse.ArgumentParser();ap.add_argument('profile',type=Path);ap.add_argument('transport',type=Path);ap.add_argument('root',type=Path);a=ap.parse_args()
    profile=json.loads(a.profile.read_bytes());transport=json.loads(a.transport.read_bytes())
    assert profile['source']=='bdaf62fd08989b790decfac941a6947f8f72273a' and profile['architecture']=='aarch64'
    assert transport['schema']=='org.projectluma.depot.native-filer-transport/v1'
    # The launcher downloads only these fully reviewed bodies. Unrendered preparation refuses.
    for key,name,bound in [('runtime_archive','runtime-input.tar',8*G),('filer_archive','filer-input.tar',256*1024**2)]:
        row=transport[key];assert isinstance(row,dict) and re.fullmatch('[0-9a-f]{64}',row.get('sha256') or '') and type(row.get('bytes')) is int and 0<row['bytes']<=bound
        from urllib.parse import urlsplit
        url=urlsplit(row['url']);assert url.scheme=='https' and url.hostname=='dl.simplyluma.com' and url.port is None and not url.username and not url.password and not url.query and not url.fragment
        assert url.path=='/inputs/depot-build/'+row['sha256']+'/'+name
        archive=a.root/name;assert archive.stat().st_size==row['bytes'] and digest(archive)==row['sha256']
    print(json.dumps(validate(profile,a.root)),flush=True)
if __name__=='__main__':main()
