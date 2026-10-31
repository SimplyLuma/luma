#!/usr/bin/python3
"""Bounded C stacks from only the fresh CI fixture. Never release acceptance."""
import json,os,subprocess,sys,time
from pathlib import Path
assert os.getuid()==0
source,out=map(Path,sys.argv[1:]);assert source.name=='out' and source.parent.name.startswith('luma-gate-browser-opens.') and source.parent.parent==Path('/tmp')
end=time.monotonic()+100
while not (source/'owned-host-pid.json').exists():
 if time.monotonic()>end:raise TimeoutError('Owned diagnostic host never started')
 time.sleep(.1)
pid=json.loads((source/'owned-host-pid.json').read_text())['pid'];assert type(pid)==int and pid>1
root=Path('/proc')/str(pid)
def identity(p):
 r=Path('/proc')/str(p);return (r.stat().st_uid,r.joinpath('stat').read_text().rsplit(')',1)[1].split()[19],r.joinpath('cmdline').read_bytes().split(b'\0')[:-1])
expected=identity(pid);assert expected[0]==1100 and expected[2][:2]==[b'/usr/bin/python3',b'/usr/share/viola-browser/luma-host/integrated_window.py']
assert b'/home/luma-gate-browser-opens/.local/share/viola-luma/profile' in expected[2]
parent=int(next(line.split()[1] for line in (root/'status').read_text().splitlines() if line.startswith('PPid:')))
compositor=identity(parent);assert compositor[0]==1100 and compositor[2][0]==b'gnome-shell'
assert b'--wayland-display=viola-qa-shm-r1' in compositor[2]
time.sleep(20)
for name,target,proof in [('host',pid,expected),('compositor',parent,compositor)]:
 assert identity(target)==proof
 with (out/(name+'-c-stacks.txt')).open('w') as output:
  result=subprocess.run(['timeout','--signal=TERM','--kill-after=3','15','gdb','-nx','-nh','-batch',
   '-iex','set auto-load off','-iex','set debuginfod enabled off','-ex','set print frame-arguments none',
   '-p',str(target),'-ex','thread apply all bt 24','-ex','detach','-ex','quit'],stdout=output,stderr=subprocess.STDOUT,timeout=20)
 assert identity(target)==proof
 print(json.dumps(dict(scope='OWNED_C_STACK_DIAGNOSTIC_NOT_ACCEPTANCE',target=name,pid=target,status=result.returncode)),flush=True)
