# SPDX-License-Identifier: Apache-2.0
"""Exact Office extension boundary controls; actual crypto/mount positive is separate."""
import configparser
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
from luma_installer import app_extensions as subject
from luma_installer.app_data_migration import MigrationError

APP = 'org.projectluma.Write'
COMMIT = 'a' * 64

class Declarations(unittest.TestCase):
    def metadata(self, **values):
        parser = configparser.ConfigParser(interpolation=None)
        parser['Application'] = {'name': APP}
        parser['Extension ' + subject.OFFICE] = dict(directory='lib/office', version='44', **{'add-ld-path':'lib64'})
        parser['Extension ' + subject.OFFICE].update(values)
        return parser
    def test_exact_supported_ids_and_optional_own_locale(self):
        raw = subject.OFFICE + '=' + COMMIT
        self.assertEqual(subject._extensions(raw, APP), {subject.OFFICE:COMMIT})
        self.assertEqual(len(subject._extensions(raw + ';' + APP + '.Locale=' + 'b'*64 + ';', APP)), 2)
        self.assertEqual(subject._extensions('', 'org.projectluma.Notes'), {})
    def test_missing_local_unknown_duplicate_or_malformed_extensions_refused(self):
        raw = subject.OFFICE + '=' + COMMIT
        for value in ('', raw+';;', raw+';'+raw, subject.OFFICE+'=local', raw+'\n',
                      'org.example.Engine='+COMMIT, APP+'.Locale='+COMMIT,
                      raw+';'+'org.projectluma.Grid.Locale='+COMMIT, 'x'*4097):
            with self.subTest(value=value), self.assertRaises(MigrationError): subject._extensions(value, APP)
        with self.assertRaises(MigrationError): subject._extensions(raw, 'org.projectluma.Notes')
    def test_only_exact_office_directory_branch_and_loader_policy(self):
        self.assertEqual(subject._declaration(self.metadata(), APP, subject.OFFICE, 'beta'), ('lib/office','44'))
        self.assertEqual(subject._declaration(self.metadata(**{'autodelete':'false','no-autodownload':'false'}), APP, subject.OFFICE, 'beta'), ('lib/office','44'))
        for values in ({'directory':'../office'}, {'version':'beta'}, {'add-ld-path':'/usr/lib64'},
                       {'autodelete':'true'}, {'subdirectories':'true'}, {'no-autodownload':'true'}):
            with self.subTest(values=values), self.assertRaises(MigrationError): subject._declaration(self.metadata(**values), APP, subject.OFFICE, 'beta')
        with self.assertRaises(MigrationError): subject._declaration(self.metadata(), 'org.projectluma.Grid', subject.OFFICE, 'beta')
    def test_own_locale_requires_fixed_split_declaration(self):
        metadata=self.metadata()
        name=APP+'.Locale'
        metadata['Extension '+name]={'directory':'share/runtime/locale','autodelete':'true','locale-subset':'true'}
        self.assertEqual(subject._declaration(metadata, APP, name, 'beta'), ('share/runtime/locale','beta'))
        for key,value in [('directory','/usr/share/locale'),('version','stable'),('subdirectories','true'),('locale-subset','false')]:
            with self.subTest(key=key):
                changed=self.metadata();changed['Extension '+name]=dict(metadata['Extension '+name]);changed['Extension '+name][key]=value
                with self.assertRaises(MigrationError): subject._declaration(changed, APP, name, 'beta')

class RuntimeMount(unittest.TestCase):
    def setUp(self):
        self.base=Path(tempfile.mkdtemp(prefix='luma-office-boundary-',dir=Path.home()))
        self.deployed=self.base/'runtime'/subject.OFFICE/'x86_64'/'44'/COMMIT
        self.files=self.deployed/'files';self.files.mkdir(parents=True)
        self.ref=SimpleNamespace(format_ref=lambda:f'runtime/{subject.OFFICE}/x86_64/44',get_origin=lambda:'luma',get_name=lambda:subject.OFFICE,get_arch=lambda:'x86_64',get_branch=lambda:'44',get_commit=lambda:COMMIT,get_deploy_dir=lambda:str(self.deployed))
        self.installation=SimpleNamespace(list_installed_refs=lambda _: [self.ref],get_remote_by_name=lambda *_: SimpleNamespace(get_url=lambda:'https://dl.simplyluma.com/repo/',get_disabled=lambda:False,get_gpg_verify=lambda:True),get_path=lambda:SimpleNamespace(get_path=lambda:str(self.base)))
        self.original_lstat=Path.lstat
        self.mounted=Path('/proc/123/root/app/lib/office')
    def tearDown(self): shutil.rmtree(self.base)
    def call(self):
        def mounted(path,*args,**kwargs):
            return self.original_lstat(self.files) if path==self.mounted else self.original_lstat(path,*args,**kwargs)
        with patch('luma_installer.depot_flatpak.validate_remote') as remote, patch('luma_installer.native_app_roles.verify_deployed_commit') as crypto, patch.object(Path,'lstat',mounted):
            subject._runtime_mount(self.installation,subject.OFFICE,'x86_64','44',COMMIT,123,'lib/office')
        return remote,crypto
    def test_exact_runtime_identity_uses_existing_remote_crypto_and_ref_binding(self):
        remote,crypto=self.call()
        self.assertEqual(remote.call_args.args[1], 'luma')
        crypto.assert_called_once_with(self.installation,self.ref,commit=COMMIT,exact_ref=f'runtime/{subject.OFFICE}/x86_64/44')
    def test_missing_duplicate_wrong_origin_or_identity_refused(self):
        for change in ('absent','duplicate','origin','arch','branch','name'):
            with self.subTest(change=change):
                with patch.object(self.installation,'list_installed_refs',return_value=[] if change=='absent' else [self.ref,self.ref] if change=='duplicate' else [self.ref]):
                    if change in ('origin','arch','branch','name'):
                        with patch.object(self.ref,'get_'+change,return_value='foreign'),self.assertRaises(MigrationError): self.call()
                    else:
                        with self.assertRaises(MigrationError): self.call()
    def test_current_and_locked_retained_mount_are_distinct_from_foreign_directory(self):
        removed=self.base/'.removed'/(subject.OFFICE+'-'+COMMIT);removed.parent.mkdir();self.deployed.rename(removed);self.files=removed/'files'
        self.ref.get_commit=lambda:'b'*64
        self.call()
        self.files=self.base/'injected';self.files.mkdir()
        with self.assertRaises(MigrationError): self.call()
    def test_symlink_writable_ancestor_and_changed_mount_refused(self):
        self.files.chmod(0o777)
        with self.assertRaises(MigrationError): self.call()
        self.files.chmod(0o755)
        self.files.rmdir();replacement=self.base/'replacement';replacement.mkdir();self.files.symlink_to(replacement)
        with self.assertRaises(MigrationError): self.call()
    def test_signature_or_remote_failure_cannot_supply_extension_authority(self):
        for name in ('luma_installer.depot_flatpak.validate_remote','luma_installer.native_app_roles.verify_deployed_commit'):
            with patch(name,side_effect=ValueError('untrusted')):
                with self.assertRaises(ValueError): subject._runtime_mount(self.installation,subject.OFFICE,'x86_64','44',COMMIT,123,'lib/office')

if __name__=='__main__': unittest.main()
