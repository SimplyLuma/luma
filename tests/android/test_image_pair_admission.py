#!/usr/bin/python3
"""Private admission negative controls; synthetic bytes are never build proof."""
import copy
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

HERE = Path(__file__).resolve().parent
MODULES = HERE if (HERE / 'package-images.py').is_file() else HERE.parents[1] / 'scripts/android'
def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, MODULES / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
package = load('pair_package', 'package-images.py')
provenance = load('pair_provenance', 'verify-packaged-image-provenance.py')

class SourceIdentity(unittest.TestCase):
    def test_current_and_retained_source_identity(self):
        self.assertEqual(provenance.source_manifest({'source_manifest_sha256':'a'*64}), 'a'*64)
        self.assertEqual(provenance.source_manifest({'source63_sha256':'b'*64}), 'b'*64)
    def test_ambiguous_missing_and_malformed_source_refused(self):
        for value in ({}, {'source_manifest_sha256':'x'*64},
                      {'source_manifest_sha256':True},
                      {'source_manifest_sha256':'a'*64, 'source63_sha256':'a'*64}):
            with self.assertRaises(ValueError): provenance.source_manifest(value)

class PairAdmission(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic-pair-negative-')
        self.root = Path(self.tmp.name)
        self.output = self.root / 'output'; self.output.mkdir()
        self.values = {'LUMA_ANDROID_IMAGE_ORIGIN':'luma-native-builder',
                      'LUMA_ANDROID_SOURCE_MANIFEST_SHA256':'a'*64,
                      'LUMA_ANDROID_COMPILER_INVOCATION_ID':'b'*32}
        self.archives = []
        for kind in ('SYSTEM', 'VENDOR'):
            archive = self.root / ('waydroid_x86_64-' + kind.lower() + '.zip')
            raw = (kind + '-synthetic-not-bootable').encode()
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr(kind.lower()+'.img', raw)
            self.archives.append(archive)
            prefix = 'LUMA_ANDROID_' + kind + '_'
            self.values.update({prefix+'FILENAME':archive.name, prefix+'URL':'private-fixture',
                prefix+'SHA256':package.digest(archive), prefix+'SIZE':str(archive.stat().st_size),
                prefix+'IMAGE_SHA256':__import__('hashlib').sha256(raw).hexdigest(),
                prefix+'IMAGE_SIZE':str(len(raw))})
        self.pin = self.root/'pin.env'; self.write_pin()

    def tearDown(self): self.tmp.cleanup()
    def write_pin(self): self.pin.write_text(''.join(k+'='+v+'\n' for k,v in self.values.items()))
    def prepare(self): package.prepare(self.pin, self.archives, self.output, 'x86_64')

    def evidence(self):
        self.prepare()
        manifest = json.loads((self.output/'luma-images.json').read_text())
        self.materials = self.root/'materials.tar.gz'; self.materials.write_bytes(b'synthetic-source')
        self.font = self.root/'OFL.txt'; self.font.write_text('synthetic-license')
        self.notices = []
        members = []
        for role in ('system','vendor'):
            notice = self.root/(role+'-NOTICE.xml.gz')
            notice.write_bytes(gzip.compress(b'<licenses><file-name>fixture</file-name></licenses>'))
            self.notices.append(notice)
            members.append({'role':role,'image_path':'/'+role+'/etc/NOTICE.xml.gz',
                            'sha256':provenance.sha(notice)})
        members.append({'role':'vendor','image_path':'/etc/licenses/prairie-figtree/OFL.txt',
                        'sha256':provenance.sha(self.font)})
        self.producer = {'result':'CANONICAL-PAIR-RETAINED','actual_container_exit_code':0,
            'actual_systemd_terminal_success':True,'source63_sha256':'a'*64,
            'compiler_invocation_id':'b'*32,
            'images':{n:{'sha256':r['sha256'],'bytes':r['size']} for n,r in manifest['images'].items()},
            'archives':{n+'.img':{'sha256':r['sha256'],'bytes':r['size']} for n,r in manifest['archives'].items()},
            'source_material_archive':{'sha256':provenance.sha(self.materials),'bytes':self.materials.stat().st_size}}
        self.producer_path = self.root/'producer.json'; self.save_producer()
        self.members = {'result':'PASS','read_only_debugfs_no_filesystem_mount':True,
            'actual_Lineage_TaskStackListener_closeRemovedTask_and_HIDL13_native_java_VINTF_coherent':True,
            'terminal_pair_receipt_sha256':provenance.sha(self.producer_path),'members':members}
        self.member_path = self.root/'members.json'; self.save_members()

    def save_producer(self): self.producer_path.write_text(json.dumps(self.producer))
    def save_members(self): self.member_path.write_text(json.dumps(self.members))
    def verify(self): provenance.verify(self.output,self.producer_path,self.member_path,
                                       self.materials,*self.notices,self.font)

    def test_official_reference_backwards_compatible(self):
        del self.values['LUMA_ANDROID_IMAGE_ORIGIN']; self.write_pin(); self.prepare()
        self.assertNotIn('origin',json.loads((self.output/'luma-images.json').read_text()))
        package.verify(self.output,'x86_64')

    def test_swapped_archives_and_raw_hash_refused(self):
        self.archives.reverse()
        with self.assertRaises(ValueError): self.prepare()
        self.archives.reverse()
        self.values['LUMA_ANDROID_SYSTEM_IMAGE_SHA256']='c'*64; self.write_pin()
        with self.assertRaises(ValueError): self.prepare()

    def test_missing_native_producer_identity_refused(self):
        self.values['LUMA_ANDROID_COMPILER_INVOCATION_ID']=''; self.write_pin()
        with self.assertRaises(ValueError): self.prepare()

    def test_positive_correspondence_and_failed_terminal_refused(self):
        self.evidence(); self.verify()
        self.producer['actual_container_exit_code']=1; self.save_producer()
        with self.assertRaises(ValueError): self.verify()
        for key,value in (('actual_container_exit_code',False),
                          ('actual_systemd_terminal_success','false')):
            self.producer['actual_container_exit_code']=0
            self.producer['actual_systemd_terminal_success']=True
            self.producer[key]=value; self.save_producer()
            with self.subTest(key=key), self.assertRaises(ValueError): self.verify()

    def test_authoritative_removal_coherent_pair_proof_required(self):
        self.evidence(); self.verify()
        key='actual_Lineage_TaskStackListener_closeRemovedTask_and_HIDL13_native_java_VINTF_coherent'
        for invalid in (False, None, 'true', 1):
            self.members[key]=invalid; self.save_members()
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): self.verify()
        del self.members[key]; self.save_members()
        with self.assertRaises(ValueError): self.verify()
        self.members[key]=True; self.save_members(); self.verify()

    def test_member_receipt_cannot_be_rebound_to_other_producer(self):
        self.evidence(); self.producer['compiler_invocation_id']='d'*32; self.save_producer()
        with self.assertRaises(ValueError): self.verify()

    def test_source_material_notice_and_license_mutations_refused(self):
        self.evidence()
        for path in (self.materials,*self.notices,self.font):
            before=path.read_bytes(); path.write_bytes(before+b'changed')
            with self.subTest(path=path.name), self.assertRaises(ValueError): self.verify()
            path.write_bytes(before)
        self.verify()

    def test_swapped_complete_raw_pair_refused(self):
        self.evidence()
        system=self.output/'system.img'; vendor=self.output/'vendor.img'
        a,b=system.read_bytes(),vendor.read_bytes();system.write_bytes(b);vendor.write_bytes(a)
        with self.assertRaises(ValueError): self.verify()

    def test_inside_image_license_missing_and_symlink_refused(self):
        self.evidence(); self.members['members'].pop(); self.save_members()
        with self.assertRaises(ValueError): self.verify()
        self.materials.unlink(); self.materials.symlink_to(self.font)
        with self.assertRaises(ValueError): self.verify()

if __name__=='__main__': unittest.main()
