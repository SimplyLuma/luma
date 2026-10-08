# SPDX-License-Identifier: Apache-2.0
"""Deployment metadata negative controls, using real Flatpak/GIO types.

Cryptographic deployed-commit admission is qualified separately against actual
signed repositories; these checks isolate altered live-instance claims.
"""
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
import gi
gi.require_version('Flatpak', '1.0')
from gi.repository import Flatpak, Gio
from luma_installer.app_data_broker import authenticate_deployment
from luma_installer.app_data_migration import MigrationError


class DeploymentIdentity(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='luma-app-identity-', dir=Path.home()))
        self.deployment = self.base / 'app/org.projectluma.Notes/x86_64/beta' / ('a' * 64)
        self.files = self.deployment / 'files'
        self.files.mkdir(parents=True)
        self.ref = SimpleNamespace(get_deploy_dir=lambda: str(self.deployment),
            get_commit=lambda:'a'*64, get_branch=lambda:'beta', get_arch=lambda:'x86_64',
            get_name=lambda:'org.projectluma.Notes', format_ref=lambda:'app/org.projectluma.Notes/x86_64/beta')
        self.system = SimpleNamespace(list_installed_refs=lambda _: [self.ref],
                                      get_path=lambda: Gio.File.new_for_path(str(self.base)))
        self.user = SimpleNamespace(list_installed_refs=lambda _: [])
        self.original_stat = Path.stat
    def tearDown(self):
        shutil.rmtree(self.base)
    def info(self, **changes):
        info = configparser.ConfigParser(interpolation=None)
        values = {'app-commit':'a'*64, 'app-path':str(self.files), 'arch':'x86_64', 'branch':'beta'}
        values.update(changes)
        info['Instance'] = values
        return info
    def validate(self, info):
        def mounted(path, *args, **kwargs):
            if path == Path('/proc/123/root/app'):
                return self.original_stat(self.files)
            return self.original_stat(path, *args, **kwargs)
        with patch.object(Flatpak.Installation, 'new_system', return_value=self.system), \
             patch.object(Flatpak.Installation, 'new_user', return_value=self.user), \
             patch('luma_installer.native_app_roles.installed', return_value=self.ref), \
             patch.object(Path, 'stat', mounted):
            authenticate_deployment(info, 'org.projectluma.Notes', 123)
    def test_exact_installed_mount_and_identity(self):
        self.validate(self.info())
    def test_forged_build_override_commit_and_identity_are_refused(self):
        for changed in ({'build':'true'}, {'devel':'true'}, {'app-commit':''},
                        {'app-commit':'b'*64}, {'app-path':str(self.base)},
                        {'original-app-path':str(self.files)}, {'app-extensions':'org.example.Code='+'b'*64},
                        {'branch':'nightly'}, {'arch':'aarch64'}):
            with self.subTest(changed=changed), self.assertRaises(MigrationError):
                self.validate(self.info(**changed))
    def test_symlinked_deployment_and_other_user_writable_ancestor_refused(self):
        self.files.chmod(0o777)
        with self.assertRaises(MigrationError): self.validate(self.info())
        self.files.chmod(0o755)
        self.files.rmdir()
        target = self.base / 'injected'; target.mkdir()
        self.files.symlink_to(target)
        with self.assertRaises(MigrationError): self.validate(self.info())

    def retained_after_update(self):
        info = self.info()
        removed = self.base / '.removed' / ('org.projectluma.Notes-' + 'a' * 64)
        removed.parent.mkdir()
        self.deployment.rename(removed)
        self.files = removed / 'files'
        self.deployment = self.base / 'app/org.projectluma.Notes/x86_64/beta' / ('b' * 64)
        (self.deployment / 'files').mkdir(parents=True)
        self.ref.get_commit = lambda: 'b' * 64
        return info

    def test_running_signed_a_uses_retained_files_after_b_is_deployed(self):
        info = self.retained_after_update()
        with patch('luma_installer.native_app_roles.verify_deployed_commit') as verify:
            self.validate(info)
        verify.assert_called_once_with(self.system, self.ref, commit='a' * 64,
                                       exact_ref='app/org.projectluma.Notes/x86_64/beta')

    def test_retained_wrong_signature_missing_mount_and_unsafe_directory_are_refused(self):
        info = self.retained_after_update()
        with patch('luma_installer.native_app_roles.verify_deployed_commit', side_effect=ValueError('untrusted')):
            with self.assertRaises(MigrationError): self.validate(info)
        with patch('luma_installer.native_app_roles.verify_deployed_commit'):
            self.files.chmod(0o777)
            with self.assertRaises(MigrationError): self.validate(info)
            self.files.chmod(0o755)
            shutil.rmtree(self.files)
            # A mount from an unrelated directory must never supply the old A.
            self.files = self.base / 'unrelated'; self.files.mkdir()
            with self.assertRaises(MigrationError): self.validate(info)


if __name__ == '__main__': unittest.main()
