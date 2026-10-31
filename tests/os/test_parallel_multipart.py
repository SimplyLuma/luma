# SPDX-License-Identifier: Apache-2.0
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('parallel_upload',ROOT/'scripts/os/lib/parallel_multipart.py')
upload=importlib.util.module_from_spec(spec);spec.loader.exec_module(upload)


class Backend:
    def __init__(self):
        self.lock=threading.Lock();self.active=0;self.peak=0;self.calls=[];self.parts={};self.fail=set();self.starts=0;self.completions=[];self.hook=None;self.completion_error=False
    def client(self):
        backend=self
        class Client:
            bucket='fixture'
            def call(self,method,key,query,body=None,extra_headers=None):
                if method=='PUT':
                    n=int(query['partNumber'])
                    with backend.lock:backend.active+=1;backend.peak=max(backend.peak,backend.active);backend.calls.append(n)
                    try:
                        time.sleep(.015 if n==1 else .005)
                        if n in backend.fail:raise ConnectionError('private remote detail not for logs')
                        tag='"'+hashlib.md5(body).hexdigest()+'"'
                        with backend.lock:backend.parts[n]=(tag,bytes(body))
                        if backend.hook:backend.hook(n)
                        return 200,{'ETag':tag},b''
                    finally:
                        with backend.lock:backend.active-=1
                if 'uploads' in query:
                    backend.starts+=1;return 200,{},b'<InitiateMultipartUploadResult><UploadId>fixture-private</UploadId></InitiateMultipartUploadResult>'
                if backend.completion_error:return 200,{},b'<Error><Code>InternalError</Code></Error>'
                rows=[(int(p.findtext('PartNumber')),p.findtext('ETag')) for p in ET.fromstring(body)]
                backend.completions.append(rows)
                return 200,{},b'<CompleteMultipartUploadResult><ETag>fixture-final</ETag></CompleteMultipartUploadResult>'
        return Client()


class ParallelUpload(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.source=self.root/'image.iso';self.source.write_bytes(b''.join(bytes([n])*1024 for n in range(8)));self.sha=upload.sha256(self.source);self.state=self.root/'state';self.backend=Backend();self.lines=[]
    def run_upload(self,**kwargs):
        return upload.multipart_upload(self.backend.client(),'media/fixture.iso',self.source,1024,'application/x-iso9660-image',client_factory=self.backend.client,expected_sha256=self.sha,state_dir=self.state,attempts=1,pause=lambda n:None,progress=lambda s,**k:self.lines.append(s),**kwargs)
    def checkpoint(self):return next(self.state.glob('*.json'))
    def test_parallel_out_of_order_parts_complete_in_order_with_newline_progress(self):
        self.assertEqual(self.run_upload(),8192);self.assertGreater(self.backend.peak,1);self.assertLessEqual(self.backend.peak,4)
        self.assertEqual(self.backend.completions[0],[(n,self.backend.parts[n][0]) for n in range(1,9)])
        self.assertEqual(b''.join(self.backend.parts[n][1] for n in range(1,9)),self.source.read_bytes());self.assertEqual(len(self.lines),8);self.assertTrue(all('fixture-private' not in line for line in self.lines));self.assertFalse(list(self.state.glob('*.json')))
    def test_failed_part_preserves_accepted_etags_and_resumes_without_reupload(self):
        self.backend.fail={2}
        with self.assertRaisesRegex(RuntimeError,'durably checkpointed'):self.run_upload()
        state=json.loads(self.checkpoint().read_text());accepted=dict(state['parts']);self.assertTrue(accepted);self.assertNotIn(2,accepted);self.assertEqual(self.checkpoint().stat().st_mode&0o777,0o600);self.assertFalse(self.backend.completions)
        before=list(self.backend.calls);self.backend.fail.clear();self.run_upload();newcalls=self.backend.calls[len(before):]
        self.assertFalse(set(accepted)&set(newcalls));self.assertEqual(self.backend.starts,1)
        self.assertTrue(all(dict(self.backend.completions[0])[n]==tag for n,tag in accepted.items()))
    def test_changed_source_and_part_layout_refused_without_destroying_state(self):
        self.backend.fail={2}
        with self.assertRaises(RuntimeError):self.run_upload()
        state=self.checkpoint();original=state.read_bytes();self.source.write_bytes(b'x'*8192)
        with self.assertRaisesRegex(ValueError,'source changed'):self.run_upload()
        self.assertEqual(state.read_bytes(),original)
        self.source.write_bytes(b''.join(bytes([n])*1024 for n in range(8)));doc=json.loads(original);doc['part_size']=2048;state.write_text(json.dumps(doc));before=state.read_bytes()
        with self.assertRaisesRegex(ValueError,'part layout'):self.run_upload()
        self.assertEqual(state.read_bytes(),before)
    def test_legacy_checkpoint_never_silently_upgraded_or_discarded(self):
        self.state.mkdir(mode=0o700);name=hashlib.sha256(b'fixture/media/fixture.iso').hexdigest()[:32]+'.json';state=self.state/name;state.write_text(json.dumps(dict(key='media/fixture.iso',size=8192,mtime=int(self.source.stat().st_mtime),upload_id='legacy-private',parts=[[1,'etag']])));state.chmod(0o600);before=state.read_bytes()
        with self.assertRaisesRegex(ValueError,'legacy checkpoint preserved'):self.run_upload()
        self.assertEqual(state.read_bytes(),before);self.assertEqual(self.backend.starts,0)
    def test_failed_atomic_checkpoint_keeps_valid_resumable_json_and_no_completion(self):
        original=upload.os.replace;calls=[]
        def replace(a,b):
            calls.append(1)
            if len(calls)==2:raise OSError('simulated checkpoint replacement failure')
            return original(a,b)
        with patch.object(upload.os,'replace',replace):
            with self.assertRaises(RuntimeError):self.run_upload()
        state=json.loads(self.checkpoint().read_text());self.assertEqual(state['source_sha256'],self.sha);self.assertFalse(list(self.state.glob('*.tmp')));self.assertFalse(self.backend.completions)
        self.run_upload();self.assertEqual(self.backend.starts,1)
    def test_source_mutation_and_server_completion_error_never_clear_checkpoint(self):
        self.backend.hook=lambda n:self.source.write_bytes(b'x'*8192) if n==1 else None
        with self.assertRaises((RuntimeError,ValueError)):self.run_upload()
        self.assertFalse(self.backend.completions);self.assertTrue(self.checkpoint().exists())
        self.backend.hook=None;self.source.write_bytes(b''.join(bytes([n])*1024 for n in range(8)));self.backend.completion_error=True
        with self.assertRaisesRegex(ValueError,'completion was not accepted'):self.run_upload()
        self.assertTrue(self.checkpoint().exists());self.assertFalse(self.backend.completions)
        self.backend.completion_error=False;self.run_upload();self.assertEqual(self.backend.starts,1)


if __name__=='__main__':unittest.main(verbosity=2)
