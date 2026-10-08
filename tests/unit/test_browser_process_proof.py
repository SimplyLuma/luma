# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import unittest

REPO=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('browser_process_proof',REPO/'tests/os/gate/browser-process-proof.py')
proof=importlib.util.module_from_spec(spec);spec.loader.exec_module(proof)

class BrowserProcessProof(unittest.TestCase):
    def setUp(self):
        self.ref='app/com.rhyme.viola/x86_64/beta'
        self.commit='a'*64
        self.url='https://gate.invalid/unique-gate-url'
        self.record={'name':'com.rhyme.viola','flatpak_id':'com.rhyme.viola',
          'exe':'/usr/bin/python3.14','argv':['/usr/bin/python3','/app/share/viola-browser/luma-host/integrated_window.py','--interactive',self.url],
          'app_path':'/var/lib/flatpak/app/com.rhyme.viola/x86_64/beta/'+self.commit+'/files'}
    def test_complete_observed_browser_record(self):
        proof.verify(self.record,self.ref,self.commit,self.url)
    def test_flatpak_launcher_is_not_a_browser_window(self):
        self.record['exe']='/usr/bin/flatpak'
        self.record['argv']=['flatpak','run',self.ref,self.url]
        with self.assertRaises(ValueError):proof.verify(self.record,self.ref,self.commit,self.url)
    def test_wrong_owner_ref_rejected(self):
        self.record['name']='org.projectluma.Viewer'
        with self.assertRaises(ValueError):proof.verify(self.record,self.ref,self.commit,self.url)
    def test_wrong_commit_rejected(self):
        self.record['app_path']=self.record['app_path'].replace(self.commit,'b'*64)
        with self.assertRaises(ValueError):proof.verify(self.record,self.ref,self.commit,self.url)
    def test_missing_or_different_url_rejected(self):
        self.record['argv'][-1]='https://gate.invalid/another-url'
        with self.assertRaises(ValueError):proof.verify(self.record,self.ref,self.commit,self.url)
    def test_foreign_process_using_python_name_rejected(self):
        self.record['argv'][1]='/app/bin/other.py'
        with self.assertRaises(ValueError):proof.verify(self.record,self.ref,self.commit,self.url)
