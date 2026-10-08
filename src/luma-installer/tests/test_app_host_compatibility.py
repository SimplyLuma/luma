# SPDX-License-Identifier: Apache-2.0
import unittest
from luma_installer.app_host_compatibility import require_host_compatibility
from luma_installer.depot_flatpak import SourceUnavailable

class HostCompatibility(unittest.TestCase):
    def test_same_baseline_accepts_app_updates_but_new_host_api_is_held(self):
        require_host_compatibility('[X-Luma]\nmin-host-installer=55\n', supported=55)
        require_host_compatibility('[X-Luma]\nmin-host-installer=54\n', supported=55)
        with self.assertRaisesRegex(SourceUnavailable, 'Keep the current app'):
            require_host_compatibility('[X-Luma]\nmin-host-installer=56\n', supported=55)

    def test_malformed_or_ambiguous_signed_floor_is_not_ignored(self):
        for text in ('[X-Luma]\nmin-host-installer=0\n', '[X-Luma]\nmin-host-installer=+55\n',
                     '[X-Luma]\nmin-host-installer=55;56\n',
                     '[X-Luma]\nmin-host-installer=55\nmin-host-installer=54\n',
                     '[X-Luma]\nmin-host-installer=9999999\n', 'x'*65537):
            with self.subTest(text=text[:80]), self.assertRaises(SourceUnavailable):
                require_host_compatibility(text)

    def test_app_without_luma_host_requirement_keeps_normal_distribution_behavior(self):
        require_host_compatibility('[Application]\nname=org.example.App\n')

    def test_actual_fixed_host_api_floors_preserve_old_deployment(self):
        calls=[]
        def host(component):calls.append(component);return {'core':93,'continuity':63,'background':5}[component]
        require_host_compatibility('[X-Luma]\nmin-host-installer=56\nmin-host-core=93\nmin-host-continuity=63\nmin-host-background=5\n', host_release=host)
        self.assertEqual(calls,['core','continuity','background'])
        for declaration in ('min-host-core=94','min-host-continuity=64','min-host-background=6','min-host-arbitrary=1','min-host-core=+93'):
            with self.subTest(declaration=declaration),self.assertRaisesRegex(SourceUnavailable,'Keep the current app'):
                require_host_compatibility('[X-Luma]\n'+declaration+'\n',host_release=host)

    def test_charlie_fixed_host_floor_and_current_installer_generation(self):
        from luma_installer.app_host_compatibility import HOST_PACKAGES, SUPPORTED_INSTALLER_RELEASE
        self.assertGreaterEqual(SUPPORTED_INSTALLER_RELEASE,60)
        self.assertEqual(HOST_PACKAGES['charlie'],'luma-charlie')
        require_host_compatibility('[X-Luma]\nmin-host-installer=59\nmin-host-charlie=5\n',host_release=lambda component: 5)
        with self.assertRaises(SourceUnavailable):
            require_host_compatibility('[X-Luma]\nmin-host-installer=59\nmin-host-charlie=5\n',host_release=lambda component: 4)

    def test_directory_service_floor_is_supported_and_future_floor_is_held(self):
        from luma_installer.app_host_compatibility import SUPPORTED_INSTALLER_RELEASE
        require_host_compatibility('[X-Luma]\nmin-host-installer=60\n')
        with self.assertRaises(SourceUnavailable):
            require_host_compatibility('[X-Luma]\nmin-host-installer=60\n', supported=59)
        with self.assertRaises(SourceUnavailable):
            require_host_compatibility('[X-Luma]\nmin-host-installer='+str(SUPPORTED_INSTALLER_RELEASE+1)+'\n')

    def test_fixed_viola_native_interface_floor_and_rpm_identity(self):
        from luma_installer.app_host_compatibility import installed_host_release
        from types import SimpleNamespace
        calls=[]
        def run(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0,stdout='151.0.7922.72-8.viola8210.native31.toolbar20261007.1\n')
        self.assertEqual(installed_host_release('viola',run),31)
        self.assertEqual(calls[0][-1],'viola-browser-stable')
        require_host_compatibility('[X-Luma]\nmin-host-installer=61\nmin-host-viola=31\n',host_release=lambda component:31)
        with self.assertRaises(SourceUnavailable):
            require_host_compatibility('[X-Luma]\nmin-host-installer=61\nmin-host-viola=31\n',host_release=lambda component:30)
        for value in ('garbage','151.0.7922.72-8.viola8210.native31\n151.0.7922.72-8.viola8210.native32\n',
                      '151.0.7922.72-8.viola8210.native0\n','151.0.7922.72-8.viola8210.native31\nextra'):
            self.assertEqual(installed_host_release('viola',lambda *_a,**_k:SimpleNamespace(returncode=0,stdout=value)),0)

    def test_fixed_rpm_query_and_missing_ambiguous_host_fail_closed(self):
        from luma_installer.app_host_compatibility import installed_host_release
        from types import SimpleNamespace
        observed=[]
        def run(args,**kwargs):observed.append(args);return SimpleNamespace(returncode=0,stdout='0.1.0-1.luma.93.creator20261007.1.fc44\n')
        self.assertEqual(installed_host_release('core',run),93)
        self.assertEqual(observed[0][-1],'prairie-core-apps')
        self.assertEqual(observed[0][0],'rpm')
        for text in ('','garbage','0.1.0-1.luma.93.creator.fc44\n0.1.0-1.luma.94.creator.fc44\n'):
            self.assertEqual(installed_host_release('core',lambda *_a,**_k:SimpleNamespace(returncode=0,stdout=text)),0)
        self.assertEqual(installed_host_release('continuity',lambda *_a,**_k:SimpleNamespace(returncode=0,stdout='0.1.0-0.63.experiment.fc44\n')),63)
