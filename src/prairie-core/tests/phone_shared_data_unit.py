# SPDX-License-Identifier: Apache-2.0
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from prairie_apps.phone_shared_data import directory
class SharedPhone(unittest.TestCase):
 def test_host_and_fixed_sandbox_family_default_and_custom_locations_match(self):
  for base in ('/home/owned/.local/share','/home/owned/custom data'):
   env={'HOME':'/home/owned','XDG_DATA_HOME':base}
   for family in ('prairie/phone','luma/phone'):
    target=directory(family,env)
    sandbox=dict(env,FLATPAK_ID='org.projectluma.Phone',XDG_DATA_HOME='/home/owned/.var/app/org.projectluma.Phone/data',HOST_XDG_DATA_HOME=base)
    mount='1 2 0:1 / '+str(target).replace(' ','\\040')+' rw - tmpfs tmpfs rw'
    with patch.object(Path,'read_text',return_value=mount):self.assertEqual(directory(family,sandbox),target)
   self.assertEqual(directory('prairie/phone',dict(env,FLATPAK_ID='org.projectluma.Notes')),Path(base)/'prairie/phone')
 def test_missing_permission_and_unknown_family_fail_before_creating_empty_profile(self):
  with tempfile.TemporaryDirectory() as home:
   env={'HOME':home,'FLATPAK_ID':'org.projectluma.Phone','HOST_XDG_DATA_HOME':home+'/host'}
   for family in ('prairie/phone','luma/phone'):
    with patch.object(Path,'read_text',return_value=''):
     with self.assertRaises(PermissionError):directory(family,env)
   self.assertFalse((Path(home)/'host').exists())
   with self.assertRaises(ValueError):directory('luma/connect',env)
if __name__=='__main__':unittest.main()
