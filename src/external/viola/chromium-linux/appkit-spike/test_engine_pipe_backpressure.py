"""A blocked command write must not prevent reading completed responses."""
import json,os,threading,time,unittest
from concurrent.futures import Future
from engine_pipe import EnginePipe
class Tests(unittest.TestCase):
 def test_response_drains_while_another_request_write_is_blocked(self):
  engine=EnginePipe.__new__(EnginePipe)
  command_read,engine.write_in=os.pipe();engine.read_out,response_write=os.pipe()
  engine.lock=threading.Lock();engine.write_lock=threading.Lock()
  engine.pending={1:Future()};earlier=engine.pending[1];engine.next_id=1
  engine.pipe_closed=False;engine.closing=True;engine.on_event=lambda _:None
  engine.on_closed=None;sent=[]
  reader=threading.Thread(target=engine._read);reader.start()
  writer=threading.Thread(target=lambda:sent.append(engine.request('Runtime.evaluate',{'expression':'x'*1000000})))
  writer.start()
  deadline=time.monotonic()+1
  while 2 not in engine.pending and time.monotonic()<deadline:time.sleep(.001)
  self.assertIn(2,engine.pending)
  # The one-megabyte command cannot fit in an unread pipe. Chromium can
  # nevertheless reply to an earlier command on its independent response pipe.
  os.write(response_write,b'{"id":1,"result":{"done":true}}\0')
  try:
   self.assertEqual(earlier.result(timeout=1),{'done':True})
  finally:
   # Always unblock the writer, even when testing the previous implementation.
   data=b''
   while not data.endswith(b'\0'):data+=os.read(command_read,65536)
   writer.join(2);os.close(response_write);reader.join(2)
   os.close(command_read);os.close(engine.write_in);os.close(engine.read_out)
  self.assertEqual(json.loads(data[:-1])['method'],'Runtime.evaluate')
 def test_failed_write_removes_pending_request(self):
  engine=EnginePipe.__new__(EnginePipe);read,engine.write_in=os.pipe();os.close(read)
  engine.lock=threading.Lock();engine.write_lock=threading.Lock();engine.pending={};engine.next_id=0;engine.pipe_closed=False
  try:
   with self.assertRaises(BrokenPipeError):engine.request('Runtime.evaluate')
   self.assertEqual(engine.pending,{})
  finally:os.close(engine.write_in)
 def test_concurrent_commands_remain_complete_packets(self):
  engine=EnginePipe.__new__(EnginePipe);read,engine.write_in=os.pipe()
  engine.lock=threading.Lock();engine.write_lock=threading.Lock();engine.pending={};engine.next_id=0;engine.pipe_closed=False
  packets=[]
  def drain():
   data=b''
   while len(packets)<2:
    data+=os.read(read,65536)
    while b'\0' in data:
     packet,data=data.split(b'\0',1);packets.append(json.loads(packet))
  reader=threading.Thread(target=drain);reader.start()
  writers=[threading.Thread(target=lambda value=value:engine.request('Runtime.evaluate',{'expression':value*100000})) for value in ('a','b')]
  for writer in writers:writer.start()
  for writer in writers:writer.join(2)
  reader.join(2);os.close(read);os.close(engine.write_in)
  self.assertEqual({p['params']['expression'] for p in packets},{'a'*100000,'b'*100000})
  self.assertEqual({p['id'] for p in packets},{1,2})
if __name__=='__main__':unittest.main()
