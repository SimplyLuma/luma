# SPDX-License-Identifier: Apache-2.0
"""The general installer must present Android inspection results and ABI advice."""
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
from luma_installer.facts import compatibility_report
from luma_installer.inspectors import architecture_is_compatible
from luma_installer.model import PackageReport

class AndroidPreviewFacts(unittest.TestCase):
    def setUp(self):
        package=types.ModuleType('luma_android');package.__path__=[]
        apk=types.ModuleType('luma_android.apk');config=types.ModuleType('luma_android.config')
        self.inspection=types.SimpleNamespace(package_format='apk',native_abis=('arm64-v8a',),display_name='download.apk')
        apk.inspect_android_package=Mock(return_value=self.inspection)
        apk.architecture_notice=Mock(return_value='Choose an x86 or universal build for this device.')
        apk.processor_summary=Mock(return_value='This APK contains ARM64 native libraries.')
        config.load_runtime_config=Mock(return_value=types.SimpleNamespace(apk_max_bytes=4096))
        self.modules=patch.dict(sys.modules,{'luma_android':package,'luma_android.apk':apk,'luma_android.config':config})
        self.modules.start();self.addCleanup(self.modules.stop);self.apk=apk
        self.metadata=patch('luma_installer.apk_metadata.metadata_for_package',return_value={
            'label':'F-Droid','icon':'/owned/artwork.png','version':'2.0.2','package':'org.fdroid.fdroid'})
        self.reader=self.metadata.start();self.addCleanup(self.metadata.stop)

    def report(self):return compatibility_report(Path('/owned/download.apk'),'android',123,'a'*64)

    def test_general_installer_shows_version_and_architecture_warning(self):
        report=self.report()
        self.assertEqual(report.title,'F-Droid');self.assertEqual(report.version,'2.0.2')
        self.assertEqual(report.architecture,'arm64-v8a')
        self.assertEqual(report.warnings,('Choose an x86 or universal build for this device.',))
        self.assertEqual(report.details['Package identifier'],'org.fdroid.fdroid')
        self.assertFalse(report.verified)
        self.apk.inspect_android_package.assert_called_once_with(Path('/owned/download.apk'),4096)

    def test_optional_metadata_failure_keeps_validated_package_and_abi_advice(self):
        self.reader.side_effect=ValueError('invalid optional resource metadata')
        report=self.report()
        self.assertEqual(report.title,'download.apk');self.assertEqual(report.version,'')
        self.assertTrue(report.warnings)
        self.assertEqual(report.details['Processor compatibility'],'This APK contains ARM64 native libraries.')

    def test_package_set_does_not_treat_outer_archive_as_one_manifest(self):
        self.inspection.package_format='apks';self.inspection.native_abis=()
        self.apk.architecture_notice.return_value=''
        report=self.report()
        self.reader.assert_not_called()
        self.assertEqual(report.architecture,'universal');self.assertEqual(report.warnings,())

    def test_android_abi_installation_authority_remains_with_runtime(self):
        for host in ('x86_64', 'aarch64'):
            for abi in ('universal', 'arm64-v8a', 'armeabi-v7a', 'x86', 'x86_64'):
                with self.subTest(host=host, abi=abi), patch('luma_installer.inspectors.host_architecture', return_value=host):
                    report = PackageReport(Path('/owned/app.apk'), 'android', 'App', '', 1, 'a'*64, abi)
                    self.assertTrue(architecture_is_compatible(report))
        # Host-native packages still require a matching machine architecture.
        with patch('luma_installer.inspectors.host_architecture', return_value='x86_64'):
            report = PackageReport(Path('/owned/app.rpm'), 'rpm', 'App', '', 1, 'a'*64, 'aarch64')
            self.assertFalse(architecture_is_compatible(report))

if __name__=='__main__':unittest.main()
