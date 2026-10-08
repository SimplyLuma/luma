# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).parents[1]))
from luma_installer.depot_app_channels import ChannelReview, data_schema, operations_match
from luma_installer.depot_flatpak import ResolvedSource, SourceUnavailable

class AppChannels(unittest.TestCase):
    def test_compatibility_marker_required(self):
        self.assertEqual(data_schema('[X-Luma]\napp-data-schema=notes-v1\n'), 'notes-v1')
        for text in ('[Application]\nname=org.projectluma.Notes\n', '[X-Luma]\napp-data-schema=../../data\n',
                     '[X-Luma]\napp-data-schema=notes-v1\napp-data-schema=notes-v2\n'):
            with self.assertRaises(SourceUnavailable): data_schema(text)
    def test_rebase_exact_target_old_removal_and_dependency_scope(self):
        import gi
        gi.require_version('Flatpak','1.0')
        from gi.repository import Flatpak
        source = ResolvedSource('org.projectluma.Notes','x86_64','app/org.projectluma.Notes/x86_64/nightly','a'*64,'luma','nightly')
        checked = ChannelReview(source,'app/org.projectluma.Notes/x86_64/beta','b'*64,'notes-v1',())
        def op(ref, commit='', remote='luma', kind=Flatpak.TransactionOperationType.INSTALL):
            return Mock(get_ref=lambda:ref, get_commit=lambda:commit, get_remote=lambda:remote, get_operation_type=lambda:kind)
        target=op(source.ref,source.commit)
        removal=op(checked.old_ref,remote='',kind=Flatpak.TransactionOperationType.UNINSTALL)
        runtime=op('runtime/org.projectluma.Platform/x86_64/44','c'*64)
        self.assertTrue(operations_match(checked,[target,removal,runtime]))
        for extra in (op('app/org.projectluma.Other/x86_64/nightly','d'*64),
                      op(runtime.get_ref(), 'c'*64, 'flathub'),
                      op(runtime.get_ref(), kind=Flatpak.TransactionOperationType.UNINSTALL),
                      op('runtime/org.unrelated.Runtime/x86_64/44','c'*64)):
            self.assertFalse(operations_match(checked,[target,removal,extra]))
        for changed in ([target], [target,removal,removal],
                        [op(source.ref,'e'*64),removal],
                        [op(source.ref,source.commit,kind=Flatpak.TransactionOperationType.UNINSTALL),removal],
                        [op(source.ref,source.commit,kind=Flatpak.TransactionOperationType.UPDATE),removal],
                        [target,op(checked.old_ref,kind=Flatpak.TransactionOperationType.UPDATE)]):
            self.assertFalse(operations_match(checked,changed))

    def test_rollback_target_data_generation_and_dependency_scope(self):
        from types import SimpleNamespace
        import gi
        gi.require_version('Flatpak', '1.0')
        from gi.repository import Flatpak, GLib
        from luma_installer.depot_app_channels import rollback_operations_match
        before = '[Application]\nname=org.projectluma.Notes\n[X-Luma]\napp-data-schema=notes-v1\n'
        wanted = 'app/org.projectluma.Notes/x86_64/beta'
        ref = SimpleNamespace(format_ref=lambda: wanted, get_arch=lambda:'x86_64', get_name=lambda:'org.projectluma.Notes')
        def op(path=wanted, commit='a'*64, remote='luma', kind=Flatpak.TransactionOperationType.UPDATE, text=before):
            metadata = GLib.KeyFile.new()
            metadata.load_from_data(text, len(text), GLib.KeyFileFlags.NONE)
            return SimpleNamespace(get_ref=lambda:path,get_commit=lambda:commit,get_remote=lambda:remote,
                get_operation_type=lambda:kind,get_metadata=lambda:metadata)
        own = op()
        runtime = op('runtime/org.projectluma.Platform/x86_64/44')
        self.assertTrue(rollback_operations_match(ref,'a'*64,before,[own,runtime]))
        for bad in (op(commit='b'*64), op(remote='flathub'),
                    op(kind=Flatpak.TransactionOperationType.UNINSTALL),
                    op(text=before.replace('notes-v1','notes-v2')),
                    op(text=before+'[Context]\nfilesystems=home;\n')):
            self.assertFalse(rollback_operations_match(ref,'a'*64,before,[bad]))
        for extra in (op('app/org.projectluma.Other/x86_64/beta'),
                      op('runtime/org.projectluma.Platform/aarch64/44'),
                      op('runtime/org.unrelated.Runtime/x86_64/44'),
                      op(runtime.get_ref(),kind=Flatpak.TransactionOperationType.UNINSTALL)):
            self.assertFalse(rollback_operations_match(ref,'a'*64,before,[own,extra]))
        self.assertFalse(rollback_operations_match(ref,'a'*64,before,[own,own]))
        self.assertFalse(rollback_operations_match(ref,'a'*64,before,[]))

if __name__=='__main__': unittest.main()
