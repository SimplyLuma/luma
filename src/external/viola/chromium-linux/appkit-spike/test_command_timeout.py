"""Timeouts must neither destroy healthy sessions nor replay browser actions."""
import json,os,threading,unittest
from command_timeout import CommandTimeoutPolicy,EngineCommandTimeout
from engine_pipe import EnginePipe
class Tests(unittest.TestCase):
 def policy(self):
  self.alive=True;self.now=0;self.notices=[];self.records=[]
  return CommandTimeoutPolicy(lambda:self.alive,lambda:self.notices.append(1),self.records.append,lambda:self.now)
 def test_startup_and_dead_engine_and_other_failures_not_hidden(self):
  p=self.policy();self.assertFalse(p.handle(TimeoutError()))
  p.ready=True;self.assertFalse(p.handle(ValueError()))
  self.alive=False;self.assertFalse(p.handle(TimeoutError()))
  self.assertEqual(p.count,0)
 def test_runtime_timeout_retains_session_and_bounds_notices_and_evidence(self):
  p=self.policy();p.ready=True
  for i in range(40):self.assertTrue(p.handle(EngineCommandTimeout('Runtime.evaluate',15,[{'function':'test'}])))
  self.assertEqual(p.count,40);self.assertEqual(len(p.snapshot()['recent']),32);self.assertEqual(len(self.notices),1)
  self.now=61;self.assertTrue(p.handle(TimeoutError('not recorded')));self.assertEqual(len(self.notices),2)
  self.assertNotIn('not recorded',json.dumps(p.snapshot()))
 def test_late_reply_cannot_satisfy_next_request(self):
  e=EnginePipe.__new__(EnginePipe);read,e.write_in=os.pipe();e.read_out,write=os.pipe()
  e.lock=threading.Lock();e.write_lock=threading.Lock();e.pending={};e.next_id=0;e.pipe_closed=False;e.closing=True;e.on_event=lambda _:None;e.on_closed=None
  reader=threading.Thread(target=e._read);reader.start()
  try:
   with self.assertRaises(EngineCommandTimeout) as caught:e.call('Runtime.evaluate',{'expression':'PRIVATE TEXT'},timeout=.01)
   self.assertEqual(e.pending,{})
   self.assertNotIn('PRIVATE TEXT',str(caught.exception)+json.dumps(caught.exception.callers))
   first=json.loads(os.read(read,4096).rstrip(b'\0'));second=e.request('Browser.getVersion');next_packet=json.loads(os.read(read,4096).rstrip(b'\0'))
   os.write(write,json.dumps({'id':first['id'],'result':{'stale':True}}).encode()+b'\0')
   os.write(write,json.dumps({'id':next_packet['id'],'result':{'current':True}}).encode()+b'\0')
   self.assertEqual(second.result(1),{'current':True})
  finally:
   os.close(write);reader.join(2)
   for fd in (read,e.write_in,e.read_out):os.close(fd)
if __name__=='__main__':unittest.main()
