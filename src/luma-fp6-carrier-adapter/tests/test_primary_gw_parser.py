# SPDX-License-Identifier: Apache-2.0
"""Exercise the genuine shell status parser only; no qmicli or hardware calls."""
import pathlib,re,subprocess,unittest
SCRIPT=pathlib.Path(__file__).resolve().parents[2]/'scripts/mobile/luma-fp6-primary-gw-session'
BODY=SCRIPT.read_text()
AWK=BODY.split("selection=$(awk "+chr(39)+"\n",1)[1].split(chr(39)+" <<<",1)[0]
AID='A0:00:00:00:87:10:02:FF'
def status(slot=1,kind='usim',aid=AID):
 return f"Slot [{slot}]:\n Application [0]:\n  Application type: '{kind}'\n  Application ID:\n   {aid}\n"
class Parser(unittest.TestCase):
 def test_selection(self):
  cases=[('physical',status(1),True),('esim',status(2),True),('isim_then_usim',status(1,'isim')+status(2),True),('two',status(1)+status(2),False),('none','Slot [1]:\n Card state: absent\n',False),('malformed',status(aid='not-hex'),False),('boundary',status(2)+status(1,'isim'),True),('unsupported',status(3),False)]
  for name,text,okay in cases:
   with self.subTest(name=name):
    p=subprocess.run(['awk',AWK],input=text.encode(),capture_output=True,timeout=3);self.assertEqual(p.returncode==0,okay);self.assertEqual(p.stderr,b'')
    if okay:self.assertRegex(p.stdout.decode(),r'^[12] A0000000871002FF\n$')
 def test_active_slots_are_preserved(self):
  pattern=r"Primary GW:[\t ]+slot '[12]', application '[0-9]+'"
  for slot in (1,2):self.assertRegex(f"Primary GW: slot '{slot}', application '0'",pattern)
  self.assertIn("state=already-active",BODY)
  self.assertLess(BODY.index('state=already-active'),BODY.index('--uim-change-provisioning-session='))
 def test_syntax(self):subprocess.run(['bash','-n',str(SCRIPT)],check=True,timeout=5)
if __name__=='__main__':unittest.main()
