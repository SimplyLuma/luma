# SPDX-License-Identifier: Apache-2.0
"""Release metadata shared by luma-port backends; no network or installation."""
from __future__ import annotations
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

MAX_VERSION_CODE = 2_100_000_000


def android_version_code(version: str, explicit: int | None = None) -> int:
    if explicit is not None:
        if type(explicit) is not int or not 1 <= explicit <= MAX_VERSION_CODE:
            raise ValueError('version_code must be an integer from 1 to 2100000000')
        return explicit
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', version)
    if not match:
        raise ValueError('prerelease versions require an explicit increasing version_code')
    major, minor, patch = map(int, match.groups())
    if major >= 2003 or minor >= 1024 or patch >= 1024:
        raise ValueError('version exceeds the legacy sem121010 range; use version_code')
    code = (major << 20) + (minor << 10) + patch
    if not 1 <= code <= MAX_VERSION_CODE:
        raise ValueError('invalid Android version_code')
    return code


def repository(value: str) -> str:
    # GitHub currently supplies hosting. No API token is shipped to clients.
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', value):
        raise ValueError('updates.repository must be owner/repository')
    return value


def updates_config(value: dict) -> dict:
    if not value:
        return {}
    repo = repository(value['repository'])
    if value.get('channel', 'stable') != 'stable':
        raise ValueError('only the stable channel has a feed contract currently')
    return {'repository': repo, 'channel': 'stable'}


def asset_url(url: str, repo: str) -> str:
    parsed = urlparse(url)
    prefix = f'/{repository(repo)}/releases/download/'
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment
            or not parsed.path.startswith(prefix) or '..' in parsed.path or '%' in parsed.path):
        raise ValueError('artifact must be an HTTPS release asset in the configured repository')
    suffix = parsed.path[len(prefix):].split('/')
    if len(suffix) != 2 or not all(suffix):
        raise ValueError('artifact URL requires a release tag and asset filename')
    return url


def inspect_apk(apk: Path, *, aapt2: str, apksigner: str) -> dict:
    """Read the signed artifact, never trust caller-supplied package/certificate metadata."""
    badging = subprocess.check_output([aapt2, 'dump', 'badging', str(apk)], text=True)
    certs = subprocess.check_output([apksigner, 'verify', '--verbose', '--print-certs', str(apk)], text=True)
    return parse_apk_identity(badging, certs)


def parse_apk_identity(badging: str, certs: str) -> dict:
    package = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", badging, re.M)
    sdk = re.search(r"^sdkVersion:'(\d+)'", badging, re.M)
    native = re.search(r'^native-code: (.+)$', badging, re.M)
    digests = re.findall(r'^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]{64})$', certs, re.M)
    if not package or not sdk or not native or len(digests) != 1:
        raise ValueError('APK needs package, minimum SDK, native ABI and exactly one verified signer')
    if 'CN=Android Debug' in certs:
        raise ValueError('debug-signed APKs cannot enter a public release feed')
    abis = re.findall(r"'([^']+)'", native.group(1))
    if not abis or any(abi not in ('arm64-v8a', 'x86_64') for abi in abis):
        raise ValueError('unsupported or missing native ABI')
    return {'package': package.group(1), 'version_code': android_version_code('', int(package.group(2))),
            'version': package.group(3), 'min_sdk': int(sdk.group(1)), 'abis': abis,
            'certificate_sha256': digests[0].lower()}


def make_feed(repo: str, entries: list[tuple[Path, str, dict]]) -> dict:
    repo = repository(repo)
    if not entries:
        raise ValueError('a release feed must contain signed APKs')
    first = entries[0][2]
    fields = ('package', 'version_code', 'version', 'certificate_sha256')
    artifacts = []
    seen = set()
    for apk, url, identity in entries:
        if any(identity[key] != first[key] for key in fields):
            raise ValueError('all APKs must share package, version and signing certificate')
        url = asset_url(url, repo)
        if any(abi in seen for abi in identity['abis']):
            raise ValueError('duplicate ABI in release feed')
        seen.update(identity['abis'])
        if apk.name != url.rsplit('/', 1)[1]:
            raise ValueError('release asset name must match the inspected APK')
        size = apk.stat().st_size
        if size <= 0:
            raise ValueError('empty APK')
        with apk.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        artifacts.append({'url': url, 'abis': identity['abis'], 'min_sdk': identity['min_sdk'],
                          'sha256': digest, 'size': size})
    return {'schema': 1, 'platform': 'android', 'channel': 'stable', 'repository': repo,
            **{key: first[key] for key in fields}, 'artifacts': artifacts}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='Generate luma-update-android.json from verified release APKs')
    parser.add_argument('--repository', required=True)
    parser.add_argument('--aapt2', required=True)
    parser.add_argument('--apksigner', required=True)
    parser.add_argument('--artifact', action='append', nargs=2, metavar=('APK', 'RELEASE_URL'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    entries = [(Path(apk), url, inspect_apk(Path(apk), aapt2=args.aapt2, apksigner=args.apksigner))
               for apk, url in args.artifact]
    args.output.write_text(json.dumps(make_feed(args.repository, entries), indent=2) + '\n')


if __name__ == '__main__':
    main()
