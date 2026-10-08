"""Read-only view of the existing OSTree updater; independent of account login.

No enrollment, staging, signature verification, reboot or privileged calls live
here. Release signatures and install decisions remain with the updater owner.
"""
import json
import platform
import re
import subprocess
import tempfile
import time
from .policy import DIGEST


def _checksum(value):
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ValueError('invalid deployment checksum')
    return value


def deployment_status(value, *, architecture, observed_at):
    if not isinstance(value, dict) or not isinstance(value.get('deployments'), list):
        raise ValueError('invalid updater status')
    rows = value['deployments']
    if not 1 <= len(rows) <= 32:
        raise ValueError('invalid deployment count')
    clean = []
    for row in rows:
        if not isinstance(row, dict): raise ValueError('invalid deployment')
        flags = {name: row.get(name, False) for name in ('booted', 'staged', 'pinned')}
        if any(type(flag) is not bool for flag in flags.values()):
            raise ValueError('invalid deployment flags')
        if flags['booted'] and flags['staged']: raise ValueError('inconsistent deployment')
        base = row.get('base-checksum')
        if base is not None: _checksum(base)
        clean.append({'checksum': _checksum(row.get('checksum')), 'base_checksum': base, **flags})
    booted = [row for row in clean if row['booted']]
    staged = [row for row in clean if row['staged']]
    if len(booted) != 1 or len(staged) > 1:
        raise ValueError('ambiguous deployment status')
    # rpm-ostree puts the default next-boot deployment first. A nonbooted
    # default may already be finalized (staged=false), but is still not running.
    pending = clean[0]['checksum'] if not clean[0]['booted'] else None
    return {'available': True, 'stale': False, 'observed_at': observed_at,
            'architecture': architecture, 'booted_checksum': booted[0]['checksum'],
            'booted_base_checksum': booted[0]['base_checksum'],
            'staged_checksum': staged[0]['checksum'] if staged else None,
            'staged_base_checksum': staged[0]['base_checksum'] if staged else None,
            'pending_checksum': pending,
            'retained_deployments': [row for row in clean if not row['booted']
                                     and row['checksum'] != pending],
            'transaction_active': bool(value.get('transaction')),
            # A retained deployment alone does not prove a rollback occurred.
            'rollback_event': None, 'failure': None}


def correlate_release(status, manifest, *, signature_verified=False):
    """Compare owner-verified canonical manifest with observed local checksums.

    The caller must obtain signature_verified from the canonical verifier,
    never from downloaded JSON or an account API's assertion.
    """
    if signature_verified is not True:
        return {'relation': 'unverified', 'release_commit': None}
    if (not isinstance(manifest, dict)
            or manifest.get('schema') != 'org.projectluma.update-release/v1'):
        raise ValueError('unsupported canonical release manifest')
    commit = _checksum(manifest.get('ostree_commit'))
    for name in ('accepted_deployment_commit', 'image_sha256', 'build_report_sha256'):
        _checksum(manifest.get(name))
    if manifest.get('previous_commit') is not None: _checksum(manifest['previous_commit'])
    source = manifest.get('source_revision')
    if not isinstance(source, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', source):
        raise ValueError('invalid release source identity')
    ref = manifest.get('ref')
    match = re.fullmatch(r'luma/[0-9]+/(x86_64|aarch64)/([a-z][a-z0-9-]*)', ref or '')
    if not match or match[2] != manifest.get('channel'):
        raise ValueError('invalid release target')
    result = {'release_commit': commit, 'relation': 'unknown'}
    if match[1] != status.get('architecture'):
        result['relation'] = 'incompatible_architecture'
    elif status.get('available') and not status.get('stale'):
        if status.get('booted_checksum') == commit: result['relation'] = 'booted'
        elif status.get('staged_checksum') == commit: result['relation'] = 'staged'
        elif status.get('pending_checksum') == commit: result['relation'] = 'pending_reboot'
        elif status.get('booted_base_checksum') == commit: result['relation'] = 'booted_base_with_local_changes'
        elif status.get('staged_base_checksum') == commit: result['relation'] = 'staged_base_with_local_changes'
        else: result['relation'] = 'not_booted_or_pending'
    return result


def _command(arguments):
    # Temporary file avoids unbounded in-memory subprocess capture. Nothing is
    # logged or exported: status can contain origin URLs and local package data.
    with tempfile.TemporaryFile() as output:
        subprocess.run(arguments, stdout=output, stderr=subprocess.DEVNULL,
                       check=True, timeout=5)
        if output.tell() > 1024 * 1024: raise ValueError('oversized updater status')
        output.seek(0)
        return output.read().decode('utf-8')


def read_status(*, previous=None):
    architecture = platform.machine()
    try:
        result = deployment_status(json.loads(_command(['rpm-ostree', 'status', '--json'])),
                                   architecture=architecture, observed_at=int(time.time()))
    except (OSError, ValueError, subprocess.SubprocessError):
        if previous and previous.get('available'):
            return {**previous, 'stale': True, 'error': 'updater_status_unavailable'}
        return {'available': False, 'stale': False, 'observed_at': None,
                'architecture': architecture, 'error': 'updater_status_unavailable'}
    try:
        raw = _command(['systemctl', 'show', 'luma-recent-update.service',
                        '--property=LoadState,Result,ExecMainStatus,ExecMainExitTimestampMonotonic'])
        fields = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
        if fields.get('LoadState') == 'loaded':
            # Last service failure has no release ID: do not attribute it to
            # the advertised desired release or claim that the boot failed.
            result['failure'] = {'scope': 'last_updater_service_attempt',
                'result': fields.get('Result'), 'exit_status': fields.get('ExecMainStatus'),
                'exit_monotonic_us': fields.get('ExecMainExitTimestampMonotonic')}
            if fields.get('Result') == 'success': result['failure'] = None
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return result
