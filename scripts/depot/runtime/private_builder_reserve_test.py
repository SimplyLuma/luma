from pathlib import Path
import importlib.util, tempfile, subprocess
from unittest.mock import patch
import types, sys
p=Path(sys.argv[1]);spec=importlib.util.spec_from_file_location('reserve',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory(prefix='luma-private-reserve-') as d:
 root=Path(d);repo=root/'repo'
 try:m.configure(repo,m.MINIMUM-1)
 except ValueError:pass
 else:raise AssertionError('invalid reserve accepted')
 assert not repo.exists()
 with patch.object(m.os,'statvfs',return_value=types.SimpleNamespace(f_bavail=1,f_frsize=4096)):
  try:m.configure(repo,m.MINIMUM)
  except RuntimeError:pass
  else:raise AssertionError('low physical reserve accepted')
 assert not repo.exists()
 m.configure(repo,m.MINIMUM)
 # Exact maintained flatpak-module-tools _create_repo initializes this existing
 # unsigned repo again. Supported OSTree initialization must retain core policy.
 subprocess.run(['ostree','--repo='+str(repo),'init','--mode=archive'],check=True)
 m.verify(repo)
 try:m.configure(repo,m.MINIMUM)
 except ValueError:pass
 else:raise AssertionError('existing repository accepted')
 subprocess.run(['ostree','--repo='+str(repo),'config','set','core.min-free-space-percent','3'],check=True)
 try:m.verify(repo)
 except RuntimeError:pass
 else:raise AssertionError('mutated percentage accepted')
print('Private runtime reserve controls PASS: invalid/low/existing refused; actual OSTree re-init persists absolute policy; mutated percentage RED',flush=True)
