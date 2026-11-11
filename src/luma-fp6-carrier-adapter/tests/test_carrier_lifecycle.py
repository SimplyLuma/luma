# SPDX-License-Identifier: Apache-2.0
"""Execute full shell candidate with only external commands/physical paths projected."""
import os,pathlib,subprocess,tempfile,unittest,sys
HERE=pathlib.Path(__file__).parent
HELPER=pathlib.Path(sys.argv[1]) if len(sys.argv)>1 else HERE/'luma-fp6-carrier-pdc'
sys.argv=sys.argv[:1]
PLATFORM='00b4217c22737e3fc64ddea978a4218c4282769e'
TMO='88f589369ff3a066511dd1b21ed42cf3496686a3'
ATT='444954dcfbd6d5bf196cd8fecf6770ff3c6b34fc'
class Controls(unittest.TestCase):
 def invoke(self,slot='androidboot.slot_suffix=_b',profile='tmo-qrel1695',platform=True,software=TMO,extra_ids='',config=True,store=True,pending=False,selection=None):
  with tempfile.TemporaryDirectory() as td:
   r=pathlib.Path(td);bins=r/'bin';bins.mkdir();calls=r/'calls'
   def executable(name,body):
    p=bins/name;p.write_text('#!/bin/bash\n'+body);p.chmod(0o755);return p
   executable('id','echo 0\n');executable('stat','echo 0:0:644\n');executable('uname','echo 7.1.2-luma-fp-ims1.gmu2\n')
   restarted=r/'restarted'
   executable('systemctl','case "$1" in is-active) exit 0;; show) echo 123;; start) [ "$2" = luma-fp6-modem-restart.service ] || exit 87; printf restart >> '+str(calls)+'; touch '+str(restarted)+';; *) exit 88;; esac\n')
   qmi=executable('qmicli',"printf '%s\\n' \"$*\" >> "+str(calls)+"\ncase \"$*\" in *--dms-get-operating-mode*) exit 0;; *--pdc-list-configs=platform*) "+(("if [ -e "+str(restarted)+" ]; then s=Active; else s=Pending; fi; " if pending else "s=Active; ")+"printf \"Configuration 0\\n Status: $s\\n ID: "+PLATFORM+"\\n\"" if platform else "true")+";; *--pdc-activate-config=platform*) exit 0;; *--pdc-list-configs=software*) printf \"Configuration 0\\n Status: Active\\n ID: "+software+"\\n"+extra_ids+"\";; *) exit 89;; esac\n")
   files={'model':'The Fairphone (Gen. 6)\0','cmdline':slot+'\n','rmtfs-cmdline':'/usr/bin/rmtfs\0-o\0/var/lib/luma/rmtfs\0-s\0' if store else '/usr/bin/rmtfs\0-r\0-P\0-s\0'}
   if config:files['config']='PROFILE='+profile+'\n'
   if selection:files['selection']='{"apn": "", "mcfg": "'+selection+'", "state": "selected"}\n'
   for n,b in files.items():(r/n).write_text(b)
   body=HELPER.read_text()
   replacements={'/etc/luma/fp6-carrier-pdc.conf':r/'config','/run/luma/fp6-carrier.json':r/'selection','/usr/bin/qmicli':qmi,'/sys/firmware/devicetree/base/model':r/'model','/proc/cmdline':r/'cmdline','/proc/$(systemctl show -p MainPID --value rmtfs.service)/cmdline':r/'rmtfs-cmdline','/usr/lib/firmware/luma/fp6-carrier-pdc/att-volte-qrel1695.mbn':r/'missing-att','/usr/lib/firmware/luma/fp6-carrier-pdc/tmo-commercial-qrel1695.mbn':r/'missing-tmo','/usr/lib/firmware/luma/fp6-carrier-pdc/dsds-la-milos.mbn':r/'missing-platform'}
   for old,new in replacements.items():body=body.replace(old,str(new))
   helper=r/'helper';helper.write_text(body)
   x=subprocess.run(['bash',str(helper)],env=dict(os.environ,PATH=str(bins)+':/usr/bin:/bin'),capture_output=True,text=True,timeout=10)
   return x.returncode,x.stdout+x.stderr,calls.read_text() if calls.exists() else ''
 def test_active_without_external_files(self):
  for slot in ['_a','_b']:
   code,out,calls=self.invoke(slot='androidboot.slot_suffix='+slot);self.assertEqual(code,0,out);self.assertIn('state=already-active',out);self.assertNotIn('--pdc-load-config',calls);self.assertNotIn('--pdc-activate-config',calls)
 def test_wrong_or_ambiguous_slot(self):
  for slot in ['', 'androidboot.slot_suffix=_c','androidboot.slot_suffix=_a androidboot.slot_suffix=_b','androidboot.slot_suffix=_b androidboot.slot_suffix=_b']:
   code,out,calls=self.invoke(slot=slot);self.assertNotEqual(code,0);self.assertIn('reason=slot',out);self.assertEqual(calls,'')
 def test_missing_needed_platform(self):
  # Permanent: exit 3 so systemd does not restart it in a loop (RestartPreventExitStatus=3).
  code,out,calls=self.invoke(platform=False);self.assertEqual(code,3);self.assertIn('state=unavailable reason=platform_profile_missing',out);self.assertNotIn('--pdc-load-config',calls)
 def test_missing_needed_att(self):
  code,out,calls=self.invoke(profile='att-qrel1695');self.assertNotEqual(code,0);self.assertIn('reason=selected_profile_missing',out);self.assertNotIn('--pdc-load-config',calls)
 def test_missing_needed_tmo(self):
  code,out,calls=self.invoke(software=ATT);self.assertEqual(code,3,out);self.assertIn('reason=selected_profile_missing',out);self.assertNotIn('--pdc-load-config',calls)
 def test_tmo_as_android_provisioned_it_is_active(self):
  code,out,calls=self.invoke(software='4ea00db36530421c79794b4ba87686fa0a31f31b');self.assertEqual(code,0,out);self.assertIn('state=already-active profile=tmo-qrel1695',out);self.assertNotIn('--pdc-load-config',calls);self.assertNotIn('--pdc-activate-config=software',calls)
 def test_tmo_stock_identity_does_not_satisfy_att(self):
  code,out,calls=self.invoke(profile='att-qrel1695',software='4ea00db36530421c79794b4ba87686fa0a31f31b');self.assertNotEqual(code,0);self.assertIn('reason=selected_profile_missing',out)
 def test_unknown_software_refused(self):
  code,out,calls=self.invoke(software='deadbeef');self.assertNotEqual(code,0);self.assertIn('reason=unexpected_software',out)
 def test_without_profile_only_platform(self):
  code,out,calls=self.invoke(config=False);self.assertEqual(code,0,out);self.assertIn('state=platform-active profile=none',out);self.assertNotIn('software',calls)
 def test_read_only_backend_skips_without_profile(self):
  code,out,calls=self.invoke(config=False,store=False);self.assertEqual(code,0,out);self.assertIn('state=skipped reason=read_only_backend',out);self.assertEqual(calls,'')
 def test_read_only_backend_refused_with_profile(self):
  code,out,calls=self.invoke(store=False);self.assertNotEqual(code,0);self.assertIn('reason=rmtfs_backend',out);self.assertEqual(calls,'')
 def test_pending_platform_restarts_modem_then_active(self):
  code,out,calls=self.invoke(pending=True);self.assertEqual(code,0,out);self.assertIn('--pdc-activate-config=platform',calls);self.assertIn('restart',calls);self.assertIn('state=already-active',out)
 def test_selector_profile_without_etc(self):
  code,out,calls=self.invoke(config=False,selection='tmo-qrel1695');self.assertEqual(code,0,out);self.assertIn('state=already-active profile=tmo-qrel1695',out)
 def test_selector_factory_is_platform_only(self):
  code,out,calls=self.invoke(config=False,selection='factory');self.assertEqual(code,0,out);self.assertIn('state=platform-active profile=none',out)
 def test_selector_profile_skipped_on_read_only_backend(self):
  code,out,calls=self.invoke(config=False,store=False,selection='tmo-qrel1695');self.assertEqual(code,0,out);self.assertIn('state=skipped',out)
if __name__=='__main__':unittest.main()
