# SPDX-License-Identifier: Apache-2.0
"""Exercise the sandbox payload described by the shipping Connect recipe."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE=Path(__file__).resolve().parents[1]
ROOT=SOURCE.parents[1]
class ConnectPackagedContract(unittest.TestCase):
    def test_code_entry_reaches_bus_with_packaged_modules(self):
        recipe=ROOT/'packaging/flatpak/apps/org.projectluma.Connect/org.projectluma.Connect.yml'
        if not recipe.is_file():
            recipe=SOURCE/'tests/fixtures/org.projectluma.Connect.yml'
        commands=json.loads(recipe.read_text())['modules'][0]['build-commands']
        with tempfile.TemporaryDirectory() as work:
            site=Path(work)/'site'
            for command in commands:
                parts=shlex.split(command)
                if len(parts)==4 and parts[:2]==['install','-Dm644'] and parts[2].endswith('.py'):
                    source=ROOT/parts[2]
                    if not source.is_file():
                        source=SOURCE/Path(parts[2]).relative_to('src/luma-continuity')
                    destination=site/Path(parts[3]).relative_to('/app/lib/python3.14/site-packages')
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(source,destination)
            # Use only assembled app modules, not the source package or host broker.
            result=subprocess.run([sys.executable,'-I','-c', '''
import os,sys
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from luma_continuity.cloud_sync import CloudSync
from luma_continuity.connect_cloud_contract import command_plan
from gi.repository import Gio,GLib
os.environ['FLATPAK_ID']='org.projectluma.Connect'
started=[];problems=[]
with patch.object(Gio,'bus_get',side_effect=lambda *args: started.append(args)), \\
     patch.object(GLib,'idle_add',side_effect=lambda fn,*args:fn(*args)):
    for code in ('ABCD EFGH','ABCDEFGH'):
        CloudSync().connect(code,'Laptop',problems.append)
assert len(started)==2, (len(started),problems)
assert not problems,problems
assert command_plan({'operation':'connect','values':{'code':'ABCDEFGH','name':'Laptop'}})[0][4]=='ABCDEFGH'
try:command_plan({'operation':'connect','values':{'code':'ABCDEFGH','name':'Laptop','hub':'https://evil.invalid'}})
except ValueError:pass
else:raise AssertionError('Caller-selected server accepted')
''',str(site)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr+result.stdout)

if __name__=='__main__':unittest.main()
