#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Retain the ordinary unsigned provider contract, bounded to one Filer output."""
import hashlib,json,os,stat,subprocess,sys,tarfile
from pathlib import Path
root,out,profile_path=map(Path,sys.argv[1:]);assert os.getuid()==1000
profile=json.loads(profile_path.read_bytes());result=root/'out/apps/org.gnome.Nautilus';repo=result/'repo'
def sha(p):
    with p.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def command(*args):return subprocess.check_output(list(map(str,args)),text=True,timeout=60).strip()
ref='app/org.gnome.Nautilus/aarch64/beta';commit=command('ostree','--repo='+str(repo),'rev-parse',ref)
assert json.loads((result/'access-modes.json').read_bytes())['commit']==commit
assert json.loads((result/'access-modes.json').read_bytes())['result']=='PASS'
for name in ['metadata','SOURCES','permissions.json','lint-manifest.json','lint-builddir.json','lint-repo.json','appstream.txt']:assert (result/name).is_file()
subprocess.run(['ostree','--repo='+str(repo),'fsck'],check=True,timeout=120)
for name,expected in profile['refs'].items():assert command('ostree','--repo='+str(root/'repo'),'rev-parse',name)==expected
for name,key in [('summary','summary_sha256'),('summary.sig','summary_signature_sha256')]:assert sha(root/'repo'/name)==profile[key]
assert sha(root/'site/luma.flatpakrepo')==profile['flatpakrepo_sha256']
receipt=dict(result='NATIVE_ARM_FILER_MAINTAINED_UNSIGNED_PROVIDER_NOT_RUNTIME_OR_RELEASE_QUALIFIED',source=profile['source'],architecture='aarch64',app_id='org.gnome.Nautilus',ref=ref,commit=commit,provider_result_directory='provider',metadata_sha256=sha(result/'metadata'),SOURCES_sha256=sha(result/'SOURCES'),source_manifest_sha256=profile['filer_source_manifest_sha256'],sealed_recipe_sha256=profile['filer_manifest_sha256'],normal_build_lint_appstream_access_gates_passed=True,runtime_refs=profile['refs'],runtime_after_refs_and_summary_invariant=True,signature_checks_disabled=False,compiler_flags_changed=False,permissions_override=False,signing=False,publication=False)
memory={n:Path('/sys/fs/cgroup',n).read_text().strip() for n in ['memory.max','memory.peak','memory.events','memory.swap.max','cpu.max']}
events=dict(line.split() for line in memory['memory.events'].splitlines());assert events['oom_kill']=='0' and memory['memory.max']=='4294967296' and memory['memory.swap.max']=='0'
receipt['actual_container_resources']=memory
(result/'BUILT.json').write_text(json.dumps(receipt,indent=2)+'\n')
inventory={};total=0
for p in sorted(result.rglob('*')):
    st=p.lstat();assert not stat.S_ISLNK(st.st_mode) and (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)),str(p)
    if p.is_file():
        total+=st.st_size;assert total<=512*1024**2 and len(inventory)<20000
        inventory[str(p.relative_to(result))]=dict(bytes=st.st_size,sha256=sha(p),mode=stat.S_IMODE(st.st_mode))
archive=out/'provider.tar';assert not archive.exists()
with tarfile.open(archive,'x',format=tarfile.PAX_FORMAT) as tar:
    for relative,row in inventory.items():
        p=result/relative;entry=tarfile.TarInfo('provider/'+relative);entry.size=row['bytes'];entry.mode=row['mode'];entry.uid=entry.gid=0;entry.mtime=0
        with p.open('rb') as stream:tar.addfile(entry,stream)
assert archive.stat().st_size<=512*1024**2
seen=set()
with tarfile.open(archive,'r:') as tar:
    for entry in tar:
        assert entry.isfile() and entry.name.startswith('provider/');relative=entry.name[9:];assert relative in inventory and relative not in seen;seen.add(relative)
        with tar.extractfile(entry) as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==inventory[relative]['sha256']
        assert entry.size==inventory[relative]['bytes'] and entry.mode==inventory[relative]['mode']
    assert seen==set(inventory)
(out/'PROVIDER.json').write_text(json.dumps(dict(**receipt,archive='provider.tar',archive_sha256=sha(archive),archive_bytes=archive.stat().st_size,files=inventory),indent=2)+'\n')
print(json.dumps(receipt),flush=True)
