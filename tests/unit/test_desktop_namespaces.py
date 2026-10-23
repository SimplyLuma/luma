# SPDX-License-Identifier: Apache-2.0
"""A shared OS repo must keep architecture-specific metadata and policy apart."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('desktop_paths', ROOT / 'scripts/os/lib/desktop_paths.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

class DesktopNamespaces(unittest.TestCase):
    def test_paths_preserve_x86_and_separate_arm(self):
        self.assertEqual(str(p.architecture_path('/pipeline/media')), '/pipeline/media')
        self.assertEqual(str(p.architecture_path('/pipeline/media', 'aarch64')), '/pipeline/media/aarch64')
        self.assertEqual(str(p.releases_path('/repo', 'aarch64')), '/repo/luma/releases/aarch64')
        self.assertEqual(p.graph_prefix('x86_64'), 'os/graph')
        self.assertEqual(p.graph_prefix('aarch64'), 'os/graph/aarch64')
        with self.assertRaises(ValueError):
            p.graph_prefix('../x86_64')

    def test_manifest_ref_cannot_lie_about_architecture(self):
        self.assertEqual(p.manifest_architecture({}), 'x86_64')
        with self.assertRaises(ValueError):
            p.manifest_architecture({'architecture': 'aarch64', 'ref': 'luma/1/x86_64/nightly'})
        with self.assertRaises(ValueError):
            p.manifest_architecture({'architecture': 'armv7l'})

    def test_build_record_restores_target_before_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            record = Path(folder) / 'build.env'
            record.write_text('LUMA_OS_ARCH=aarch64\nLUMA_OS_PROFILE=arm-preview\nLUMA_OS_GRAPH_PREFIX=os/graph\n')
            command = '. scripts/os/lib/common.sh; luma_os_load_build_env "$1"; printf "%s\\n" "$LUMA_OS_GRAPH_PREFIX" "$LUMA_OS_HUB_GRAPH_URL_TEMPLATE"; luma_os_channel_ref nightly; luma_os_arch_path /pipeline/media'
            run = subprocess.run(['bash', '-ec', command, 'test', str(record)], cwd=ROOT, capture_output=True, text=True, check=True)
            self.assertEqual(run.stdout.splitlines(), ['os/graph/aarch64', 'https://hub.simplyluma.com/api/updates/graph/<channel>?arch=aarch64', 'luma/1/aarch64/nightly', '/pipeline/media/aarch64'])
            record.write_text('LUMA_OS_ARCH=armv7l\n')
            self.assertNotEqual(subprocess.run(['bash', '-ec', command, 'test', str(record)], cwd=ROOT, capture_output=True).returncode, 0)

    def test_graph_render_selects_only_actual_architecture(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            for arch, commit in [('x86_64', 'a' * 64), ('aarch64', 'b' * 64)]:
                directory = p.releases_path(repo, arch) / '1.0.0-nightly.20261022.1'
                directory.mkdir(parents=True)
                (directory / 'manifest.json').write_text(json.dumps({'version': directory.name, 'channel': 'nightly', 'architecture': arch, 'ref': f'luma/1/{arch}/nightly', 'commit': commit, 'published_utc': '2026-10-22T00:00:00Z', 'build_id': '20261022.1'}))
                obj = repo / 'objects' / commit[:2] / (commit[2:] + '.commit')
                obj.parent.mkdir(parents=True)
                obj.write_bytes(b'test-only commit presence')
            for arch, commit in [('x86_64', 'a' * 64), ('aarch64', 'b' * 64)]:
                run = subprocess.run(['python3', 'scripts/os/lib/graph_from_releases.py', str(repo), 'nightly', arch], cwd=ROOT, capture_output=True, text=True, check=True)
                value = json.loads(run.stdout)
                self.assertEqual(value['arch'], arch)
                self.assertEqual([r['commit'] for r in value['releases']], [commit])
            self.assertNotEqual(subprocess.run(['python3', 'scripts/os/lib/graph_from_releases.py', str(repo), 'nightly', 'armv7l'], cwd=ROOT, capture_output=True).returncode, 0)

    def test_actual_media_entry_uses_selected_architecture(self):
        source = (ROOT / 'scripts/os/nightly-media.sh').read_text()
        body = source.split("<<'ENTRY'\n", 1)[1].split('\nENTRY', 1)[0]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'entry.json'
            args = [str(output), 'nightly', '20261022.1', '1.0.0-nightly.20261022.1', 'b' * 64,
                    'luma-nightly-20261022.1.iso', 'c' * 64, '100', 'https://dl.simplyluma.com/media/aarch64/nightly',
                    '0', '2026-10-23 00:00:00 +0000', '2026-10-22', '', 'General bug fixes.', 'Luma test',
                    str(ROOT / 'scripts/os/lib'), 'aarch64', 'arm-preview']
            subprocess.run(['python3', '-', *args], input=body, text=True, check=True)
            value = json.loads(output.read_text())
            self.assertEqual((value['arch'], value['profile']), ('aarch64', 'arm-preview'))
            self.assertIn('/media/aarch64/nightly/', value['url'])

    def test_actual_media_index_rejects_cross_arch_entry(self):
        source = (ROOT / 'scripts/os/nightly-media.sh').read_text()
        body = source.split("<<'INDEX' || fail 'writing the media index failed'\n", 1)[1].split('\nINDEX', 1)[0]
        with tempfile.TemporaryDirectory() as folder:
            tree = Path(folder)
            (tree / 'luma-nightly-20261022.1.iso.json').write_text(json.dumps({'arch': 'x86_64'}))
            args = [str(tree), 'nightly', 'latest.iso', 'https://dl.simplyluma.com/media/aarch64/nightly', str(tree / 'releases'), 'aarch64', '20261022.1']
            run = subprocess.run(['python3', '-', *args], input=body, text=True, capture_output=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn('another architecture', run.stderr)

    def test_approved_refresh_rejects_cross_arch_publication(self):
        spec = importlib.util.spec_from_file_location('approved_graph', ROOT / 'scripts/os/lib/approved_graph.py')
        approved = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(approved)
        # The paused historical bootstrap retains its x86 default. It must
        # never be silently admitted as an ARM publication.
        release = {'commit': approved.BASELINE, 'version': '1.0.0-beta.1', 'released_at': '2026-10-22T00:00:00Z', 'paused': True}
        manifest = {'commit': approved.BASELINE, 'version': release['version'], 'published_utc': release['released_at'], 'bootstrap': {'paused': True}}
        approved.admitted_release(release, manifest, architecture='x86_64')
        with self.assertRaises(ValueError):
            approved.admitted_release(release, manifest, architecture='aarch64')

    def test_signer_rejects_unknown_arch_before_signing(self):
        run = subprocess.run(['bash', 'scripts/os/sign-update-graph.sh', '--arch', 'armv7l', '--channel', 'nightly'], cwd=ROOT, capture_output=True)
        self.assertNotEqual(run.returncode, 0)

if __name__ == '__main__':
    unittest.main()
