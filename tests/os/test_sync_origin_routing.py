# SPDX-License-Identifier: Apache-2.0
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'scripts/os/lib/sync-origin.sh'

class OriginRouting(unittest.TestCase):
 def fixture(self, destination='os/repo', link='', parts='repo graph'):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name)
  generation=root/'generation';repo=generation/'os/repo';(repo/'objects').mkdir(parents=True)
  (repo/'config').write_text('[core]\nmode=archive-z2\n');(repo/'summary').write_text('signed summary');(repo/'summary.sig').write_text('signature')
  graph=generation/'os/graph'
  for name in ('nightly.json','nightly.json.minisig','x86_64/nightly.json','aarch64/nightly.json','aarch64/fairphone-fp6/nightly.json'):
   path=graph/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('signed '+name)
  bins=root/'bin';bins.mkdir();log=root/'calls.jsonl'
  helper='''#!/usr/bin/python3
import json,os,pathlib,sys,urllib.parse
name=pathlib.Path(sys.argv[0]).name
with open(os.environ['CALL_LOG'],'a') as f:f.write(json.dumps([name,*sys.argv[1:]])+'\\n')
if name=='curl':
 data=sys.stdin.read();url=data.split('url = "',1)[1].split('"',1)[0]
 path=pathlib.Path(os.environ['GENERATION'])/urllib.parse.urlsplit(url).path.lstrip('/')
 if not path.is_file():sys.exit(22)
 output=sys.argv[sys.argv.index('--output')+1]
 if output!='/dev/null':pathlib.Path(output).write_bytes(path.read_bytes())
'''
  for name in ('rsync','curl','ostree'):
   p=bins/name;p.write_text(helper);p.chmod(0o755)
  env=os.environ|{'PATH':str(bins)+':'+os.environ['PATH'],'CALL_LOG':str(log),'GENERATION':str(generation),'DL_RSYNC_DEST':'publisher@example.invalid','DL_SSH_KEY':'/private/key','KNOWN_HOSTS':'/private/known','DL_HTTP_ORIGIN':'https://example.invalid','DL_HOST_HEADER':'example.invalid','DL_PUBLIC_REPO_DEST':destination,'DL_PUBLIC_REPO_LINK_DEST':link}
  result=subprocess.run(['bash',str(SCRIPT),str(generation),'1','0',parts,''],env=env,capture_output=True,text=True,timeout=20)
  calls=[json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
  return result,calls
 def test_explicit_physical_preview_keeps_client_public_url_and_all_graph_files(self):
  result,calls=self.fixture('os/preview-repo','/os/repo');self.assertEqual(result.returncode,0,result.stderr)
  transfers=[row for row in calls if row[0]=='rsync']
  self.assertEqual([row[-1] for row in transfers[:3]],['publisher@example.invalid:os/preview-repo/']*3)
  self.assertIn('--link-dest=/os/repo',transfers[0]);self.assertNotIn('--link-dest=/os/repo',transfers[-1])
  # Mock HTTP serves only canonical os/repo and actual graph files: an internal
  # preview URL or directory-as-file probe would fail the unchanged verifier.
  self.assertIn('origin serves what was published',result.stdout)
  self.assertEqual(len([row for row in calls if row[0]=='curl']),7)
 def test_colliding_public_and_private_sources_fail_before_upload(self):
  result,calls=self.fixture('os/preview-repo',parts='repo preview graph');self.assertEqual(result.returncode,2);self.assertEqual(calls,[])
 def test_default_public_destination_is_preserved(self):
  result,calls=self.fixture();self.assertEqual(result.returncode,0,result.stderr)
  self.assertEqual([row[-1] for row in calls if row[0]=='rsync'][:3],['publisher@example.invalid:os/repo/']*3)
 def test_unreviewed_route_fails_before_upload(self):
  for destination,link in [('os/other',''),('os/repo','/private'),('os/preview-repo','/outside')]:
   with self.subTest(destination=destination,link=link):
    result,calls=self.fixture(destination,link);self.assertEqual(result.returncode,2);self.assertEqual(calls,[])
 def test_container_boundary_forwards_both_selected_routing_values(self):
  source=(ROOT/'scripts/os/sync-to-cdn.sh').read_text()
  block=source[source.index('    luma_os_tools env DL_RSYNC_DEST='):source.index('      <"$luma_os_repo_root/scripts/os/lib/sync-origin.sh"')]
  command='luma_os_tools() { printf "%s\\0" "$@"; }; '+block.replace('\\\n',' ')+'\n'
  env=os.environ|{'DL_RSYNC_DEST':'publisher','DL_SSH_KEY':'key','DL_HTTP_ORIGIN':'https://example','DL_HOST_HEADER':'example','DL_PUBLIC_REPO_DEST':'os/preview-repo','DL_PUBLIC_REPO_LINK_DEST':'/os/repo'}
  args=subprocess.check_output(['bash','-c',command],env=env).decode().split('\0')
  self.assertIn('DL_PUBLIC_REPO_DEST=os/preview-repo',args);self.assertIn('DL_PUBLIC_REPO_LINK_DEST=/os/repo',args)

if __name__=='__main__':unittest.main()
