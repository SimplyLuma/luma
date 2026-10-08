# SPDX-License-Identifier: Apache-2.0
"""C launch protocol controls; installed signature positives remain separate."""
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gi
gi.require_version('Flatpak', '1.0')
from gi.repository import Flatpak
from luma_installer import native_app_roles as roles


class NativeRoleCLI(unittest.TestCase):
    app = 'org.gnome.Nautilus'

    def reference(self, **changes):
        values = {'name':self.app, 'arch':'x86_64', 'branch':'beta',
                  'ref':f'app/{self.app}/x86_64/beta'}
        values.update(changes)
        return SimpleNamespace(get_name=lambda:values['name'], get_arch=lambda:values['arch'],
            get_branch=lambda:values['branch'], format_ref=lambda:values['ref'])

    def call(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = roles.main(args)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_no_role_keeps_native_without_output_or_flatpak_lookup(self):
        with patch.object(Path, 'exists', return_value=False), \
             patch.object(roles, 'required', return_value=False), \
             patch.object(roles, 'installed') as installed:
            self.assertEqual(self.call(['--resolve',self.app]), (0,'',''))
            installed.assert_not_called()

    def test_exact_system_trusted_ref_is_only_success_output(self):
        installation = object()
        with patch.object(Path, 'exists', return_value=False), \
             patch.object(roles, 'required', return_value=True), \
             patch.object(Flatpak.Installation, 'new_system', return_value=installation), \
             patch.object(roles, 'installed', return_value=self.reference()) as installed:
            self.assertEqual(self.call(['--resolve',self.app]),
                (10,f'app/{self.app}/x86_64/beta\n',''))
            installed.assert_called_once_with(installation,self.app)

    def test_invalid_requests_never_query_role_or_execute_a_command(self):
        with patch.object(roles, 'required') as required:
            for args in ([], ['--resolve'], ['--resolve','../secret'],
                         ['--resolve',self.app,'--command=sh'], ['--exec',self.app]):
                result, output, error = self.call(args)
                self.assertEqual(result,1); self.assertEqual(output,''); self.assertTrue(error)
            required.assert_not_called()

    def test_missing_unsigned_or_unsafe_contract_refuses_without_fallback(self):
        with patch.object(Path, 'exists', return_value=False), \
             patch.object(roles, 'required', return_value=True), \
             patch.object(Flatpak.Installation, 'new_system'), \
             patch.object(roles, 'installed', side_effect=ValueError('private error text')):
            self.assertEqual(self.call(['--resolve',self.app]),
                (1,'','The signed application installation needs repair.\n'))
        with patch.object(Path, 'exists', return_value=False), \
             patch.object(roles, 'required', side_effect=ValueError('unsafe role contract')):
            self.assertEqual(self.call(['--resolve',self.app])[0:2],(1,''))

    def test_sandbox_cannot_resolve_native_ownership(self):
        with patch.object(Path, 'exists', return_value=True), \
             patch.object(roles, 'required') as required:
            self.assertEqual(self.call(['--resolve',self.app])[0:2],(1,''))
            required.assert_not_called()

    def test_wrong_name_ref_arch_or_branch_never_enters_stdout(self):
        with patch.object(Path, 'exists', return_value=False), \
             patch.object(roles, 'required', return_value=True), \
             patch.object(Flatpak.Installation, 'new_system'):
            for changes in ({'name':'org.projectluma.Notes'}, {'arch':'../../secret'},
                            {'branch':'beta\n--command=sh'}, {'ref':'app/'+self.app+'/x86_64/beta\n'}):
                with patch.object(roles, 'installed', return_value=self.reference(**changes)):
                    self.assertEqual(self.call(['--resolve',self.app])[0:2],(1,''))
