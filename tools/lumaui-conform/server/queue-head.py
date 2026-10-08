"""Choose the conform queue head while the caller holds queue.lock.

This preserves fewest-held-run and oldest-ticket ordering. Admission, load,
slot and nightly guards remain in conform-serve.sh.
"""
import collections,os,pathlib,re,sys
Q=re.compile(r'^\d+-(\d{8}-\d{6}-\d+)-([a-zA-Z0-9]+)-(\d+)$')
H=re.compile(r'^(\d{8}-\d{6}-\d+)\.([^.]+)\.(\d+)$')
def live(pid):
 try:os.kill(pid,0);return True
 except ProcessLookupError:return False
 except PermissionError:return True

def head(root):
 root=pathlib.Path(root);candidates=[];runs=set();counts=collections.Counter()
 for p in (root/'queue').iterdir():
  m=Q.fullmatch(p.name)
  if not m:continue
  if not live(int(m[3])):
   p.unlink(missing_ok=True);continue
  candidates.append((m[1],p.name));runs.add(m[1])
 for p in (root/'held').iterdir():
  m=H.fullmatch(p.name)
  if not m or m[1] not in runs:continue
  if live(int(m[3])):counts[m[1]]+=1
  else:p.unlink(missing_ok=True)
 return min(candidates,key=lambda x:(counts[x[0]],x[1]))[1] if candidates else ''
if __name__=='__main__':print(head(sys.argv[1]))
