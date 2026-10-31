# SPDX-License-Identifier: Apache-2.0
import importlib.util
import fcntl
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock
from luma_installer import retired_apps as policy

class Ref:
    def __init__(self,ref):self.ref=ref
    def format_ref(self):return self.ref
    def get_name(self):return self.ref.split('/')[1]
class Installation:
    def __init__(self,*refs):self.refs=list(refs);self.drops=0
    def list_installed_refs(self,_):return [Ref(ref) for ref in self.refs]
    def drop_caches(self,_):self.drops+=1
class Transaction:
    def __init__(self,installation,refs,fail=False):self.installation=installation;self.refs=refs;self.fail=fail
    def run(self,_):
        if self.fail:raise RuntimeError('running app, retry later')
        self.installation.refs=[ref for ref in self.installation.refs if ref not in self.refs];return True
class Retirement(unittest.TestCase):
    def test_all_branches_arches_installations_exact_app_only_and_data_kept(self):
        retired=[f'app/org.projectluma.Displays/{arch}/{branch}' for arch in ('x86_64','aarch64') for branch in ('stable','beta','nightly')]
        unrelated=['app/org.projectluma.Notes/aarch64/beta','runtime/org.projectluma.Displays/aarch64/44','app/org.projectluma.DisplaysOther/x86_64/beta']
        system=Installation(*(retired+unrelated));user=Installation(*retired)
        with tempfile.TemporaryDirectory() as d:
            data=Path(d)/'.var/app/org.projectluma.Displays/preferences.json';data.parent.mkdir(parents=True);data.write_text('saved user data')
            self.assertEqual(policy.reconcile([system,user],transaction=lambda i,r,c:Transaction(i,r)),tuple(sorted(retired))*2)
            self.assertEqual(data.read_text(),'saved user data')
        self.assertEqual(system.refs,unrelated);self.assertEqual(user.refs,[])
        self.assertEqual(policy.reconcile([system,user],transaction=lambda *_:self.fail('no repeated removal')),())
    def test_failure_does_not_mark_complete_and_next_run_retries(self):
        app='app/org.projectluma.Displays/aarch64/nightly';installation=Installation(app)
        with self.assertRaises(RuntimeError):policy.reconcile([installation],transaction=lambda i,r,c:Transaction(i,r,True))
        self.assertEqual(installation.refs,[app])
        self.assertEqual(policy.reconcile([installation],transaction=lambda i,r,c:Transaction(i,r)),(app,))
    def test_false_success_or_duplicate_inventory_refused(self):
        app='app/org.projectluma.Displays/x86_64/beta';installation=Installation(app)
        with self.assertRaises(RuntimeError):policy.reconcile([installation],transaction=lambda *_:SimpleNamespace(run=lambda _:True))
        installation.refs.append(app)
        with self.assertRaises(ValueError):policy.reconcile([installation],transaction=lambda *_:self.fail('must reject before transaction'))
    def test_transaction_is_uninstall_only_noninteractive_no_network_or_prune(self):
        class Tx:
            def __init__(self):self.flags={};self.callbacks={};self.refs=[];self.operations=[]
            def __getattr__(self,name):
                if name.startswith('set_'):return lambda value:self.flags.__setitem__(name,value)
                raise AttributeError(name)
            def connect(self,name,fn):self.callbacks[name]=fn
            def add_uninstall(self,ref):self.refs.append(ref)
            def get_operations(self):return self.operations
        tx=Tx();flatpak=SimpleNamespace(Transaction=SimpleNamespace(new_for_installation=lambda *_:tx),TransactionOperationType=SimpleNamespace(UNINSTALL=7))
        gi=SimpleNamespace(require_version=lambda *_:None);repo=SimpleNamespace(Flatpak=flatpak)
        refs=('app/org.projectluma.Displays/aarch64/beta',)
        with mock.patch.dict(sys.modules,{'gi':gi,'gi.repository':repo}):self.assertIs(policy.removal_transaction(object(),refs),tx)
        self.assertEqual(tx.refs,list(refs));self.assertEqual(tx.flags,{'set_no_interaction':True,'set_no_pull':True,'set_disable_dependencies':True,'set_disable_related':True,'set_disable_prune':True,'set_include_unused_uninstall_ops':False,'set_force_uninstall':False})
        op=lambda ref,kind=7:SimpleNamespace(get_ref=lambda:ref,get_operation_type=lambda:kind)
        tx.operations=[op(refs[0])];self.assertTrue(tx.callbacks['ready'](tx))
        for operations in ([],[op(refs[0],1)],[op('app/org.projectluma.Notes/aarch64/beta')],[op(refs[0]),op(refs[0])]):
            tx.operations=operations;self.assertFalse(tx.callbacks['ready'](tx))
        self.assertFalse(tx.callbacks['add-new-remote'](tx,'unknown'))
    def test_kernel_lock_serializes_reconcilers_and_rejects_link(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'retirement.lock'
            with policy._lock(path,os.geteuid()):
                fd=os.open(path,os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                finally:os.close(fd)
            with policy._lock(path,os.geteuid()):pass
            path.unlink();path.symlink_to(Path(d)/'other')
            with self.assertRaises(OSError):
                with policy._lock(path,os.geteuid()):pass

    def test_privilege_scope_refused_before_gi_or_home_access(self):
        with mock.patch.object(policy.os,'geteuid',return_value=1003),self.assertRaises(PermissionError):policy.reconcile_current(system=True)
        with mock.patch.object(policy.os,'geteuid',return_value=0),self.assertRaises(PermissionError):policy.reconcile_current(system=False)

if __name__=='__main__':unittest.main()
