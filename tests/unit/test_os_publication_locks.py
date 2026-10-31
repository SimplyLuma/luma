# SPDX-License-Identifier: Apache-2.0
"""Real inherited file descriptions, subprocess contention and fail-closed maps."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('publication_lock_fds', ROOT/'scripts/os/lib/publication_lock_fds.py')
locks = importlib.util.module_from_spec(spec); spec.loader.exec_module(locks)
CHANNELS = ['nightly', 'beta', 'stable']


class PublicationLocks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root/'locks').mkdir()
        self.streams = {}
        for channel in [*CHANNELS, 'repository']:
            stream = (self.root/'locks'/('publish-'+channel+'.lock')).open('a')
            self.addCleanup(stream.close); self.streams[channel] = stream

    def mapping(self, names=None):
        return {name: self.streams[name].fileno() for name in (names or self.streams)}

    def shell(self, mapping=None, extra=''):
        env = os.environ.copy(); env.update(LUMA_OS_ROOT=str(self.root), PYTHONDONTWRITEBYTECODE='1')
        env.pop('LUMA_TARGET_ARCHITECTURE', None); env.pop('LUMA_OS_PUBLICATION_LOCK_FDS', None)
        if mapping is not None: env['LUMA_OS_PUBLICATION_LOCK_FDS'] = json.dumps(mapping)
        script = 'set -euo pipefail; source "$1/scripts/os/lib/common.sh"; '+extra+'; luma_os_publication_lock'
        return subprocess.run(['bash', '-c', script, 'test', str(ROOT)], env=env,
                              pass_fds=tuple(stream.fileno() for stream in self.streams.values()),
                              text=True, capture_output=True, timeout=10)

    def test_absent_map_unchanged_normal_acquisition(self):
        self.assertEqual(self.shell(extra=':').returncode, 0)

    def test_all_inherited_locks_reuse_the_same_open_descriptions(self):
        for stream in self.streams.values(): fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = self.shell(self.mapping(), ':')
        self.assertEqual(result.returncode, 0, result.stderr)
        # Child exit must not release the outer owner's lock description.
        for name in self.streams:
            fresh = (self.root/'locks'/('publish-'+name+'.lock')).open('a')
            with fresh, self.assertRaises(BlockingIOError): fcntl.flock(fresh, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_partial_map_acquires_other_locks_normally(self):
        fcntl.flock(self.streams['nightly'], fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = self.shell(self.mapping(['nightly']), ':')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_partial_map_channel_fd6_is_not_overwritten_by_repository(self):
        fd = self.streams['nightly'].fileno()
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        extra = f'exec 6>&{fd}; export LUMA_OS_PUBLICATION_LOCK_FDS=\'{{"nightly":6}}\''
        result = self.shell(extra=extra)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_separate_file_description_is_contended_even_when_inode_matches(self):
        original = self.streams['nightly']; fcntl.flock(original, fcntl.LOCK_EX | fcntl.LOCK_NB)
        other = (self.root/'locks/publish-nightly.lock').open('a')
        with other, self.assertRaises(BlockingIOError):
            locks.validate(json.dumps({'nightly': other.fileno()}), self.root, CHANNELS)

    def test_external_process_lock_contention_still_fails_without_map(self):
        fcntl.flock(self.streams['beta'], fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = self.shell(extra=':')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('legacy beta publication', result.stderr)

    def test_wrong_inode_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'another inode'):
            locks.validate(json.dumps({'nightly': self.streams['beta'].fileno()}), self.root, CHANNELS)

    def test_closed_descriptor_fails_closed(self):
        with self.assertRaises(OSError): locks.validate('{"nightly":999999}', self.root, CHANNELS)

    def test_unknown_keys_refused(self):
        with self.assertRaisesRegex(ValueError, 'unknown'):
            locks.validate('{"../../other":3}', self.root, CHANNELS)

    def test_duplicate_json_keys_refused(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            locks.validate('{"nightly":3,"nightly":4}', self.root, CHANNELS)

    def test_duplicate_descriptors_refused(self):
        with self.assertRaisesRegex(ValueError, 'distinct'):
            locks.validate('{"nightly":3,"beta":3}', self.root, CHANNELS)

    def test_noninteger_and_reserved_descriptors_refused(self):
        for value in (True, 2, -1, '3', 3.0, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'integers'):
                locks.validate(json.dumps({'nightly': value}), self.root, CHANNELS)

    def test_nonobject_map_refused(self):
        with self.assertRaisesRegex(ValueError, 'object'): locks.validate('[]', self.root, CHANNELS)

    def test_configured_channel_names_are_bounded(self):
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            locks.validate('{}', self.root, ['../../other'])

    def test_bad_map_is_not_ignored_by_shell_command_substitution(self):
        result = self.shell({'unknown': 3}, ':')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('validation failed', result.stderr)

    def test_second_call_fails_explicitly_instead_of_leaking_or_reopening_fds(self):
        result = self.shell(self.mapping(), 'luma_os_publication_lock')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('already acquired', result.stderr)


if __name__ == '__main__': unittest.main()
