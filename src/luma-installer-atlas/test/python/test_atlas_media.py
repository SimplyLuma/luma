# SPDX-License-Identifier: LGPL-2.1-or-later
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

source = Path(os.environ.get('ATLAS_MEDIA_SOURCE', Path(__file__).resolve().parents[2] / 'libexec/atlas-media'))
loader = importlib.machinery.SourceFileLoader('tested_atlas_media', str(source))
spec = importlib.util.spec_from_loader(loader.name, loader)
media = importlib.util.module_from_spec(spec)
loader.exec_module(media)

class MediaActions(unittest.TestCase):
    def restart(self, present, outcome):
        with patch.object(media.os.path, 'isfile', return_value=present), patch.object(media.subprocess, 'run', side_effect=outcome) as run:
            with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
                result = media.prepare_restart()
            return result, json.loads(output.getvalue()), run

    def test_native_restart_before_exit_and_failures_preserved(self):
        result, body, run = self.restart(True, [subprocess.CompletedProcess([], 0)])
        self.assertEqual((result, body), (0, {'ok': True}))
        self.assertEqual(run.call_args.args[0], ['systemctl', 'start', 'plymouth-reboot.service'])
        self.assertEqual(run.call_args.kwargs['timeout'], 3)
        # Non-installer and failed native splash never execute reboot/kill.
        result, body, run = self.restart(False, [subprocess.CompletedProcess([], 0)])
        self.assertEqual(body['reason'], 'not-installer'); run.assert_not_called()
        for failure in [subprocess.CompletedProcess([], 1, '', 'denied'), OSError('missing'), subprocess.TimeoutExpired('systemctl', 3)]:
            result, body, run = self.restart(True, [failure] if isinstance(failure, subprocess.CompletedProcess) else failure)
            self.assertEqual(result, 1); self.assertEqual(body['reason'], 'splash-unavailable')
            self.assertEqual(run.call_count, 1)

    def test_exact_representative_city_and_target_readback(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); zones = base/'tz'; zones.mkdir()
            for zone in ['America/Chicago', 'Asia/Kathmandu', 'UTC']:
                file = zones/zone; file.parent.mkdir(exist_ok=True); file.write_text('tzif fixture')
            (zones/'zone.tab').write_text('US\t+415100-0873900\tAmerica/Chicago\nNP\t+2743+08519\tAsia/Kathmandu\n')
            (zones/'iso3166.tab').write_text('US\tUnited States\nNP\tNepal\n')
            chicago = media.location_record('America/Chicago', str(zones))
            self.assertEqual(chicago['place']['name'], 'Chicago')
            self.assertEqual(chicago['place']['country'], 'United States')
            self.assertAlmostEqual(chicago['place']['longitude'], -87.65)
            kathmandu = media.location_record('Asia/Kathmandu', str(zones))
            self.assertEqual(kathmandu['place']['country'], 'Nepal')
            self.assertAlmostEqual(kathmandu['place']['latitude'], 27+43/60)
            self.assertIsNone(media.location_record('UTC', str(zones))['place'])
            for bad in ['../../etc/passwd', '/etc/passwd', 'America/Missing']:
                with self.assertRaises(ValueError): media.location_record(bad, str(zones))
            target = base/'installed'; (target/'etc').mkdir(parents=True)
            targetzones = target/'usr/share/zoneinfo/Asia'; targetzones.mkdir(parents=True)
            (targetzones/'Kathmandu').write_text('tzif fixture')
            (target/'etc/localtime').symlink_to('../usr/share/zoneinfo/UTC')
            intent = base/'intent.json'; intent.write_text(json.dumps(kathmandu | {'place': {'name': 'Wrong city'}}))
            derive = media.location_record
            with patch.object(media, 'LOCATION_INTENT', str(intent)), patch.object(media, 'location_record', side_effect=lambda zone: derive(zone, str(zones))), patch.object(media.subprocess, 'run'):
                self.assertEqual(media.apply_location(str(target)), 0)
                self.assertEqual(os.readlink(target/'etc/localtime'), '../usr/share/zoneinfo/Asia/Kathmandu')
                self.assertEqual(json.loads((target/'etc/luma/setup-location.json').read_text()), kathmandu)
                with self.assertRaisesRegex(ValueError, 'not an installation'): media.apply_location('/')
                (targetzones/'Kathmandu').unlink()
                with self.assertRaisesRegex(ValueError, 'missing from installed'): media.apply_location(str(target))

if __name__ == '__main__': unittest.main()
