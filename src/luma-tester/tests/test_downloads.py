# SPDX-License-Identifier: Apache-2.0
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from luma_tester.core import TesterError
from luma_tester.downloads import HubClient, architecture_notice, download


class Response(io.BytesIO):
    url = 'https://media.simplyluma.com/test.qcow2'

    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}

    def read1(self, size=-1):
        return self.read(size)

    def getcode(self):
        return self.status


class DownloadsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'image'
        self.content = b'checked image bytes'
        self.artifact = dict(artifact_id='os/nightly/20261022.1/x86_64',
                             url=Response.url, size_bytes=len(self.content),
                             sha256=hashlib.sha256(self.content).hexdigest())
        self.ticket = dict(self.artifact, artifact=dict(artifact_id=self.artifact['artifact_id']),
                           download_id='dl_testonly')
        self.client = HubClient()
        self.calls = []

        def request(path, body=None):
            self.calls.append((path, body))
            return self.ticket if path == 'tickets' else {'status': 'completed'}

        self.client.request = Mock(side_effect=request)

    def fetch(self, response=None):
        response = response or Response(self.content)
        with patch('luma_tester.downloads.build_opener') as opener:
            opener.return_value.open.return_value = response
            result = download(self.artifact, self.path, client=self.client)
            return result, opener

    def test_progress_tracks_received_bytes_then_verification(self):
        class Chunks(Response):
            def read(self, size=-1):
                return super().read(min(size, 3))
        events = []
        with patch('luma_tester.downloads.build_opener') as opener, patch(
            'luma_tester.transfers.time.monotonic', side_effect=__import__('itertools').count()
        ):
            opener.return_value.open.return_value = Chunks(self.content)
            download(self.artifact, self.path, client=self.client, progress=events.append)
        received = [event.received for event in events if event.phase == 'downloading']
        self.assertEqual(received[0], 0)
        self.assertTrue(any(0 < count < len(self.content) for count in received))
        self.assertEqual(received[-1], len(self.content))
        phases = [event.phase for event in events]
        self.assertLess(phases.index('downloading'), phases.index('verifying'))
        self.assertEqual(events[-1].phase, 'ready')
        self.assertEqual(events[-1].fraction, 1)
        self.assertTrue(all(event.total == len(self.content) for event in events))

    def test_resumed_progress_starts_from_retained_bytes(self):
        with self.assertRaises(URLError):
            self.fetch(Response(self.content[:5]))
        events = []
        with patch('luma_tester.downloads.build_opener') as opener:
            opener.return_value.open.return_value = Response(self.content[5:], 206,
                {'Content-Range': f'bytes 5-{len(self.content)-1}/{len(self.content)}'})
            download(self.artifact, self.path, client=self.client, progress=events.append)
        self.assertEqual(next(event.received for event in events if event.phase == 'downloading'), 5)
        self.assertEqual(events[-1].phase, 'ready')

    def test_failed_verification_never_reports_ready(self):
        events = []
        with patch('luma_tester.downloads.build_opener') as opener:
            opener.return_value.open.return_value = Response(b'x' * len(self.content))
            with self.assertRaisesRegex(TesterError, 'SHA-256 mismatch'):
                download(self.artifact, self.path, client=self.client, progress=events.append)
        self.assertIn('verifying', [event.phase for event in events])
        self.assertNotIn('ready', [event.phase for event in events])
        self.assertFalse(self.path.exists())

    def test_verified_completion_and_cache_do_not_count_again(self):
        self.fetch()
        self.assertEqual(self.path.read_bytes(), self.content)
        self.assertEqual(self.calls[-1], ('tickets/dl_testonly/complete',
            {'sha256': self.artifact['sha256'], 'size_bytes': len(self.content)}))
        calls = list(self.calls)
        self.fetch()
        self.assertEqual(self.calls, calls)
        self.assertEqual(self.calls[0][1]['source'], 'test')
        self.assertEqual(set(self.calls[0][1]), {'artifact_id', 'source', 'purpose',
                                               'experience', 'tester_version', 'host_os', 'host_arch'})

    def test_reporting_unavailable_does_not_block_download(self):
        self.client.request.side_effect = URLError('offline')
        self.fetch()
        self.assertEqual(self.path.read_bytes(), self.content)

    def test_completion_unavailable_does_not_block_use(self):
        def request(path, body=None):
            if path == 'tickets':
                return self.ticket
            raise URLError('report offline')
        self.client.request.side_effect = request
        self.fetch()
        self.assertTrue(self.path.exists())

    def test_checksum_failure_is_reported_and_bad_bytes_deleted(self):
        with self.assertRaisesRegex(TesterError, 'SHA-256 mismatch'):
            self.fetch(Response(b'x' * len(self.content)))
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.with_name('image.part').exists())
        self.assertEqual(self.calls[-1][1]['reason'], 'checksum_mismatch')
        self.assertFalse(any(path.endswith('/complete') for path, _ in self.calls))
        self.fetch()
        self.assertEqual(sum(path == 'tickets' for path, _ in self.calls), 1)

    def test_retry_resumes_same_ticket(self):
        with self.assertRaises(URLError):
            self.fetch(Response(self.content[:5]))
        saved = json.loads(self.path.with_name('image.download.json').read_text())
        self.assertEqual(saved['ticket']['download_id'], 'dl_testonly')
        _, opener = self.fetch(Response(self.content[5:], 206,
                               {'Content-Range': f'bytes 5-{len(self.content)-1}/{len(self.content)}'}))
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header('Range'), 'bytes=5-')
        self.assertEqual(sum(path == 'tickets' for path, _ in self.calls), 1)
        self.assertEqual(self.path.read_bytes(), self.content)

    def test_ignored_range_restarts_with_same_ticket(self):
        with self.assertRaises(URLError):
            self.fetch(Response(self.content[:5]))
        self.fetch()
        self.assertEqual(self.path.read_bytes(), self.content)
        self.assertEqual(sum(path == 'tickets' for path, _ in self.calls), 1)

    def test_invalid_range_never_completes(self):
        with self.assertRaises(URLError):
            self.fetch(Response(self.content[:5]))
        with self.assertRaisesRegex(TesterError, 'range'):
            self.fetch(Response(self.content[5:], 206, {'Content-Range': 'bytes 0-1/2'}))
        self.assertFalse(any(path.endswith('/complete') for path, _ in self.calls))

    def test_ticket_digest_cannot_replace_selected_artifact(self):
        self.ticket['sha256'] = '0' * 64
        self.fetch()
        self.assertEqual(self.path.read_bytes(), self.content)
        self.assertEqual(len(self.calls), 1)

    def test_corrupt_cache_never_counts_or_runs(self):
        self.path.write_bytes(b'bad')
        with self.assertRaisesRegex(TesterError, 'Cached download'):
            self.fetch()
        self.assertEqual(self.calls, [])

    def test_https_downgrade_never_completes(self):
        response = Response(self.content)
        response.url = 'http://example.org/image'
        with self.assertRaisesRegex(TesterError, 'HTTPS'):
            self.fetch(response)
        self.assertFalse(any(path.endswith('/complete') for path, _ in self.calls))

    def test_native_arch_first_and_arm_emulation_explicit(self):
        client = HubClient()
        arm = dict(self.artifact, arch='aarch64')
        x86 = dict(self.artifact, arch='x86_64')
        with patch('luma_tester.downloads.platform.machine', return_value='arm64'), \
                patch.object(client, 'request', return_value={'artifacts': [x86, arm]}):
            self.assertEqual(client.catalog()[0]['arch'], 'aarch64')
        self.assertIn('emulation', architecture_notice({'x86_64'}, 'aarch64'))
        self.assertEqual(architecture_notice({'x86_64', 'aarch64'}, 'aarch64'), '')


if __name__ == '__main__':
    unittest.main()
