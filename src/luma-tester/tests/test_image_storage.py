# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from luma_tester import image_storage, vm
from luma_tester.core import Session, TesterError


class ImageStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.root = self.directory/'state'
        self.image = dict(sha256='a'*64, media='disk')
        self.base = image_storage.cache_path(self.root, self.image)
        self.base.parent.mkdir(parents=True)
        self.base.write_bytes(b'base image')
        self.manifest = dict(schema=1, id='first', target='desktop', arch='x86_64', boot='uefi',
            cloud_init=True, user='luma', build='Test image', image=dict(url=self.base.as_uri(),
            sha256=self.image['sha256'], bytes=self.base.stat().st_size))

    def workspace(self, identity='first', sha=None, iso=False, installed=False):
        value=dict(self.manifest, id=identity, image=dict(self.manifest['image'],sha256=sha or self.image['sha256']))
        if iso:value.update(media='iso',cloud_init=False)
        directory=self.root/'sessions'/identity;directory.mkdir(parents=True)
        path=directory/'manifest.json';vm.atomic_json(path,value)
        if installed:
            (directory/'workspace.qcow2').touch()
            vm.atomic_json(directory/'setup.json',dict(phase='ready',sha256=value['image']['sha256']))
        else:(directory/'disk.qcow2').write_bytes(b'workspace files and snapshots')
        return Session(path,self.root)

    def test_unused_download_is_deleted_and_other_images_are_kept(self):
        other=self.base.with_name('b'*64+'.qcow2');other.write_bytes(b'other')
        self.base.chmod(0o444)
        plan=image_storage.inspect(self.root,self.image)
        self.assertFalse(plan.workspaces)
        image_storage.remove(self.root,plan)
        self.assertFalse(self.base.exists());self.assertEqual(other.read_bytes(),b'other')

    def test_confirmed_workspace_snapshots_and_registrations_are_removed_without_touching_imports(self):
        session=self.workspace();other=self.workspace('other','b'*64)
        imported=self.root/'imported-images/first.json';vm.atomic_json(imported,self.manifest)
        pinned=self.root/'preset-images/first.json';vm.atomic_json(pinned,self.manifest)
        original=self.directory/'original.qcow2';original.write_bytes(b'original')
        shared=self.directory/'project';shared.mkdir();(shared/'app.py').write_text('important')
        (session.directory/'shared-project').symlink_to(shared,target_is_directory=True)
        vm.atomic_json(self.root/'presets.json',[dict(name='Phone',manifest=str(pinned),target='mobile',intent='try',image=self.manifest['image'])])
        vm.atomic_json(self.root/'preferences.json',dict(images=[str(imported)],selected_images={'desktop':str(imported)},selected_variants={'desktop:x86_64':str(imported)},other_setting=True))
        plan=image_storage.inspect(self.root,self.image)
        self.assertEqual(plan.workspaces,(session.directory,));self.assertEqual(plan.presets,('Phone',))
        image_storage.remove(self.root,plan)
        self.assertFalse(self.base.exists());self.assertFalse(session.directory.exists())
        self.assertFalse(imported.exists());self.assertFalse(pinned.exists())
        self.assertTrue(other.disk.exists());self.assertEqual(original.read_bytes(),b'original')
        self.assertEqual((shared/'app.py').read_text(),'important')
        self.assertEqual(json.loads((self.root/'presets.json').read_text()),[])
        prefs=json.loads((self.root/'preferences.json').read_text())
        self.assertEqual(prefs['selected_images'],{});self.assertTrue(prefs['other_setting'])

    def test_live_session_lease_blocks_every_deletion_even_if_qmp_is_unreachable(self):
        session=self.workspace();plan=image_storage.inspect(self.root,self.image)
        with session.lock(),self.assertRaises(TesterError):image_storage.remove(self.root,plan)
        self.assertTrue(self.base.exists());self.assertTrue(session.disk.exists())

    def test_download_or_preparation_lease_blocks_deletion(self):
        plan=image_storage.inspect(self.root,self.image)
        with image_storage.lease(self.root,self.image['sha256']),self.assertRaises(TesterError):
            image_storage.remove(self.root,plan)
        self.assertTrue(self.base.exists())

    def test_new_workspace_since_confirmation_requires_new_confirmation(self):
        plan=image_storage.inspect(self.root,self.image);session=self.workspace()
        with self.assertRaisesRegex(TesterError,'changed'):image_storage.remove(self.root,plan)
        self.assertTrue(self.base.exists());self.assertTrue(session.disk.exists())

    def test_installed_iso_workspace_and_snapshots_survive_installer_removal(self):
        self.image['media']='iso';self.base.rename(self.base.with_suffix('.iso'));self.base=self.base.with_suffix('.iso')
        session=self.workspace(iso=True,installed=True)
        plan=image_storage.inspect(self.root,self.image)
        self.assertFalse(plan.workspaces)
        image_storage.remove(self.root,plan)
        self.assertFalse(self.base.exists());self.assertTrue(session.disk.exists())

    def test_linked_cache_and_workspace_are_refused(self):
        original=self.directory/'original';original.write_bytes(b'keep')
        self.base.unlink();self.base.symlink_to(original)
        with self.assertRaises(TesterError):image_storage.inspect(self.root,self.image)
        self.base.unlink();self.base.write_bytes(b'base')
        (self.root/'sessions').mkdir();(self.root/'sessions/linked').symlink_to(original)
        with self.assertRaises(TesterError):image_storage.inspect(self.root,self.image)
        self.assertEqual(original.read_bytes(),b'keep')

    def test_unreadable_workspace_record_fails_before_deleting_anything(self):
        session=self.workspace();(session.directory/'manifest.json').write_text('{broken')
        with self.assertRaises(TesterError):image_storage.inspect(self.root,self.image)
        self.assertTrue(self.base.exists());self.assertTrue(session.disk.exists())

    def test_only_managed_digest_paths_can_be_deleted(self):
        with self.assertRaises(TesterError):image_storage.inspect(self.root,dict(sha256='../original'))

    def test_external_manifest_is_forgotten_but_original_file_is_never_deleted(self):
        original=self.directory/'original.json';vm.atomic_json(original,self.manifest)
        vm.atomic_json(self.root/'preferences.json',dict(images=[str(original)],selected_images={'desktop':str(original)}))
        plan=image_storage.inspect(self.root,self.image)
        self.assertIn(str(original),plan.references)
        image_storage.remove(self.root,plan)
        self.assertTrue(original.is_file())
        preferences=json.loads((self.root/'preferences.json').read_text())
        self.assertEqual(preferences['images'],[]);self.assertEqual(preferences['selected_images'],{})

    @unittest.skipUnless(shutil.which('qemu-img'),'qemu-img required')
    def test_real_overlay_with_internal_snapshot_is_removed_only_after_confirmation(self):
        self.base.unlink()
        subprocess.run(['qemu-img','create','-q','-f','qcow2',str(self.base),'32M'],check=True)
        session=self.workspace();session.disk.unlink()
        subprocess.run(['qemu-img','create','-q','-f','qcow2','-F','qcow2','-b',str(self.base),str(session.disk)],check=True)
        subprocess.run(['qemu-img','snapshot','-c','saved-work',str(session.disk)],check=True)
        plan=image_storage.inspect(self.root,self.image)
        info=json.loads(subprocess.check_output(['qemu-img','info','--output=json',str(session.disk)]))
        self.assertEqual(info['snapshots'][0]['name'],'saved-work')
        self.assertTrue(self.base.exists());self.assertTrue(session.disk.exists())
        image_storage.remove(self.root,plan)
        self.assertFalse(self.base.exists());self.assertFalse(session.directory.exists())
