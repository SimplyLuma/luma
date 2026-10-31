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
wrapper_spec=importlib.util.spec_from_file_location('payload_wrapper',ROOT/'scripts/install/atlas-iso/run-mkksiso.py')
wrapper=importlib.util.module_from_spec(wrapper_spec);wrapper_spec.loader.exec_module(wrapper)
REF = 'luma/1/aarch64/nightly'
C = 'a' * 64


def manifest(repo, candidate=C):
    return dict(schema=reuse.SCHEMA, source_revision='b'*40, candidate=candidate,
                architecture='aarch64', profile='arm-preview', ref=REF,
                config=(repo/'config').read_text(), heads={REF: candidate},
                directories={'':reuse.attributes(repo), **{p.relative_to(repo).as_posix():reuse.attributes(p) for p in repo.rglob('*') if p.is_dir()}},
                metadata_roots=sorted(p.name for p in repo.iterdir() if p.name != 'objects'),
                metadata_bodies={p.relative_to(repo).as_posix(): dict(bytes=p.stat().st_size, sha256=reuse.digest(p), **reuse.attributes(p))
                                 for p in repo.rglob('*') if p.is_file() and 'objects' not in p.relative_to(repo).parts},
                objects={p.relative_to(repo).as_posix(): dict(bytes=p.stat().st_size, sha256=reuse.digest(p), **reuse.attributes(p))
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
            before=manifest(repo);body.chmod(0o400);after=manifest(repo)
            self.assertIn(body.relative_to(repo).as_posix(),reuse.plan(before,after)['replace'])

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
            newrepo=root/'new/repo';shutil.copytree(oldrepo,newrepo);(newrepo/extra.relative_to(oldrepo)).unlink();(newrepo/extra.parent.relative_to(oldrepo)).rmdir();(newrepo/'refs/heads/ostree-metadata').unlink()
            (newrepo/f'objects/aa/{"a"*62}.commitmeta').write_bytes(b'replacement signature')
            (newrepo/'summary').write_bytes(b'fresh summary');(newrepo/'objects/aa').chmod(0o700);new=manifest(newrepo)
            options=root/'commands';op=reuse.write_options(newrepo,old,new,options)
            self.assertEqual(op['remove'],[extra.relative_to(oldrepo).as_posix()])
            out=root/'new.iso';self.run_iso('-indev',donor,'-outdev',out,'-options_from_file',options)
            extracted=root/'extracted';self.run_iso('-osirrox','on','-indev',out,'-extract','/luma/repo',extracted)
            reuse.verify_iso_inventory(out,new)
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

    def test_prior_index_only_reuses_unchanged_file_identity(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d)/'repo';p=fixture(repo)
            before=reuse.capture_index(repo,'b'*40,C,'aarch64','arm-preview',REF)
            second=reuse.capture_index(repo,'b'*40,C,'aarch64','arm-preview',REF,before)
            self.assertEqual(second['actual_body_read_bytes'],0)
            body=p.read_bytes();p.write_bytes(bytes([body[0]^1])+body[1:])
            changed=reuse.capture_index(repo,'b'*40,C,'aarch64','arm-preview',REF,before)
            self.assertGreater(changed['actual_body_read_bytes'],0)
            self.assertNotEqual(changed['objects'][p.relative_to(repo).as_posix()]['sha256'],before['objects'][p.relative_to(repo).as_posix()]['sha256'])

    def test_shared_candidate_cache_cannot_be_used_as_payload_custody(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d)/'repo';fixture(repo)
            foreign=repo/'refs/heads/luma/1/x86_64/nightly'
            foreign.parent.mkdir(parents=True);foreign.write_text('e'*64+'\n')
            before=foreign.read_bytes()
            cache=reuse.capture_index(repo,'b'*40,C,'aarch64','arm-preview',REF,scope='candidate-cache')
            self.assertEqual(foreign.read_bytes(),before)
            with self.assertRaisesRegex(ValueError,'new scoped payload capture'):
                reuse.manifest_from_index(repo,cache)
            with self.assertRaisesRegex(ValueError,'heads'):
                reuse.capture_index(repo,'b'*40,C,'aarch64','arm-preview',REF)
            # Normal selected media generation, not cache capture, owns scoping.
            payload=Path(d)/'payload';shutil.copytree(repo,payload)
            (payload/foreign.relative_to(repo)).unlink()
            scoped=reuse.capture_index(payload,'b'*40,C,'aarch64','arm-preview',REF,cache)
            self.assertEqual(scoped['scope'],'payload')
            reuse.manifest_from_index(payload,scoped)
            self.assertEqual(foreign.read_bytes(),before)

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
            old_index=reuse.capture_index(repo,'b'*40,oldcommit,'aarch64','arm-preview',REF)
            donor_receipt=reuse.verify_iso_inventory(donor,old);receipt_path=r/'donor-receipt.json';reuse.save_private(receipt_path,donor_receipt)
            receipt_sha=reuse.digest(receipt_path)

            (tree/'body').write_text('second');newcommit=commit();new=manifest(repo,newcommit);options=r/'options'
            old_index_path=r/'old-index.json';reuse.save_private(old_index_path,old_index)
            cli_index=r/'cli-index.json'
            cli_pin=run('python3',ROOT/'scripts/install/atlas-iso/payload-object-reuse.py',
                        '--repository',repo,'--output',cli_index,'--source-revision','c'*40,
                        '--candidate',newcommit,'--architecture','aarch64','--profile','arm-preview',
                        '--ref',REF,'--previous',old_index_path,'--previous-sha256',reuse.digest(old_index_path)).strip()
            new_index=reuse.load_pinned(cli_index,cli_pin,reuse.INDEX_SCHEMA)
            # Exercise the exporter cache -> selected signed payload boundary
            # with a real shared repository and full OSTree fsck on both sides.
            foreign_ref='luma/1/x86_64/nightly'
            run('ostree','--repo='+str(repo),'refs','--create='+foreign_ref,oldcommit)
            foreign_before=(repo/'refs/heads'/foreign_ref).read_bytes()
            cache_path=r/'shared-cache.json'
            cache_pin=run('python3',ROOT/'scripts/install/atlas-iso/payload-object-reuse.py',
                         '--scope','candidate-cache','--repository',repo,'--output',cache_path,
                         '--source-revision','c'*40,'--candidate',newcommit,
                         '--architecture','aarch64','--profile','arm-preview','--ref',REF).strip()
            cache=reuse.load_pinned(cache_path,cache_pin,reuse.INDEX_SCHEMA)
            with self.assertRaisesRegex(ValueError,'new scoped payload capture'):
                reuse.manifest_from_index(repo,cache)
            scoped_repo=r/'selected-repo'
            run('ostree','--repo='+str(scoped_repo),'init','--mode=archive')
            run('ostree','--repo='+str(scoped_repo),'pull-local',repo,newcommit)
            run('ostree','--repo='+str(scoped_repo),'refs','--create='+REF,newcommit)
            run('ostree','--repo='+str(scoped_repo),'summary','--update','--gpg-sign='+fpr,'--gpg-homedir='+str(home))
            scoped_index=r/'selected-index.json'
            scoped_pin=run('python3',ROOT/'scripts/install/atlas-iso/payload-object-reuse.py',
                           '--repository',scoped_repo,'--output',scoped_index,
                           '--source-revision','c'*40,'--candidate',newcommit,
                           '--architecture','aarch64','--profile','arm-preview','--ref',REF,
                           '--previous',cache_path,'--previous-sha256',cache_pin).strip()
            scoped=reuse.load_pinned(scoped_index,scoped_pin,reuse.INDEX_SCHEMA)
            self.assertTrue(scoped['ostree_full_fsck_verified'])
            self.assertEqual(reuse.manifest_from_index(scoped_repo,scoped)['heads'],{REF:newcommit})
            self.assertEqual((repo/'refs/heads'/foreign_ref).read_bytes(),foreign_before)
            # Keep the original test payload explicitly scoped for its ISO checks.
            run('ostree','--repo='+str(repo),'refs','--delete',foreign_ref)

            self.assertGreater(new_index['reused_unchanged_inode_bodies'],0)
            # A complete, fsck-verified capture cannot become an incomplete
            # payload by dropping a reachable body before manifest construction.
            missing = next(p for p in (repo/'objects').rglob('*.filez'))
            missing_body = missing.read_bytes(); missing_mode = missing.stat().st_mode & 0o7777
            missing.unlink()
            with self.assertRaisesRegex(ValueError, 'object set differs'):
                reuse.manifest_from_index(repo, new_index)
            missing.write_bytes(missing_body); missing.chmod(missing_mode)
            index_path=r/'new-index.json';reuse.save_private(index_path,new_index);index_sha=reuse.digest(index_path)
            reuse.verify_signed_metadata(repo,REF,newcommit,key,r/'metadata-proof')
            signature=(repo/'summary.sig').read_bytes();(repo/'summary.sig').write_bytes(b'invalid signature')
            with self.assertRaises(subprocess.CalledProcessError):
                reuse.verify_signed_metadata(repo,REF,newcommit,key,r/'bad-signature-proof')
            (repo/'summary.sig').write_bytes(signature)

            actual_manifest,operation=reuse.authenticated_plan(donor,receipt_path,receipt_sha,repo,index_path,index_sha,options)
            self.assertEqual(actual_manifest['objects'],new['objects']);self.assertTrue(operation['reuse'])
            payload=r/'luma';payload.mkdir();(payload/'repo').symlink_to(repo,target_is_directory=True)
            out=r/'new.iso'
            command=wrapper.replace_payload_command(['xorriso','-indev',str(donor),'-outdev',str(out),'-map',str(payload),'luma'],payload_options=options)
            subprocess.run(command+['-commit','--','-end'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            final_receipt=reuse.verify_iso_inventory(out,actual_manifest)
            self.assertEqual(final_receipt['iso_sha256'],reuse.digest(out))
            # The maintained first-donor path verifies actual output bytes too.
            cli_receipt=r/'cli-body-receipt.json'
            cli_receipt_pin=run('python3',ROOT/'scripts/install/atlas-iso/verify-payload-bodies.py',
                                '--iso',out,'--repository',repo,'--body-index',index_path,
                                '--body-index-sha256',index_sha,'--output',cli_receipt).strip()
            self.assertEqual(reuse.load_pinned(cli_receipt,cli_receipt_pin,reuse.RECEIPT_SCHEMA)['iso_sha256'],reuse.digest(out))
            # Full ISO custody detects substitution before any reuse is planned.
            with donor.open('ab') as stream:stream.write(b'changed')
            with self.assertRaisesRegex(ValueError,'donor ISO'):
                reuse.authenticated_plan(donor,receipt_path,receipt_sha,repo,index_path,index_sha,r/'bad-plan')

            extracted=r/'extracted';self.run_iso('-osirrox','on','-indev',out,'-extract','/luma/repo',extracted)
            run('ostree','--repo='+str(extracted),'fsck','--all')
            client=r/'client';run('ostree','--repo='+str(client),'init','--mode=archive')
            run('ostree','--repo='+str(client),'remote','add','--gpg-import='+str(key),'--set=gpg-verify-summary=true','fixture',extracted.as_uri())
            run('ostree','--repo='+str(client),'pull','fixture',REF)
            self.assertEqual(run('ostree','--repo='+str(client),'rev-parse','fixture:'+REF).strip(),newcommit)
            self.assertEqual(run('ostree','--repo='+str(client),'cat',newcommit,'/body'),'second')
            run('ostree','--repo='+str(client),'fsck','--all')
