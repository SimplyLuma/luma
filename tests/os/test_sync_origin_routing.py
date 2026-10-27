# SPDX-License-Identifier: Apache-2.0
import json
import http.server
import ssl
import threading
import shutil
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
 print('200')
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
 @unittest.skipUnless(shutil.which('curl') and shutil.which('openssl'),'real HTTPS fixture needs curl and openssl')
 def test_real_https_redirect_full_body_head_downgrade_and_virtual_host(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);key=root/'key.pem';cert=root/'cert.pem';calls=root/'curl-calls.jsonl';errors=root/'curl-errors.log'
   subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1','-keyout',str(key),'-out',str(cert)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
   class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def respond(self,head=False):
     port=self.server.server_port
     if self.path in ('/good','/bad','/downgrade'):
      self.send_response(302)
      suffix='/missing' if self.path=='/bad' else '/payload'
      scheme='http' if self.path=='/downgrade' else 'https'
      self.send_header('Location',f'{scheme}://localhost:{port}'+suffix);self.send_header('Content-Length','0');self.end_headers();return
     if self.path=='/missing':self.send_response(404);self.send_header('Content-Length','0');self.end_headers();return
     self.send_response(200);self.send_header('Content-Length',str(len(b'exact signed object body')));self.end_headers()
     if not head:self.wfile.write(b'exact signed object body')
    def do_GET(self):self.respond()
    def do_HEAD(self):self.respond(True)
   server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
   context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key);server.socket=context.wrap_socket(server.socket,server_side=True)
   thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   try:
    bins=root/'bin';bins.mkdir();wrapper=bins/'curl'
    wrapper.write_text('#!/usr/bin/python3\nimport os,json,sys\nwith open(os.environ["CURL_CALLS"],"a") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\nfd=os.open(os.environ["CURL_ERRORS"],os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600);os.dup2(fd,2)\nos.execv("/usr/bin/curl",["curl","--show-error","--noproxy","*","--cacert",os.environ["TEST_CA"],*sys.argv[1:]])\n');wrapper.chmod(0o755)
    source=SCRIPT.read_text();block=source[source.index('request() {'):source.index('digest() {')]
    env=os.environ|{'PATH':str(bins)+':'+os.environ['PATH'],'CURL_CALLS':str(calls),'CURL_ERRORS':str(errors),'TEST_CA':str(cert),'DL_HTTP_ORIGIN':f'https://127.0.0.1:{server.server_port}','DL_HOST_HEADER':f'localhost:{server.server_port}'}
    target=root/'download';command=block+'\nfetch good "$1" && present good && ! present bad && ! fetch downgrade "$2"'
    result=subprocess.run(['bash','-c',command,'https-fixture',str(target),str(root/'downgrade')],env=env,capture_output=True,text=True,timeout=20)
    self.assertEqual(result.returncode,0,result.stderr+' CALLS '+(calls.read_text() if calls.exists() else 'NONE')+' ERRORS '+(errors.read_text() if errors.exists() else 'NONE'));self.assertEqual(target.read_bytes(),b'exact signed object body')
    actual=[json.loads(line) for line in calls.read_text().splitlines()]
    self.assertIn('-H',actual[0]);self.assertNotIn('-H',actual[1])
   finally:server.shutdown();server.server_close();thread.join(timeout=3)
 def test_container_boundary_forwards_both_selected_routing_values(self):
  source=(ROOT/'scripts/os/sync-to-cdn.sh').read_text()
  block=source[source.index('    luma_os_tools env DL_RSYNC_DEST='):source.index('      <"$luma_os_repo_root/scripts/os/lib/sync-origin.sh"')]
  command='luma_os_tools() { printf "%s\\0" "$@"; }; '+block.replace('\\\n',' ')+'\n'
  env=os.environ|{'DL_RSYNC_DEST':'publisher','DL_SSH_KEY':'key','DL_HTTP_ORIGIN':'https://example','DL_HOST_HEADER':'example','DL_PUBLIC_REPO_DEST':'os/preview-repo','DL_PUBLIC_REPO_LINK_DEST':'/os/repo'}
  args=subprocess.check_output(['bash','-c',command],env=env).decode().split('\0')
  self.assertIn('DL_PUBLIC_REPO_DEST=os/preview-repo',args);self.assertIn('DL_PUBLIC_REPO_LINK_DEST=/os/repo',args)

if __name__=='__main__':unittest.main()
