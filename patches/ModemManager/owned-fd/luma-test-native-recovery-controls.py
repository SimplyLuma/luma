#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile native recovery controls against this build's actual production bodies."""
import re,sys,subprocess,shlex,tempfile
from pathlib import Path
source=Path(sys.argv[1]).read_text()
scaffold=Path(sys.argv[2]).read_text()
def function(name):
 m=re.search(r'static [^;{}]+\b'+name+r'\s*\([^;{}]*\)\s*\{',source)
 if not m:raise ValueError('missing production function '+name)
 i=m.end();depth=1
 while depth:
  depth+=(source[i]=='{')-(source[i]=='}');i+=1
 return source[m.start():i]
names=['luma_deactivation_free','luma_deactivation_fail','luma_deactivation_terminal_io','luma_deactivation_status_ready','luma_deactivation_off_ready','luma_deactivation_on_ready','luma_deactivation_dispatch']
a=source.index('typedef struct {');b=source.index('} LumaDeactivationRecovery;',a)+len('} LumaDeactivationRecovery;')
body=scaffold.replace('@PRODUCTION_STRUCT@',source[a:b]).replace('@PRODUCTION_CALLBACKS@','\n'.join(function(n) for n in names))
assert '@PRODUCTION_' not in body
flags=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','gio-2.0'],text=True))
with tempfile.TemporaryDirectory(prefix='mm-native-recovery-check-') as directory:
 c=Path(directory)/'controls.c';exe=Path(directory)/'controls';c.write_text(body)
 subprocess.run(['cc','-Wall','-Wextra','-Werror',str(c),*flags,'-o',str(exe)],check=True,timeout=30)
 subprocess.run([str(exe)],check=True,timeout=20)
