# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from luma_installer import app_baseline as baseline

class Inventory(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'baseline';self.root.mkdir()
        # Native qualification owns these temporary files; production still
        # requires root-owned paths. Signature/deployment is a separate real gate.
        self.original=baseline.protected
        def owned(path,*,directory=False):
            info=path.lstat()
            import stat
            if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                    or info.st_uid!=os.getuid() or info.st_mode&0o022):raise ValueError('unsafe fixture member')
            return info
        self.patch=patch.object(baseline,'protected',owned);self.patch.start();self.addCleanup(self.patch.stop)
        bundle=b'owned structural fixture, not a signed application'
        sha=hashlib.sha256(bundle).hexdigest();(self.root/(sha+'.flatpak')).write_bytes(bundle)
        rows=[]
        for kind,names,branch in [('runtime',baseline.RUNTIMES,'44'),('app',baseline.APPS,'beta')]:
            for name in sorted(names):rows.append({'ref':f'{kind}/{name}/x86_64/{branch}','commit':'a'*64,'sha256':sha,'bytes':len(bundle),'file':sha+'.flatpak'})
        key=b'owned key fixture';(self.root/'luma-depot.gpg').write_bytes(key)
        roles=(json.dumps({'schema':'org.projectluma.first-party-app-roles/v1','roles':{app:'signed-system-flatpak'for app in baseline.APPS}},sort_keys=True)+'\n').encode()
        (self.root/'roles.json').write_bytes(roles)
        self.doc={'schema':'org.projectluma.offline-app-baseline/v1','default_channel':'beta','architecture':'x86_64','bundles':rows,
                  'public_key_sha256':hashlib.sha256(key).hexdigest(),'role_contract_sha256':hashlib.sha256(roles).hexdigest()}
        self.save()
    def save(self): (self.root/'manifest.json').write_text(json.dumps(self.doc))
    def test_complete_os_owned_app_runtime_inventory(self):
        doc,rows,key=baseline.inventory(self.root)
        self.assertEqual(len(rows),len(baseline.APPS)+len(baseline.RUNTIMES));self.assertEqual(doc['default_channel'],'beta')
    def test_missing_app_duplicate_foreign_role_future_branch_and_path_refused(self):
        original=json.loads(json.dumps(self.doc))
        for change in ('missing','duplicate','foreign','nightly','path','architecture','role'):
            self.doc=json.loads(json.dumps(original));row=self.doc['bundles'][-1]
            if change=='missing':self.doc['bundles'].pop()
            elif change=='duplicate':self.doc['bundles'][-1]=dict(self.doc['bundles'][0])
            elif change=='foreign':row['ref']='app/com.evil.App/x86_64/beta'
            elif change=='nightly':row['ref']=row['ref'].replace('/beta','/nightly')
            elif change=='path':row['file']='../../owned.flatpak'
            elif change=='architecture':row['ref']=row['ref'].replace('x86_64','aarch64')
            else:self.doc['role_contract_sha256']='0'*64
            self.save()
            with self.subTest(change=change),self.assertRaises(ValueError):baseline.inventory(self.root)
    def test_exact_bundle_corruption_symlink_and_immutable_journal(self):
        row=self.doc['bundles'][0];bundle=self.root/row['file'];bundle.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):baseline.inventory(self.root)
        bundle.unlink();bundle.symlink_to(self.root/'luma-depot.gpg')
        with self.assertRaises(ValueError):baseline.inventory(self.root)
        journal=self.root/'state.json';baseline.write_state(journal,{'applications':{'org.projectluma.Notes':{'commit':'b'*64}},'result':'PENDING'})
        self.assertEqual(json.loads(journal.read_text())['result'],'PENDING');self.assertEqual(journal.stat().st_mode&0o777,0o600)
        baseline.write_state(journal,{'result':'PASS'});self.assertEqual(json.loads(journal.read_text()),{'result':'PASS'})
        self.assertFalse(list(self.root.glob('.seed-*')))

if __name__=='__main__':unittest.main()
