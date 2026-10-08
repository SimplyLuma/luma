"""An IMS registration outage must not redirect a call into MM voice."""
import unittest
from unittest.mock import patch
from prairie_apps import phone_backend as backend

class VoiceOwnerTests(unittest.TestCase):
    def test_installed_ims_owner_survives_status_discovery_failure(self):
        with patch.object(backend.Path, 'is_file', return_value=True), \
             patch.object(backend, '_run', return_value=''), \
             patch.object(backend, 'ImsVoiceTransport') as ims, \
             patch.object(backend, 'ModemVoiceTransport') as modem:
            self.assertIs(backend.preferred_voice_transport(), ims.return_value)
            modem.assert_not_called()

    def test_registration_loss_keeps_ims_owner(self):
        for registered in ('true', 'false'):
            with self.subTest(registered=registered), \
                 patch.object(backend.shutil, 'which', return_value='/usr/bin/busctl'), \
                 patch.object(backend, '_run', return_value='a{sv} 1 "registered" b '+registered), \
                 patch.object(backend, 'ImsVoiceTransport') as ims, \
                 patch.object(backend, 'ModemVoiceTransport') as modem:
                self.assertIs(backend.preferred_voice_transport(), ims.return_value)
                modem.assert_not_called()

    def test_ims_proxy_failure_does_not_redirect_call(self):
        with patch.object(backend.shutil, 'which', return_value='/usr/bin/busctl'), \
             patch.object(backend, '_run', return_value='a{sv} 1 "registered" b false'), \
             patch.object(backend, 'ImsVoiceTransport', side_effect=RuntimeError('unavailable')), \
             patch.object(backend, 'ModemVoiceTransport') as modem:
            with self.assertRaises(RuntimeError): backend.preferred_voice_transport()
            modem.assert_not_called()

if __name__ == '__main__': unittest.main()
