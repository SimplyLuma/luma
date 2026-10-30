#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Build the source-owned Android integration fixture with verified tools.

Execute in the isolated JDK builder. No SDK image, Google services or private
APK is needed. The generated key and APK are test artifacts, never OS payloads.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tools', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--fixture', choices=('integration', 'provider'), default='integration')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    source = root / ('tests/android/app' if args.fixture == 'integration'
                     else 'tests/android/provider-probe')
    package = ('org.projectluma.androidtest' if args.fixture == 'integration'
               else 'org.projectluma.androidproviderprobe')
    manifest = json.loads((root / 'config/android/acceptance-tools.json').read_text())
    for name in ('android.jar', 'aapt2.jar', 'r8.jar'):
        path = args.tools / name
        expected = manifest['artifacts'][name]
        if path.stat().st_size != expected['size'] or digest(path) != expected['sha256']:
            raise ValueError('Android fixture tool does not match its manifest: ' + name)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)

    def run(*command):
        subprocess.run([str(item) for item in command], check=True)

    with tempfile.TemporaryDirectory(dir=args.output, prefix='compile-') as directory:
        work = Path(directory)
        with zipfile.ZipFile(args.tools / 'aapt2.jar') as archive:
            (work / 'aapt2').write_bytes(archive.read('aapt2'))
        (work / 'aapt2').chmod(0o700)
        unsigned = work / 'unsigned.apk'
        run(work / 'aapt2', 'link', '--manifest', source / 'AndroidManifest.xml',
            '-I', args.tools / 'android.jar', '-o', unsigned)
        classes, dex = work / 'classes', work / 'dex'
        classes.mkdir()
        dex.mkdir()
        run('javac', '--release', '8', '-classpath', args.tools / 'android.jar',
            '-d', classes, *sorted(source.glob('*.java')))
        compiled = work / 'classes.jar'
        run('jar', 'cf', compiled, '-C', classes, '.')
        run('java', '-cp', args.tools / 'r8.jar', 'com.android.tools.r8.D8',
            '--min-api', '26', '--lib', args.tools / 'android.jar', '--output', dex, compiled)
        with zipfile.ZipFile(unsigned, 'a', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(dex / 'classes.dex', 'classes.dex')
        key = work / 'fixture.jks'
        run('keytool', '-genkeypair', '-storetype', 'JKS', '-keystore', key,
            '-storepass', 'fixture-only', '-keypass', 'fixture-only', '-alias', 'fixture',
            '-dname', 'CN=Luma Android integration fixture', '-keyalg', 'RSA', '-validity', '30')
        apk = args.output / ('AndroidIntegrationTest.apk' if args.fixture == 'integration'
                             else 'AndroidProviderProbe.apk')
        run('jarsigner', '-keystore', key, '-storepass', 'fixture-only',
            '-keypass', 'fixture-only', '-signedjar', apk, unsigned, 'fixture')
        run('jarsigner', '-verify', apk)
        report = {'schema': 1, 'package': package,
                  'apk_sha256': digest(apk), 'apk_size': apk.stat().st_size,
                  'source': {path.name: digest(path) for path in sorted(source.iterdir()) if path.is_file()},
                  'tools': manifest, 'qualification': 'build/signature only; no execution claim'}
        (args.output / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
        print(apk)


if __name__ == '__main__':
    main()
