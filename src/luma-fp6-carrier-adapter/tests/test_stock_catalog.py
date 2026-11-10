# SPDX-License-Identifier: Apache-2.0
"""Full shell control: only physical files and external commands are projected."""
import os,pathlib,subprocess,tempfile,unittest,sys
HERE=pathlib.Path(__file__).parent
HELPER=pathlib.Path(sys.argv[1]) if len(sys.argv)>1 else HERE/"luma-fp6-carrier-pdc"
sys.argv=sys.argv[:1]
PLATFORM='00b4217c22737e3fc64ddea978a4218c4282769e'
TMO='88f589369ff3a066511dd1b21ed42cf3496686a3'
ATT='444954dcfbd6d5bf196cd8fecf6770ff3c6b34fc'
def activate(body,wanted):
 blocks=body.split('Configuration ')
 for i in range(1,len(blocks)):
  block=blocks[i]
  if 'ID:' not in block:continue
  compact=block.split('ID:',1)[1].splitlines()[0].strip().replace(':','').lower()
  import re
  blocks[i]=re.sub(r'Status:\s*(Active|Inactive|Pending)', 'Status:      '+('Active' if compact==wanted else 'Inactive'),block)
 return 'Configuration '.join(blocks)
class Controls(unittest.TestCase):
 def run_case(self,kind='warm',pending=False):
  with tempfile.TemporaryDirectory() as td:
   r=pathlib.Path(td);bins=r/'bin';bins.mkdir();calls=r/'calls'
   def exe(n,b):
    p=bins/n;p.write_text('#!/bin/bash\n'+b);p.chmod(0o755);return p
   exe('id','echo 0');exe('stat','echo 0:0:644');exe('uname','echo 7.1.2-luma-fp-ims1.gmu2')
   exe('systemctl','case "$1" in is-active) exit 0;; show) echo 123;; *) exit 88;; esac')
   p=(HERE/'stock-platform-catalog.txt').read_text();w=(HERE/'stock-software-catalog.txt').read_text()
   if kind in ['already','software-load','software-load-loses-stock','tmo-load']:p=activate(p,PLATFORM)
   if kind=='unknown-platform':p=p.replace('BB:3E:7E:AE:62:30:3E:55:1D:1F:72:53:1B:4D:7A:8B:4C:39:28:E3','FF:3E:7E:AE:62:30:3E:55:1D:1F:72:53:1B:4D:7A:8B:4C:39:28:E3')
   if kind=='duplicate':p=p.replace('BB:3E:7E:AE:62:30:3E:55:1D:1F:72:53:1B:4D:7A:8B:4C:39:28:E3','00:B4:21:7C:22:73:7E:3F:C6:4D:DE:A9:78:A4:21:8C:42:82:76:9E')
   if kind=='unknown-software':w=w.replace(TMO.upper(), 'F'*40) if ':' not in w else w.replace('88:F5:89:36:9F:F3:A0:66:51:1D:D1:B2:1E:D4:2C:F3:49:66:86:A3','FF:F5:89:36:9F:F3:A0:66:51:1D:D1:B2:1E:D4:2C:F3:49:66:86:A3')
   activated=activate(p,PLATFORM)
   if kind in ['load-needed','load-loses-stock']:
    blocks=p.split('Configuration ');p=blocks[0]+''.join('Configuration '+x for x in blocks[1:] if PLATFORM not in x.replace(':','').lower())
    if kind=='load-loses-stock':
     blocks=activated.split('Configuration ');activated=blocks[0]+''.join('Configuration '+x for x in blocks[1:] if 'bb3e7eae62303e551d1f72531b4d7a8b4c3928e3' not in x.replace(':','').lower())
    (r/'profile').write_text('explicitly modeled external exact-hash profile')
    exe('sha256sum','echo 281cf305a5bcd98b709a0446f9733d1859cb423a791b2af17ab9776ffeaf0400')
   wactivated=w
   if kind in ['software-load','software-load-loses-stock']:
    wactivated=activate(w,ATT)+'Configuration 26:\n Description: Existing supported external ATT profile\n Status: Active\n ID: '+ATT+'\n'
    if kind=='software-load-loses-stock':
     blocks=wactivated.split('Configuration ');wactivated=blocks[0]+''.join('Configuration '+x for x in blocks[2:])
    (r/'att-profile').write_text('explicitly modeled external exact-hash ATT profile')
    exe('sha256sum','echo 32e7f3d9059e63323f70cf113b1ecc040dc7e9c82238a1d5bde2b21ca2eb7d6e')
   if kind=='tmo-load':
    blocks=w.split('Configuration ');w=blocks[0]+''.join('Configuration '+x for x in blocks[1:] if TMO not in x.replace(':','').lower())
    wactivated=w+'Configuration 26:\n Description: Commercial-TMO\n Status: Active\n ID: '+TMO+'\n'
    (r/'tmo-profile').write_text('explicitly modeled external exact-hash TMO profile')
    exe('sha256sum','echo 817e5f33a18a176a14859184de88c60921318864dea994a845133bc841e3edda')
   (r/'platform').write_text(p);(r/'software').write_text(w);(r/'activated').write_text(activated);(r/'pending').write_text(p.replace('Status:      Inactive','Status:      Pending',1));(r/'software-activated').write_text(wactivated)
   q=exe('qmicli',"printf '%s\\n' \"$*\" >> "+str(calls)+"\ncase \"$*\" in *--dms-get-operating-mode*) exit 0;; *--pdc-list-configs=platform*) cat "+str(r/'platform')+";; *--pdc-list-configs=software*) cat "+str(r/'software')+";; *--pdc-load-config=software,*) exit 0;; *--pdc-activate-config=software,*) cp "+str(r/'software-activated')+" "+str(r/'software')+"; exit 255;; *--pdc-load-config=platform,*) exit 0;; *--pdc-activate-config=platform,*) "+('cp '+str(r/'pending')+' '+str(r/'platform') if pending=='shape' else 'true' if pending else 'cp '+str(r/'activated')+' '+str(r/'platform'))+"; exit 255;; *) exit 89;; esac")
   for n,v in {'config':'PROFILE='+('att-qrel1695' if kind.startswith('software-load') else 'tmo-qrel1695')+'\n','model':'The Fairphone (Gen. 6)\0','cmdline':'androidboot.slot_suffix=_b\n','rmtfs':'/usr/bin/rmtfs\0-o\0/var/lib/luma/rmtfs\0-s\0'}.items():(r/n).write_text(v)
   body=HELPER.read_text()
   for old,new in {'/etc/luma/fp6-carrier-pdc.conf':r/'config','/usr/bin/qmicli':q,'/sys/firmware/devicetree/base/model':r/'model','/proc/cmdline':r/'cmdline','/usr/lib/firmware/luma/fp6-carrier-pdc/dsds-la-milos.mbn':r/'profile','/usr/lib/firmware/luma/fp6-carrier-pdc/att-volte-qrel1695.mbn':r/'att-profile','/usr/lib/firmware/luma/fp6-carrier-pdc/tmo-commercial-qrel1695.mbn':r/'tmo-profile','/proc/$(systemctl show -p MainPID --value rmtfs.service)/cmdline':r/'rmtfs'}.items():body=body.replace(old,str(new))
   (r/'helper').write_text(body);x=subprocess.run(['bash',str(r/'helper')],env=dict(os.environ,PATH=str(bins)+':/usr/bin:/bin'),capture_output=True,text=True,timeout=10)
   return x.returncode,x.stdout+x.stderr,calls.read_text() if calls.exists() else ''
 def test_known_stock_warm_catalog_preserved(self):
  code,out,calls=self.run_case();self.assertEqual(code,0,out);self.assertEqual(calls.count('--pdc-activate-config=platform,'),1);self.assertNotIn('--pdc-load-config',calls);self.assertNotIn('--pdc-delete-config',calls)
 def test_already_active_no_mutations(self):
  code,out,calls=self.run_case('already');self.assertEqual(code,0,out);self.assertNotIn('--pdc-activate-config',calls)
 def test_activation_not_active_refused(self):
  code,out,calls=self.run_case(pending=True);self.assertNotEqual(code,0);self.assertIn('reason=platform_identity',out)
 def test_actual_pending_status_refused(self):
  # A Pending entry is tolerated in the listing, but the selected platform must end Active.
  code,out,calls=self.run_case(pending='shape');self.assertNotEqual(code,0);self.assertIn('reason=platform_identity',out)
 def test_load_needed_preserves_stock_union(self):
  code,out,calls=self.run_case('load-needed');self.assertEqual(code,0,out);self.assertEqual(calls.count('--pdc-load-config=platform,'),1);self.assertNotIn('--pdc-delete-config',calls)
 def test_load_cannot_drop_stock(self):
  code,out,calls=self.run_case('load-loses-stock');self.assertNotEqual(code,0);self.assertIn('reason=platform_catalog_changed',out)
 def test_software_load_preserves_stock_union(self):
  code,out,calls=self.run_case('software-load');self.assertEqual(code,0,out);self.assertEqual(calls.count('--pdc-load-config=software,'),1)
 def test_tmo_load_preserves_stock_union(self):
  code,out,calls=self.run_case('tmo-load');self.assertEqual(code,0,out);self.assertIn('--pdc-load-config=software,',calls);self.assertIn('tmo-profile',calls);self.assertIn('state=active profile=tmo-qrel1695',out)
 def test_software_load_cannot_drop_stock(self):
  code,out,calls=self.run_case('software-load-loses-stock');self.assertNotEqual(code,0);self.assertIn('reason=software_catalog_changed',out)
 def test_unknown_duplicate_catalog_refused(self):
  for kind in ['unknown-platform','duplicate','unknown-software']:
   with self.subTest(kind=kind):
    code,out,calls=self.run_case(kind);self.assertNotEqual(code,0,out)
    if kind!='unknown-software':self.assertNotIn('--pdc-activate-config',calls)
if __name__=='__main__':unittest.main()
