# SPDX-License-Identifier: Apache-2.0
"""Live-instance identity controls, including actual daemonized proxy topology.

Virtual /proc supplies kernel identity changes; real Flatpak/GIO types and the
maintained authentication helper execute. Signed commit admission and actual
sandbox launch are separate mandatory installed-process qualifications.
"""
import configparser
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import gi
gi.require_version('Flatpak', '1.0')
from gi.repository import Flatpak, GLib
from luma_installer import app_data_broker as broker
from luma_installer.app_data_migration import MigrationError


class LiveInstance(unittest.TestCase):
    uid = 60302
    outer, child, sender, proxy_parent = 1689, 1729, 1725, 1724

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='luma-live-instance-')
        self.proc = Path(self.temporary.name)
        self.info = configparser.ConfigParser(interpolation=None)
        self.info['Application'] = {'name':'org.projectluma.Notes'}
        self.info['Instance'] = {'instance-id':'2501054419', 'app-commit':'a'*64,
                                'arch':'x86_64', 'branch':'beta'}
        stream = io.StringIO(); self.info.write(stream); self.text = stream.getvalue()
        self.instance = SimpleNamespace(get_id=lambda:'2501054419',
            get_app=lambda:'org.projectluma.Notes', get_commit=lambda:'a'*64,
            get_arch=lambda:'x86_64', get_branch=lambda:'beta',
            get_pid=lambda:self.outer, get_child_pid=lambda:self.child,
            is_running=lambda:True)
        self.instances = [self.instance]
        # Measured actual Flatpak proxy topology: proxy bwrap is reparented to
        # PID1, independent from the app babysitter. No invented common parent.
        self.process(1,0,uid=0)
        self.process(self.outer, 1683)
        self.process(self.child, self.outer)
        self.process(self.proxy_parent, 1)
        self.process(self.sender, self.proxy_parent)
        for pid in (self.child, self.sender):
            target = self.proc/str(pid)/'root'; target.mkdir()
            (target/'.flatpak-info').write_text(self.text)
        self.original_open = Path.open
        self.original_readlink = Path.readlink
        self.original_stat = Path.stat
        self.original_is_symlink = Path.is_symlink
        self.original_iterdir = Path.iterdir
        self.pipe_fds=[]
        self.link_pipe(148,150)
        self.runtime=self.proc/'runtime-info';self.runtime.write_text(self.text)

    def tearDown(self):
        for fd in self.pipe_fds:os.close(fd)
        self.temporary.cleanup()

    def link_pipe(self, reader, writer):
        read_fd,write_fd=os.pipe();self.pipe_fds.extend((read_fd,write_fd))
        for pid,number,actual,direction in ((self.child,reader,read_fd,'00'),(self.sender,writer,write_fd,'01')):
            base=self.proc/str(pid)
            (base/'fd').mkdir(exist_ok=True);(base/'fdinfo').mkdir(exist_ok=True)
            target=base/'fd'/str(number)
            if target.is_symlink():target.unlink()
            target.symlink_to('/proc/self/fd/'+str(actual))
            (base/'fdinfo'/str(number)).write_text('flags:\t'+direction+'\n')
        return read_fd,write_fd

    def process(self, pid, parent, *, uid=None, state='S', start='19377813'):
        base = self.proc/str(pid); base.mkdir(exist_ok=True)
        fields = [state, str(parent)] + ['0']*17 + [start]
        (base/'stat').write_text(str(pid)+' (native process with spaces) '+' '.join(fields))
        number = self.uid if uid is None else uid
        (base/'status').write_text('Name:\tnative\nUid:\t'+('\t'.join([str(number)]*4))+'\n')
        exe = base/'exe'
        if not exe.exists(): exe.symlink_to('/usr/bin/xdg-dbus-proxy' if pid==self.sender else '/usr/bin/bwrap')

    def mapped(self, path):
        value = str(path)
        if value.startswith('/proc/'):return self.proc/value.removeprefix('/proc/')
        if value==f'/run/user/{self.uid}/.flatpak/2501054419/info':return self.runtime
        return path

    def validate(self):
        def opened(path, *args, **kwargs):
            return self.original_open(self.mapped(path), *args, **kwargs)
        with patch.object(Flatpak.Instance,'get_all',return_value=self.instances), \
             patch.object(Path,'open',opened), \
             patch.object(Path,'iterdir',lambda path:self.original_iterdir(self.mapped(path))), \
             patch.object(Path,'stat',lambda path,*args,**kwargs:self.original_stat(self.mapped(path),*args,**kwargs)):
            return broker._instance_child(self.info,'org.projectluma.Notes',
                                          self.sender,self.uid,self.text)

    def test_legitimate_daemonized_proxy_maps_to_exact_live_application(self):
        child, starts = self.validate()
        self.assertEqual(child,self.child)
        self.assertIn(self.sender,starts); self.assertIn(self.outer,starts)
        self.assertIn(self.child,starts)

    def test_missing_duplicate_stopped_or_wrong_identity_instance_refused(self):
        for instances in ([],[self.instance,self.instance]):
            with self.subTest(count=len(instances)),self.assertRaises(MigrationError):
                self.instances=instances;self.validate()
        self.instances=[self.instance]
        for attribute,value in (('is_running',False),('get_app','org.projectluma.Tasks'),
                ('get_commit','b'*64),('get_arch','aarch64'),('get_branch','nightly')):
            previous=getattr(self.instance,attribute)
            try:
                setattr(self.instance,attribute,lambda value=value:value)
                with self.subTest(attribute=attribute),self.assertRaises(MigrationError):self.validate()
            finally:setattr(self.instance,attribute,previous)

    def test_unready_child_and_foreign_child_tree_refused(self):
        for pid in (0,-1,999999):
            self.instance.get_child_pid=lambda pid=pid:pid
            with self.subTest(pid=pid),self.assertRaises((MigrationError,FileNotFoundError)):self.validate()
        self.instance.get_child_pid=lambda:self.child
        self.process(self.child,1)
        with self.assertRaises(MigrationError):self.validate()

    def test_wrong_uid_zombie_and_missing_process_refused(self):
        for pid in (self.outer,self.child,self.sender):
            for options in ({'uid':60303},{'state':'Z'},{'state':'X'}):
                self.process(pid,self.outer if pid==self.child else self.proxy_parent,**options)
                with self.subTest(pid=pid,options=options),self.assertRaises(MigrationError):self.validate()
            self.process(pid,self.outer if pid==self.child else (1683 if pid==self.outer else self.proxy_parent))
        (self.proc/str(self.sender)/'stat').unlink()
        with self.assertRaises(FileNotFoundError):self.validate()

    def test_child_metadata_different_missing_or_oversized_refused(self):
        target=self.proc/str(self.child)/'root/.flatpak-info'
        for text in (self.text.replace('org.projectluma.Notes','org.projectluma.Tasks'),self.text+'x'*65537):
            target.write_text(text)
            with self.subTest(length=len(text)),self.assertRaises(MigrationError):self.validate()
        target.unlink()
        with self.assertRaises(FileNotFoundError):self.validate()

    def test_unmaintained_proxy_and_missing_proxy_executable_refused(self):
        target=self.proc/str(self.sender)/'exe'
        target.unlink();target.symlink_to('/usr/bin/python3')
        with self.assertRaises(MigrationError):self.validate()
        target.unlink()
        with self.assertRaises(FileNotFoundError):self.validate()

    def test_writable_or_foreign_owned_host_proxy_cannot_supply_authority(self):
        original=Path.lstat
        def unsafe(path,*args,**kwargs):
            value=original(path,*args,**kwargs)
            if str(path)=='/usr/bin/xdg-dbus-proxy':
                return SimpleNamespace(st_mode=value.st_mode|0o022,st_uid=value.st_uid,
                    st_dev=value.st_dev,st_ino=value.st_ino)
            return value
        with patch.object(Path,'lstat',unsafe),self.assertRaises(MigrationError):self.validate()
        def foreign(path,*args,**kwargs):
            value=original(path,*args,**kwargs)
            if str(path)=='/usr/bin/xdg-dbus-proxy':
                return SimpleNamespace(st_mode=value.st_mode,st_uid=self.uid,
                    st_dev=value.st_dev,st_ino=value.st_ino)
            return value
        with patch.object(Path,'lstat',foreign),self.assertRaises(MigrationError):self.validate()

    def authenticate(self, deployment, *, caller_uid=None):
        def opened(path,*args,**kwargs):return self.original_open(self.mapped(path),*args,**kwargs)
        caller=self.uid if caller_uid is None else caller_uid
        connection=SimpleNamespace(call_sync=lambda bus,path,interface,method,*args:
            GLib.Variant('(u)',(caller if method=='GetConnectionUnixUser' else self.sender,)))
        with patch.object(Flatpak.Instance,'get_all',return_value=self.instances), \
             patch.object(Path,'open',opened), \
             patch.object(Path,'iterdir',lambda path:self.original_iterdir(self.mapped(path))), \
             patch.object(Path,'stat',lambda path,*args,**kwargs:self.original_stat(self.mapped(path),*args,**kwargs)), \
             patch.object(Path,'is_symlink',lambda path:self.original_is_symlink(self.mapped(path))), \
             patch.object(broker.os,'getuid',return_value=self.uid), \
             patch.object(broker,'authenticate_deployment',side_effect=deployment):
            return broker.authenticate(connection,':1.5')

    def test_full_auth_passes_child_to_deployment_not_proxy(self):
        observed=[]
        def deployment(info,app,pid):observed.append((app,pid))
        self.assertEqual(self.authenticate(deployment),'org.projectluma.Notes')
        self.assertEqual(observed,[('org.projectluma.Notes',self.child)])

    def test_full_auth_retains_crypto_refusal_and_detects_reuse_during_crypto(self):
        def refused(*_):raise MigrationError('Untrusted signature or altered mounted inode')
        with self.assertRaises(MigrationError):self.authenticate(refused)
        for pid in (self.outer,self.child,self.sender):
            def changed(*_,pid=pid):
                parent=1683 if pid==self.outer else self.outer if pid==self.child else self.proxy_parent
                self.process(pid,parent,start='changed-start')
            with self.subTest(pid=pid),self.assertRaises(MigrationError):self.authenticate(changed)
            self.process(pid,1683 if pid==self.outer else self.outer if pid==self.child else self.proxy_parent)

    def test_full_auth_foreign_root_uid_and_forged_runtime_metadata_refused(self):
        def forbidden(*_):self.fail('No deployment authority may be requested')
        for uid in (0,60303):
            with self.subTest(uid=uid),self.assertRaises(MigrationError):self.authenticate(forbidden,caller_uid=uid)
        self.runtime.write_text(self.text.replace('org.projectluma.Notes','org.projectluma.Tasks'))
        with self.assertRaises(MigrationError):self.authenticate(forbidden)
        self.runtime.unlink();target=self.proc/'forged-info';target.write_text(self.text);self.runtime.symlink_to(target)
        with self.assertRaises(MigrationError):self.authenticate(forbidden)

    def test_lifecycle_pipe_requires_matching_kernel_inode_and_direction(self):
        self.link_pipe(149,151)
        with self.assertRaises(MigrationError):self.validate()  # two distinct linked pipes
        (self.proc/str(self.child)/'fd/149').unlink()
        (self.proc/str(self.sender)/'fd/151').unlink()
        flags=self.proc/str(self.sender)/'fdinfo/150'
        flags.write_text('flags:\t00\n')
        with self.assertRaises(MigrationError):self.validate()  # two read ends
        flags.write_text('flags:\t01\n')
        (self.proc/str(self.child)/'fd/148').unlink()
        with self.assertRaises(MigrationError):self.validate()  # missing application end

    def test_duplicated_descriptor_for_one_lifecycle_endpoint_is_refused(self):
        base=self.proc/str(self.sender)
        original=base/'fd/150'
        (base/'fd/151').symlink_to(original.readlink())
        (base/'fdinfo/151').write_text('flags:\t01\n')
        with self.assertRaises(MigrationError):self.validate()

    def test_descriptor_enumeration_is_bounded_before_accessing_unbounded_entries(self):
        observed=[]
        def infinite():
            for number in range(10000000):
                observed.append(number);yield Path('/proc')/str(self.sender)/'fd'/str(number)
        with patch.object(Path,'iterdir',return_value=infinite()),self.assertRaises(MigrationError):
            broker._pipe_descriptors(self.sender,os.O_WRONLY)
        self.assertEqual(len(observed),4097)

    def test_lifecycle_fd_replacement_during_read_is_refused(self):
        original=self.original_stat;calls={}
        def changed(path,*args,**kwargs):
            mapped=self.mapped(path);value=original(mapped,*args,**kwargs)
            if str(mapped)==str(self.proc/str(self.sender)/'fd/150'):
                calls[str(mapped)]=calls.get(str(mapped),0)+1
                if calls[str(mapped)]>=2:
                    return SimpleNamespace(st_mode=value.st_mode,st_dev=value.st_dev,st_ino=value.st_ino+1)
            return value
        self.original_stat=changed
        with self.assertRaises(MigrationError):self.validate()

    def test_lifecycle_link_changed_during_crypto_is_refused(self):
        def replaced(*_):
            read_fd,write_fd=os.pipe();self.pipe_fds.extend((read_fd,write_fd))
            target=self.proc/str(self.sender)/'fd/150';target.unlink();target.symlink_to('/proc/self/fd/'+str(write_fd))
        with self.assertRaises(MigrationError):self.authenticate(replaced)

    def test_pid_reuse_during_validation_refused(self):
        original=broker._process_identity;calls={}
        def reuse(pid,uid):
            result=original(pid,uid);calls[pid]=calls.get(pid,0)+1
            if pid==self.child and calls[pid]>1:return result[0],str(int(result[1])+1)
            return result
        with patch.object(broker,'_process_identity',side_effect=reuse),self.assertRaises(MigrationError):
            self.validate()


if __name__=='__main__':unittest.main()
