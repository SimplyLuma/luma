import unittest
from unittest.mock import patch
from luma_continuity.updates import deployment_status, correlate_release, read_status


class UpdateStatusTests(unittest.TestCase):
    def manifest(self, commit='b' * 64):
        return dict(schema='org.projectluma.update-release/v1', channel='recent',
                    ref='luma/44/aarch64/recent', ostree_commit=commit,
                    accepted_deployment_commit='d' * 64, previous_commit=None,
                    source_revision='e' * 40, image_sha256='f' * 64,
                    build_report_sha256='c' * 64)

    def status(self, rows):
        return deployment_status({'deployments': rows}, architecture='aarch64', observed_at=100)

    def test_staged_is_not_booted_and_retained_is_not_rollback_event(self):
        status = self.status([{'checksum': 'b' * 64, 'staged': True},
                              {'checksum': 'a' * 64, 'booted': True},
                              {'checksum': 'c' * 64, 'pinned': True}])
        self.assertEqual(status['booted_checksum'], 'a' * 64)
        self.assertIsNone(status['rollback_event'])
        self.assertEqual(len(status['retained_deployments']), 1)
        self.assertEqual(correlate_release(status, self.manifest(), signature_verified=True)['relation'], 'staged')

    def test_finalized_pending_is_not_current_or_staged(self):
        status = self.status([{'checksum': 'b' * 64}, {'checksum': 'a' * 64, 'booted': True}])
        self.assertIsNone(status['staged_checksum'])
        self.assertEqual(correlate_release(status, self.manifest(), signature_verified=True)['relation'], 'pending_reboot')

    def test_exported_commit_not_accepted_builder_commit_defines_running_release(self):
        status = self.status([{'checksum': 'd' * 64, 'booted': True}])
        self.assertEqual(correlate_release(status, self.manifest(), signature_verified=True)['relation'], 'not_booted_or_pending')
        self.assertEqual(correlate_release(status, self.manifest('d' * 64), signature_verified=True)['relation'], 'booted')

    def test_unverified_wrong_arch_and_stale_do_not_claim_release(self):
        status = self.status([{'checksum': 'b' * 64, 'booted': True}])
        self.assertEqual(correlate_release(status, self.manifest())['relation'], 'unverified')
        self.assertEqual(correlate_release({**status, 'architecture': 'x86_64'}, self.manifest(), signature_verified=True)['relation'], 'incompatible_architecture')
        self.assertEqual(correlate_release({**status, 'stale': True}, self.manifest(), signature_verified=True)['relation'], 'unknown')

    def test_missing_backend_is_unavailable_and_failed_refresh_preserves_stale(self):
        with patch('luma_continuity.updates._command', side_effect=FileNotFoundError):
            self.assertFalse(read_status()['available'])
            prior = self.status([{'checksum': 'b' * 64, 'booted': True}])
            failed = read_status(previous=prior)
            self.assertTrue(failed['stale']); self.assertEqual(failed['observed_at'], 100)

    def test_invalid_flags_and_ambiguous_boot_are_rejected(self):
        for rows in ([{'checksum': 'a' * 64, 'booted': 'true'}],
                     [{'checksum': 'a' * 64, 'booted': True, 'staged': True}],
                     [{'checksum': 'a' * 64, 'booted': True}, {'checksum': 'b' * 64, 'booted': True}]):
            with self.assertRaises(ValueError): self.status(rows)

    def test_private_deployment_fields_are_not_returned(self):
        status = self.status([{'checksum': 'a' * 64, 'booted': True,
                               'origin': 'https://synthetic-token@example.test', 'requested-packages': ['private']}])
        self.assertNotIn('synthetic-token', str(status)); self.assertNotIn('requested-packages', str(status))

    def test_layered_base_match_is_not_exact_release_parity(self):
        status = self.status([{'checksum': 'a' * 64, 'base-checksum': 'b' * 64, 'booted': True}])
        self.assertEqual(status['booted_base_checksum'], 'b' * 64)
        self.assertEqual(correlate_release(status, self.manifest(), signature_verified=True)['relation'],
                         'booted_base_with_local_changes')
