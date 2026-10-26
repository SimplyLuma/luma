"""Portable keyless normal OSTree public closure readback, both selected CPUs."""
from pathlib import Path
import argparse,hashlib,json,re,subprocess,urllib.request,os,configparser
def main():
 p=argparse.ArgumentParser();p.add_argument('--profile',type=Path,required=True);p.add_argument('--profile-sha256',required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--cache',type=Path,required=True);a=p.parse_args()
 def sha(q):
  with q.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
 assert re.fullmatch('[0-9a-f]{64}',a.profile_sha256) and sha(a.profile)==a.profile_sha256 and a.profile.stat().st_size<=4*1024**2
 d=json.loads(a.profile.read_bytes());assert d['schema']=='org.projectluma.native38-public-readback/v1' and d['origin']=='https://dl.simplyluma.com/'
 assert len(d['all_heads'])==76 and len(d['closure_refs'])==56 and all(d['all_heads'][r]==h for r,h in d['closure_refs'].items())
 for r,h in d['all_heads'].items():assert re.fullmatch(r'(app|runtime)/[A-Za-z0-9_.-]+/(aarch64|x86_64)/[A-Za-z0-9_.-]+',r) and re.fullmatch('[0-9a-f]{64}',h)
 assert a.out.is_absolute() and not a.out.exists();a.out.mkdir(mode=0o700)
 def fetch(rel):
  assert rel and not rel.startswith('/') and '..' not in rel.split('/')
  url=d['origin']+rel
  with urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache','User-Agent':'Luma-native38-public-closure/1'}),timeout=60) as f:
   assert f.status==200 and f.geturl()==url;b=f.read(32*1024**2+1);assert len(b)<=32*1024**2
  return b
 def write(q,b):
  q.parent.mkdir(parents=True,exist_ok=True)
  with q.open('xb') as f:f.write(b);f.flush();os.fsync(f.fileno())
 for rel,h in d['repository_metadata'].items():assert hashlib.sha256(fetch(rel)).hexdigest()==h
 key=a.out/'luma-depot.gpg';body=fetch('keys/luma-depot.gpg');assert hashlib.sha256(body).hexdigest()==d['public_key_sha256'];write(key,body)
 assert a.cache.is_absolute() and not a.cache.is_symlink();a.cache.mkdir(mode=0o700,exist_ok=True)
 assert a.cache.stat().st_uid==os.getuid() and a.cache.stat().st_mode&0o777==0o700
 marker=a.cache/'PROFILE-SHA256'
 if marker.exists():assert marker.is_file() and not marker.is_symlink() and marker.read_text()==a.profile_sha256+'\n'
 else:
  assert not list(a.cache.iterdir());write(marker,(a.profile_sha256+'\n').encode())
 repo=a.cache/'repo'
 if not repo.exists():
  subprocess.run(['ostree','init','--repo='+str(repo),'--mode=archive'],check=True,timeout=60)
  subprocess.run(['ostree','remote','add','--repo='+str(repo),'--gpg-import='+str(key),'--set=gpg-verify=true','--set=gpg-verify-summary=true','canonical',d['origin']+'repo'],check=True,timeout=60)
 assert repo.is_dir() and not repo.is_symlink() and repo.stat().st_uid==os.getuid()
 cfg=configparser.ConfigParser();cfg.read(repo/'config');section='remote "canonical"'
 assert cfg.get(section,'url')==d['origin']+'repo' and cfg.get(section,'gpg-verify')=='true' and cfg.get(section,'gpg-verify-summary')=='true'
 summary=subprocess.check_output(['ostree','remote','summary','--repo='+str(repo),'canonical'],text=True,timeout=120)
 assert len(summary)<1024**2 and 'Verification enabled' in summary and 'Good signature from "Luma Depot' in summary
 rows=re.findall(r'^\* ((?:app|runtime)/[^\s]+)\n\s+Latest Commit[^\n]*\n\s+([0-9a-f]{64})$',summary,re.M)
 assert len(rows)==len(set(r for r,h in rows))==76 and dict(rows)==d['all_heads'];write(a.out/'summary.txt',summary.encode())
 with (a.out/'pull.log').open('x') as f:subprocess.run(['ostree','pull','--repo='+str(repo),'--depth=0','canonical',*sorted(d['closure_refs'])],stdout=f,stderr=subprocess.STDOUT,check=True,timeout=10800)
 # Audit complete selected payloads before adding legacy metadata-only refs.
 with (a.out/'public-fsck.log').open('x') as f:subprocess.run(['ostree','--repo='+str(repo),'fsck'],stdout=f,stderr=subprocess.STDOUT,check=True,timeout=3600)
 extra=sorted(set(d['all_heads'])-set(d['closure_refs']))
 with (a.out/'legacy-metadata.log').open('x') as f:subprocess.run(['ostree','pull','--repo='+str(repo),'--commit-metadata-only','canonical',*extra],stdout=f,stderr=subprocess.STDOUT,check=True,timeout=1800)
 for ref,h in d['all_heads'].items():
  assert subprocess.check_output(['ostree','--repo='+str(repo),'rev-parse','canonical:'+ref],text=True,timeout=30).strip()==h
  text=subprocess.check_output(['ostree','--repo='+str(repo),'show','--gpg-verify-remote=canonical',h],text=True,timeout=30);assert 'Good signature from "Luma Depot' in text
 verified={}
 for rel,h in d['public_files'].items():
  b=fetch(rel);assert hashlib.sha256(b).hexdigest()==h;write(a.out/'site'/rel,b);verified[rel]=h
 subprocess.run(['minisign','-Vm',str(a.out/'site/catalog/catalog-4.json'),'-p',str(a.out/'site/keys/depot-catalog.pub')],check=True,timeout=30)
 # Source profile pins the retirement-aware signed catalogue and all retained
 # legacy documents, not a hardcoded discovery count or old seven-app delta.
 catalog=json.loads((a.out/'site/catalog/catalog-4.json').read_bytes());assert 'org.projectluma.Displays' not in json.dumps(catalog)
 for rel,h in d['repository_metadata'].items():assert hashlib.sha256(fetch(rel)).hexdigest()==h
 receipt=dict(repository_metadata=d['repository_metadata'],result='PASS_PUBLIC_SIGNED_CPU_UNION_CLOSURES_AND_METADATA',profile_sha256=a.profile_sha256,final_union_sha256=d['final_union_sha256'],repository_url=d['origin']+'repo',all_heads=d['all_heads'],complete_closure_refs=d['closure_refs'],summary_signature_verified=True,all_commit_signatures_verified=True,normal_depth_zero_pull=True,downloaded_repository_fsck_exit_status=0,catalogue_signature_verified=True,public_files=verified,publication_performed=False)
 write(a.out/'PASS.json',(json.dumps(receipt,indent=2)+'\n').encode());print(json.dumps(receipt),flush=True)
if __name__=='__main__':main()
