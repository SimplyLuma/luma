# SPDX-License-Identifier: LGPL-2.1-or-later
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('payload_reuse', ROOT / 'scripts/install/atlas-iso/payload-object-reuse.py')
reuse = importlib.util.module_from_spec(spec); spec.loader.exec_module(reuse)
REF = 'luma/1/aarch64/nightly'
C = 'a' * 64


def manifest(repo, candidate=C):
    return dict(schema=reuse.SCHEMA, source_revision='b'*40, candidate=candidate,
                architecture='aarch64', profile='arm-preview', ref=REF,
                config=(repo/'config').read_text(), heads={REF: candidate},
                metadata_roots=sorted(p.name for p in repo.iterdir() if p.name != 'objects'),
                metadata_bodies={p.relative_to(repo).as_posix(): dict(bytes=p.stat().st_size, sha256=reuse.digest(p))
                                 for p in repo.rglob('*') if p.is_file() and 'objects' not in p.relative_to(repo).parts},
                objects={p.relative_to(repo).as_posix(): dict(bytes=p.stat().st_size, sha256=reuse.digest(p))
                         for p in (repo/'objects').rglob('*') if p.is_file()})


def fixture(repo):
    for name in ('objects', 'refs/heads/luma/1/aarch64'):
        (repo/name).mkdir(parents=True, exist_ok=True)
    (repo/'config').write_text('[core]\nmode=archive-z2\nrepo_version=1\n')
    (repo/'summary').write_bytes(b'new summary')
    (repo/'summary.sig').write_bytes(b'new signature')
    (repo/'refs/heads'/REF).write_text(C+'\n')
    for suffix in ('commit', 'commitmeta'):
        path=repo/f'objects/{C[:2]}/{C[2:]}.{suffix}';path.parent.mkdir(exist_ok=True);path.write_bytes(suffix.encode())
    path=repo/('objects/cc/'+'c'*62+'.filez');path.parent.mkdir();path.write_bytes(zlib.compress(b'body'*100, 1))
    return path


class PayloadReuse(unittest.TestCase):
    def test_mutable_signatures_never_reused_and_compressed_body_equality_required(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d)/'repo';body=fixture(repo);old=manifest(repo)
            body.write_bytes(zlib.compress(b'body'*100,9));new=manifest(repo)
            op=reuse.plan(old,new)
            self.assertIn(body.relative_to(repo).as_posix(),op['replace'])
            self.assertIn(f'objects/aa/{"a"*62}.commitmeta',op['replace'])
            self.assertIn(f'objects/aa/{"a"*62}.commit',op['reuse'])

    def test_invalid_config_heads_paths_and_scope_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d)/'repo';fixture(repo);m=manifest(repo)
            for change in ('private','parent','traversal','heads','architecture'):
                bad=copy.deepcopy(m)
                if change=='private':bad['metadata_roots'].append('keys')
                if change=='parent':bad['config']+='parent=/secret\n'
                if change=='traversal':bad['objects']['objects/../private']=dict(bytes=1,sha256='f'*64)
                if change=='heads':bad['heads']['luma/1/aarch64/device/nightly']='f'*64
                if change=='architecture':bad['architecture']='x86_64'
                with self.subTest(change=change),self.assertRaises(ValueError):reuse.plan(m,bad)

    def test_manifest_requires_external_pin_and_immutable_regular_file(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d)/'repo';fixture(repo);p=Path(d)/'manifest.json';p.write_text(json.dumps(manifest(repo)));pin=reuse.digest(p)
            with self.assertRaisesRegex(ValueError,'immutable'):reuse.load(p,pin)
            p.chmod(0o400);reuse.load(p,pin)
            with self.assertRaisesRegex(ValueError,'external pin'):reuse.load(p,'0'*64)
            link=Path(d)/'link';link.symlink_to(p)
            with self.assertRaisesRegex(ValueError,'immutable'):reuse.load(link,pin)

    @unittest.skipUnless(shutil.which('xorriso'),'requires real xorriso')
    def test_real_iso_replaces_metadata_removes_extras_and_preserves_exact_new_bodies(self):
        with tempfile.TemporaryDirectory(prefix='reuse quote ') as d:
            root=Path(d);oldrepo=root/'old/repo';fixture(oldrepo)
            extra=oldrepo/('objects/dd/'+'d'*62+'.filez');extra.parent.mkdir();extra.write_bytes(b'removed object')
            (oldrepo/'refs/heads/ostree-metadata').write_text('e'*64+'\n')
            old=manifest(oldrepo);old['heads']['ostree-metadata']='e'*64
            reuse.verify_object_inventory(oldrepo,old)
            original=extra.read_bytes();extra.write_bytes(b'corrupt donor')
            with self.assertRaisesRegex(ValueError,'compressed body'):reuse.verify_object_inventory(oldrepo,old)
            extra.unlink()
            with self.assertRaisesRegex(ValueError,'object set'):reuse.verify_object_inventory(oldrepo,old)
            extra.write_bytes(original)
            donor=root/'donor.iso';self.run_iso('-outdev',donor,'-map',oldrepo,'/luma/repo')
            newrepo=root/'new/repo';shutil.copytree(oldrepo,newrepo);(newrepo/extra.relative_to(oldrepo)).unlink();(newrepo/'refs/heads/ostree-metadata').unlink()
            (newrepo/f'objects/aa/{"a"*62}.commitmeta').write_bytes(b'replacement signature')
            (newrepo/'summary').write_bytes(b'fresh summary');new=manifest(newrepo)
            options=root/'commands';op=reuse.write_options(newrepo,old,new,options)
            self.assertEqual(op['remove'],[extra.relative_to(oldrepo).as_posix()])
            out=root/'new.iso';self.run_iso('-indev',donor,'-outdev',out,'-options_from_file',options)
            extracted=root/'extracted';self.run_iso('-osirrox','on','-indev',out,'-extract','/luma/repo',extracted)
            actual={p.relative_to(extracted).as_posix():p.read_bytes() for p in extracted.rglob('*') if p.is_file()}
            expected={p.relative_to(newrepo).as_posix():p.read_bytes() for p in newrepo.rglob('*') if p.is_file()}
            self.assertEqual(actual,expected)
            self.assertNotIn('refs/heads/ostree-metadata',actual)
            broken=copy.deepcopy(new);broken['objects'][next(iter(new['objects']))]['sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'replacement object'):
                # Force that body to be copied, rather than relying on donor equality.
                reuse.write_options(newrepo,old,broken,root/'bad-commands')
            extra=newrepo/('objects/ff/'+'f'*62+'.filez');extra.parent.mkdir();extra.write_bytes(b'unmanifested')
            with self.assertRaisesRegex(ValueError,'object set'):reuse.write_options(newrepo,old,new,root/'extra-commands')

    def test_unchanged_thousand_objects_require_no_host_body_reads_during_mapping(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d)/'repo';fixture(repo)
            for i in range(1000):
                name=f'{i:064x}';p=repo/f'objects/{name[:2]}/{name[2:]}.filez';p.parent.mkdir(exist_ok=True);p.write_bytes(b'unchanged body')
            old=manifest(repo);new=copy.deepcopy(old);reads=[];original=reuse.digest
            def record(path):reads.append(Path(path).relative_to(repo).as_posix());return original(path)
            with patch.object(reuse,'digest',record):
                op=reuse.write_options(repo,old,new,Path(d)/'options')
            self.assertEqual(len(op['reuse']),1002)
            object_reads=[p for p in reads if p.startswith('objects/')]
            self.assertEqual(object_reads,[f'objects/aa/{"a"*62}.commitmeta'])
            # A changed metadata body at the same path is refused before writing.
            (repo/'summary.sig').write_bytes(b'stale or substituted signature')
            with self.assertRaisesRegex(ValueError,'mutable metadata'):
                reuse.write_options(repo,old,new,Path(d)/'bad-options')

    @staticmethod
    def run_iso(*args):
        subprocess.run(['xorriso',*map(str,args),'-commit','--','-end'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)

    @unittest.skipUnless(all(shutil.which(t) for t in ('xorriso','ostree','gpg')),'requires real ISO/OSTree/GPG')
    def test_real_signed_ostree_iso_still_pulls_with_full_gpg_and_closure_verification(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);home=r/'gnupg';home.mkdir(mode=0o700)
            def run(*args):return subprocess.check_output(list(map(str,args)),stderr=subprocess.PIPE,text=True)
            run('gpg','--batch','--homedir',home,'--passphrase','','--quick-generate-key','ISO reuse test <fixture@example.invalid>','ed25519','sign','0')
            fpr=next(row.split(':')[9] for row in run('gpg','--homedir',home,'--with-colons','--list-keys').splitlines() if row.startswith('fpr:'))
            self.addCleanup(subprocess.run,['gpgconf','--homedir',str(home),'--kill','gpg-agent'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            key=r/'key.asc';key.write_text(run('gpg','--homedir',home,'--armor','--export',fpr))
            repo=r/'repo';tree=r/'tree';tree.mkdir();(tree/'body').write_text('first')
            run('ostree','--repo='+str(repo),'init','--mode=archive')
            def commit():
                c=run('ostree','--repo='+str(repo),'commit','--branch='+REF,'--tree=dir='+str(tree),'--gpg-sign='+fpr,'--gpg-homedir='+str(home)).strip()
                run('ostree','--repo='+str(repo),'summary','--update','--gpg-sign='+fpr,'--gpg-homedir='+str(home));return c
            oldcommit=commit();old=manifest(repo,oldcommit);donor=r/'old.iso';self.run_iso('-outdev',donor,'-map',repo,'/luma/repo')
            (tree/'body').write_text('second');newcommit=commit();new=manifest(repo,newcommit);options=r/'options';reuse.write_options(repo,old,new,options)
            out=r/'new.iso';self.run_iso('-indev',donor,'-outdev',out,'-options_from_file',options)
            extracted=r/'extracted';self.run_iso('-osirrox','on','-indev',out,'-extract','/luma/repo',extracted)
            run('ostree','--repo='+str(extracted),'fsck','--all')
            client=r/'client';run('ostree','--repo='+str(client),'init','--mode=archive')
            run('ostree','--repo='+str(client),'remote','add','--gpg-import='+str(key),'--set=gpg-verify-summary=true','fixture',extracted.as_uri())
            run('ostree','--repo='+str(client),'pull','fixture',REF)
            self.assertEqual(run('ostree','--repo='+str(client),'rev-parse','fixture:'+REF).strip(),newcommit)
            self.assertEqual(run('ostree','--repo='+str(client),'cat',newcommit,'/body'),'second')
            run('ostree','--repo='+str(client),'fsck','--all')
