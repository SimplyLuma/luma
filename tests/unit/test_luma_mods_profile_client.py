#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Actual GI client DTO controls; transport seam is not signed-caller proof."""
import os
import json
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/luma-mods'))
sys.path.insert(0,str(ROOT/'tests/unit'))
from test_luma_mods_profile_boundary import BoundaryTests, MOD, DOMAIN
from luma_mods.profile_client import Client
from luma_mods.errors import LumaModsError
from luma_mods.system_client import SystemTransactionClient
from gi.repository import Gio

class ClientTests(unittest.TestCase):
    def setUp(self):
        self.host = BoundaryTests()
        self.host.setUp()
        self.addCleanup(self.host.doCleanups)
        self.client = Client.__new__(Client)
        self.client.presentation = 'desktop'
        self.client.snapshot = None
        self.client.profiles = {}
        self.client._verifications = {}
        self.client.system_snapshot = None
        self.client._public_catalog = None
        self.client.call = self.host.bridge.dispatch
        self.addCleanup(self.cleanup_catalog)
    def cleanup_catalog(self):
        if self.client._public_catalog: self.client._public_catalog.cleanup()
    def review(self):
        _, catalog = self.client.catalog()
        with patch.dict(os.environ,{'LUMA_PRESENTATION_MODE':'desktop'}):
            return self.client.review(MOD,catalog)
    def test_public_dto_requires_confirmation_and_re_resolves_native_install(self):
        plan, verified, profile = self.review()
        self.assertFalse(verified[MOD].verified)
        with self.assertRaises(LumaModsError): self.client.authority(plan,False)
        authority = self.client.authority(plan,True)
        self.assertEqual(dict(authority.trust_levels),{})
        self.assertIn(MOD,authority.confirmed_unverified)
        self.client.install(plan,profile,authority,expected_generation=0)
        self.assertEqual(self.host.backend._read()[DOMAIN],{'surface':'green-glow'})
        self.client.remove(MOD,expected_generation=1)
        self.assertEqual(self.host.backend._read(),self.host.original)
    def test_host_plan_mismatch_is_refused_before_review_or_authority(self):
        _, catalog = self.client.catalog()
        def tampered(request):
            value = self.host.bridge.dispatch(request)
            if request['operation'] == 'review': value['plan']['composition_sha256'] = '0'*64
            return value
        self.client.call = tampered
        with self.assertRaises(LumaModsError): self.client.review(MOD,catalog)
        self.assertEqual(self.client._verifications,{})
        self.assertEqual(self.host.runtime.store.read()['generation'],0)
    def test_system_staging_and_activation_remain_direct_interactive_system_calls(self):
        calls = []
        class Proxy:
            def call(self,*args): calls.append(args)
        client = SystemTransactionClient.__new__(SystemTransactionClient)
        client.proxy = Proxy()
        client.stage_catalog(MOD,'sha256:'+'a'*64,'b'*64,lambda *_:None)
        client.activate('owned-candidate',lambda *_:None)
        self.assertEqual(calls[0][0],'StageCatalog')
        self.assertEqual(calls[0][1].unpack(),(MOD,'sha256:'+'a'*64,'b'*64,True))
        self.assertEqual(calls[1][0],'Activate')
        self.assertEqual(calls[1][1].unpack(),('owned-candidate',True))
        for args in calls: self.assertEqual(args[2],Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION)

    def test_recovery_enable_gate_protects_actual_root_owned_file_descriptors(self):
        from luma_mods import system_service as service
        # Native package gates run inside the canonical root-owned builder.
        # No installed enable marker, production policy or service is modified.
        self.assertEqual(os.geteuid(),0,'Root file ownership gate requires canonical native builder.')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); marker = root/'enable'; record = root/'readiness.json'
            value = {'schema':'org.luma.mod-recovery-readiness/v0.1',
                     'known_good_checksum':'a'*64,'health_unit':'luma-mod-boot-promote.service',
                     'boot_attempt_limit':2}
            with patch.object(service,'ENABLE_MARKER',marker),patch.object(service,'RECOVERY_RECORD',record):
                self.assertFalse(service._recovery_ready(),'Missing default must remain closed.')
                marker.write_text('owned fixture only'); marker.chmod(0o600)
                record.write_text(json.dumps(value)); record.chmod(0o600)
                self.assertTrue(service._recovery_ready())
                for path in (marker,record):
                    path.chmod(0o622); self.assertFalse(service._recovery_ready()); path.chmod(0o600)
                    os.chown(path,60310,60310)
                    try:self.assertFalse(service._recovery_ready())
                    finally:os.chown(path,0,0)
                original = record.read_bytes(); record.unlink(); record.symlink_to(root/'outside')
                (root/'outside').write_bytes(original)
                self.assertFalse(service._recovery_ready()); record.unlink()
                record.write_bytes(b'x'*65537); record.chmod(0o600)
                self.assertFalse(service._recovery_ready())
                record.write_text('{'); self.assertFalse(service._recovery_ready())
                record.write_text(json.dumps(dict(value,boot_attempt_limit=9)))
                self.assertFalse(service._recovery_ready())

if __name__ == '__main__':
    unittest.main(defaultTest='ClientTests',verbosity=2)
