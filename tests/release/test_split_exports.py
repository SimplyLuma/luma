# SPDX-License-Identifier: Apache-2.0
"""Real Git archives and remote branches, not a subprocess mock."""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/release/export-split-projects.py'
spec = importlib.util.spec_from_file_location('split_exports', SCRIPT)
exports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exports)

class SplitExports(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'canonical'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('remote', 'add', 'origin', 'https://example.invalid/canonical.git')
        self.manifest = self.repo / 'config/release/split-projects.json'
        self.manifest.parent.mkdir(parents=True)
        self.project = dict(name='luma-test', branch='products/luma-test',
                            remote_env='LUMA_TEST_REMOTE', paths=['app'])
        self.save_manifest()
        (self.repo / 'app').mkdir()
        (self.repo / 'app' / 'main.py').write_text('print("qualified source")\n')
        (self.repo / 'private.txt').write_text('not an export input\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture source')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        self.patch = patch.object(exports, 'ROOT', self.repo)
        self.patch.start()
    def tearDown(self):
        self.patch.stop()
        self.temporary.cleanup()
    def git(self, *args, cwd=None):
        return subprocess.check_output(('git', *args), cwd=cwd or self.repo, text=True)
    def save_manifest(self):
        self.manifest.write_text(json.dumps(dict(schema=1, projects=[self.project]), indent=2)+'\n')
    def seal_manifest(self):
        self.save_manifest(); self.git('add', '.'); self.git('commit', '-qm', 'change selection')
        return self.git('rev-parse', 'HEAD').strip()
    def test_selection_comes_from_commit_and_receipt_hashes_its_exact_bytes(self):
        raw = self.manifest.read_bytes()
        self.project['paths'] = ['private.txt']
        self.save_manifest()
        projects, digest = exports.committed_projects(self.commit)
        self.assertEqual(projects[0]['paths'], ['app'])
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
    def test_archive_is_reproducible_and_excludes_unselected_files(self):
        projects, digest = exports.committed_projects(self.commit)
        with contextlib.redirect_stdout(None):
            for name in ('one', 'two'):
                exports.export(projects[0], self.commit, self.root/name, False, digest)
        one, = (self.root/'one').rglob('*.tar.gz')
        two, = (self.root/'two').rglob('*.tar.gz')
        self.assertEqual(one.read_bytes(), two.read_bytes())
        with tarfile.open(one) as archive:
            self.assertEqual(set(archive.getnames()), {'app', 'app/main.py', 'LUMA-SOURCE.json'})
            receipt = json.load(archive.extractfile('LUMA-SOURCE.json'))
        self.assertEqual(receipt['canonical_commit'], self.commit)
        self.assertEqual(receipt['selection_manifest_sha256'], digest)
    def test_cli_dirty_selection_cannot_publish_an_unselected_private_file(self):
        self.project['paths'] = ['private.txt']
        self.save_manifest()
        script=self.repo/'scripts/release/export-split-projects.py'
        script.parent.mkdir(parents=True)
        script.write_text(SCRIPT.read_text())
        subprocess.run((sys.executable,str(script),'--source',self.commit,
                        '--output',str(self.root/'out')),check=True,
                       stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        archive_path, = (self.root/'out').rglob('*.tar.gz')
        with tarfile.open(archive_path) as archive:
            self.assertNotIn('private.txt', archive.getnames())
            self.assertIn('app/main.py', archive.getnames())
    def test_missing_selected_path_fails(self):
        self.project['paths'] = ['missing']
        with self.assertRaises(subprocess.CalledProcessError):
            exports.committed_projects(self.seal_manifest())
    def test_product_root_keeps_own_packaging_separate_from_os_packaging(self):
        (self.repo/'app/packaging').mkdir()
        (self.repo/'app/packaging/app.conf').write_text('product-owned\n')
        (self.repo/'packaging').mkdir()
        (self.repo/'packaging/app.conf').write_text('os-owned\n')
        self.project.update(source_root='app', paths=['app','packaging'])
        source=self.seal_manifest()
        projects,digest=exports.committed_projects(source)
        with contextlib.redirect_stdout(None):
            exports.export(projects[0],source,self.root/'out',False,digest)
        archive_path,=(self.root/'out').rglob('*.tar.gz')
        with tarfile.open(archive_path) as archive:
            self.assertEqual(archive.extractfile('main.py').read(),b'print("qualified source")\n')
            self.assertEqual(archive.extractfile('packaging/app.conf').read(),b'product-owned\n')
            self.assertEqual(archive.extractfile('.luma-integration/packaging/app.conf').read(),b'os-owned\n')
            self.assertEqual(json.load(archive.extractfile('LUMA-SOURCE.json'))['source_root'],'app')
    def test_source_root_must_be_a_selected_directory(self):
        self.project.update(source_root='private.txt',paths=['private.txt'])
        with self.assertRaisesRegex(SystemExit,'directory'):
            exports.committed_projects(self.seal_manifest())
    def test_overlapping_paths_are_refused(self):
        self.project['paths']=['app','app/main.py']
        with self.assertRaisesRegex(SystemExit,'overlapping'):
            exports.committed_projects(self.seal_manifest())
    def test_git_pathspec_magic_cannot_export_an_unselected_file(self):
        name=':(top)private.txt'
        (self.repo/name).write_text('literal selected filename\n')
        self.project['paths']=[name]
        source=self.seal_manifest()
        projects,digest=exports.committed_projects(source)
        with contextlib.redirect_stdout(None):
            exports.export(projects[0],source,self.root/'out',False,digest)
        archive_path,=(self.root/'out').rglob('*.tar.gz')
        with tarfile.open(archive_path) as archive:
            self.assertIn(name,archive.getnames())
            self.assertNotIn('private.txt',archive.getnames())
    def test_existing_receipt_cannot_be_overwritten(self):
        (self.repo/'app/LUMA-SOURCE.json').write_text('original provenance\n')
        self.project['source_root']='app'
        source=self.seal_manifest()
        projects,digest=exports.committed_projects(source)
        with self.assertRaisesRegex(SystemExit,'reserved'):
            exports.export(projects[0],source,self.root/'out',False,digest)
    def test_promoted_root_refuses_links_to_relocated_external_inputs(self):
        (self.repo/'docs').mkdir(); (self.repo/'docs/readme.txt').write_text('extra\n')
        (self.repo/'app/manual').symlink_to('../docs/readme.txt')
        self.project.update(source_root='app',paths=['app','docs'])
        source=self.seal_manifest()
        projects,digest=exports.committed_projects(source)
        with self.assertRaisesRegex(SystemExit,'symlink'):
            exports.export(projects[0],source,self.root/'out',False,digest)
    def test_traversal_is_refused(self):
        self.project['paths'] = ['app/../private.txt']
        with self.assertRaisesRegex(SystemExit, 'source path'):
            exports.committed_projects(self.seal_manifest())
    def test_duplicate_projects_are_refused(self):
        self.manifest.write_text(json.dumps(dict(schema=1, projects=[self.project, self.project])))
        self.git('add', '.'); self.git('commit', '-qm', 'duplicate')
        with self.assertRaisesRegex(SystemExit, 'duplicate'):
            exports.committed_projects(self.git('rev-parse', 'HEAD').strip())
    def test_publication_requires_explicit_remote(self):
        tree=self.root/'tree'; tree.mkdir()
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(SystemExit, 'explicit'):
            exports.publish_tree(self.project, tree, self.commit, self.project['branch'])
    def test_remote_access_failure_does_not_create_orphan(self):
        tree=self.root/'tree'; tree.mkdir()
        with patch.dict(os.environ, {'LUMA_TEST_REMOTE': str(self.root/'absent.git')}), self.assertRaisesRegex(SystemExit, 'access failure'):
            exports.publish_tree(self.project, tree, self.commit, self.project['branch'])
        self.assertNotEqual(self.git('symbolic-ref', 'HEAD', cwd=tree.parent/'repository').strip(), 'refs/heads/products/luma-test')
    def test_new_remote_branch_is_real_and_existing_history_is_retained(self):
        remote=self.root/'product.git'
        self.git('init','--bare','-q',str(remote))
        tips=[]
        with patch.dict(os.environ, {'LUMA_TEST_REMOTE': str(remote)}), contextlib.redirect_stdout(None):
            for n in range(2):
                parent=self.root/str(n); parent.mkdir()
                tree=parent/'tree'; tree.mkdir()
                (tree/'main.py').write_text(str(n))
                exports.publish_tree(self.project,tree,self.commit,self.project['branch'])
                tips.append(self.git('rev-parse',self.project['branch'],cwd=remote).strip())
        self.assertEqual(self.git('rev-parse',tips[1]+'^',cwd=remote).strip(), tips[0])
        self.assertEqual(self.git('show',tips[1]+':main.py',cwd=remote), '1')
    def test_publication_preserves_file_and_directory_symlinks(self):
        remote=self.root/'product.git'; self.git('init','--bare','-q',str(remote))
        tree=self.root/'tree'; tree.mkdir()
        (tree/'file').write_text('source\n'); (tree/'directory').mkdir()
        (tree/'directory/inside').write_text('nested source\n')
        (tree/'file-link').symlink_to('file')
        (tree/'directory-link').symlink_to('directory',target_is_directory=True)
        with patch.dict(os.environ,{'LUMA_TEST_REMOTE':str(remote)}),contextlib.redirect_stdout(None):
            exports.publish_tree(self.project,tree,self.commit,self.project['branch'])
        for name,target in [('file-link','file'),('directory-link','directory')]:
            entry=self.git('ls-tree',self.project['branch'],'--',name,cwd=remote)
            self.assertTrue(entry.startswith('120000 '),entry)
            self.assertEqual(self.git('show',self.project['branch']+':'+name,cwd=remote),target)
    def test_existing_directory_symlink_does_not_block_next_publication(self):
        remote=self.root/'product.git'; self.git('init','--bare','-q',str(remote))
        tips=[]
        with patch.dict(os.environ,{'LUMA_TEST_REMOTE':str(remote)}),contextlib.redirect_stdout(None):
            for n in range(2):
                parent=self.root/str(n); parent.mkdir()
                tree=parent/'tree'; tree.mkdir()
                (tree/'real').mkdir(); (tree/'real/file').write_text(str(n))
                (tree/'alias').symlink_to('real',target_is_directory=True)
                exports.publish_tree(self.project,tree,self.commit,self.project['branch'])
                tips.append(self.git('rev-parse',self.project['branch'],cwd=remote).strip())
        self.assertEqual(self.git('rev-parse',tips[1]+'^',cwd=remote).strip(),tips[0])
        self.assertTrue(self.git('ls-tree',tips[1],'--','alias',cwd=remote).startswith('120000 '))

if __name__ == '__main__': unittest.main()
