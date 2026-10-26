# SPDX-License-Identifier: Apache-2.0
"""Cross-architecture feeds and origins must never reach a deployment transaction."""
from dataclasses import replace
import unittest

import fakes
from fakes import graph_doc, release
from luma_update.engine import Engine, UpdateError


class Architecture(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()

    def tearDown(self):
        self.rig.close()

    def engine(self, machine, origin_arch):
        self.rig.backend.list[0].origin = f'luma:luma/1/{origin_arch}/stable'
        return Engine(self.rig.paths, self.rig.settings, self.rig.backend,
                      self.rig.probes, self.rig.http, clock=lambda: self.rig.now,
                      arch=machine, greenboot_installed=True)

    def publish(self, arch, *, bucket=None):
        self.rig.publish(graph_doc([release('1.0.0', 1), release('1.0.1', 2)], arch=arch))
        if bucket is not None and bucket != arch:
            # Serve a genuinely signed foreign graph at the native bucket the
            # client requests; publishing only the foreign URL tests a 404.
            source = self.rig.settings.graph('stable', arch)
            target = self.rig.settings.graph('stable', bucket)
            for suffix in ('', '.minisig'):
                self.rig.http.files[target + suffix] = self.rig.http.files[source + suffix]

    def test_matching_native_updates_stage_on_both_architectures(self):
        for arch in ('aarch64', 'x86_64'):
            with self.subTest(arch=arch):
                self.rig.backend.list = self.rig.backend.list[-1:]
                self.rig.backend.calls.clear()
                self.publish(arch)
                engine = self.engine(arch, arch)
                self.assertIsNotNone(engine.check())
                self.assertTrue(engine.download(user_initiated=True))
                self.assertTrue(any(call[0] == 'update' for call in self.rig.backend.calls))

    def test_wrong_signed_feed_is_rejected_in_both_directions(self):
        for machine, other in (('aarch64', 'x86_64'), ('x86_64', 'aarch64')):
            with self.subTest(machine=machine):
                self.publish(other, bucket=machine)
                engine = self.engine(machine, machine)
                self.assertIsNone(engine.check())
                self.assertFalse(engine.download(user_initiated=True))
                self.assertEqual(self.rig.backend.calls, [])
                self.assertEqual(engine.status().last_error_class, 'graph')

    def test_wrong_origin_cannot_choose_a_matching_foreign_feed(self):
        for machine, other in (('aarch64', 'x86_64'), ('x86_64', 'aarch64')):
            with self.subTest(machine=machine):
                self.publish(other)
                engine = self.engine(machine, other)
                self.assertIsNone(engine.check())
                self.assertFalse(engine.download(user_initiated=True))
                self.assertEqual(engine.status().last_error_class, 'architecture')
                self.assertEqual(self.rig.backend.calls, [])

    def test_origin_change_invalidates_cached_download_decision(self):
        self.publish('aarch64')
        engine = self.engine('aarch64', 'aarch64')
        self.assertIsNotNone(engine.check())
        self.rig.backend.list[0].origin = 'luma:luma/1/x86_64/stable'
        self.assertFalse(engine.download(user_initiated=True))
        self.assertIsNone(engine._decision)
        self.assertEqual(engine.status().available_version, '')
        self.assertFalse(engine.status().signature_verified)
        self.assertEqual(self.rig.backend.calls, [])

    def test_unsupported_device_cannot_inherit_supported_origin(self):
        engine = self.engine('riscv64', 'x86_64')
        self.assertIsNone(engine.check())
        self.assertEqual(engine.status().last_error_class, 'architecture')
        self.assertEqual(self.rig.backend.calls, [])

    def test_direct_foreign_graph_request_rejected(self):
        engine = self.engine('aarch64', 'aarch64')
        with self.assertRaises(UpdateError):
            engine.fetch_graph('stable', 'x86_64')

    def test_architecture_placeholder_uses_device_architecture(self):
        self.rig.settings = replace(self.rig.settings,
                                   graph_url='https://example.test/{arch}/{channel}.json')
        engine = self.engine('aarch64', 'aarch64')
        template = self.rig.settings.graph_url
        self.rig.settings = replace(self.rig.settings, graph_url=template.replace('{arch}', 'aarch64'))
        self.publish('aarch64')
        old = self.rig.settings.graph_url.format(arch='aarch64', channel='stable')
        self.assertIsNotNone(engine.check())
        engine.refresh()
        self.assertEqual(engine.status().graph_url, old)


if __name__ == '__main__':
    unittest.main()
