# SPDX-License-Identifier: Apache-2.0
import argparse
import json
from pathlib import Path
import subprocess
import sys

from .core import Session, TesterError, digest, run


def main():
    parser = argparse.ArgumentParser(description='Luma Tester — Linux QEMU mobile and desktop previews')
    parser.add_argument('--state', help='Private data directory (or LUMA_TESTER_HOME)')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('gui', help='Open the native launcher')
    publish = sub.add_parser('manifest', help='Describe a prepared cloud-init enabled Luma VM disk')
    publish.add_argument('disk', type=Path)
    publish.add_argument('--output', required=True, type=Path)
    publish.add_argument('--id', required=True)
    publish.add_argument('--build', required=True)
    publish.add_argument('--target', choices=['mobile', 'desktop'], required=True)
    publish.add_argument('--arch', choices=['x86_64', 'aarch64'], required=True)
    publish.add_argument('--boot', choices=['bios', 'uefi'], required=True)
    publish.add_argument('--user', default='luma')
    publish.add_argument('--url', help='Published HTTPS disk URL; defaults to the local disk')
    for action in ['doctor', 'prepare', 'start', 'status', 'stop', 'pause', 'resume', 'screenshot', 'ssh', 'sync', 'wait', 'dev']:
        p = sub.add_parser(action)
        p.add_argument('manifest', type=Path)
        if action == 'start':
            p.add_argument('--port', type=int, default=2228)
            p.add_argument('--memory', type=int, default=4096, help='MiB')
            p.add_argument('--cpus', type=int, default=4)
            p.add_argument('--headless', action='store_true')
            p.add_argument('--embedded', action='store_true', help='Private Unix display socket for the LumaUI viewer')
            p.add_argument('--software', action='store_true', help='Use TCG even when KVM is available')
            p.add_argument('--firmware')
            p.add_argument('--dry-run', action='store_true')
        elif action in ('screenshot', 'sync'):
            p.add_argument('path', type=Path)
        elif action == 'ssh':
            p.add_argument('command', nargs=argparse.REMAINDER)
        elif action == 'dev':
            p.add_argument('source', type=Path)
            p.add_argument('--once', action='store_true')
            p.add_argument('command', nargs=argparse.REMAINDER)
        elif action == 'wait':
            p.add_argument('--timeout', type=int, default=180)
            p.add_argument('--graphical', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'gui':
            from .gui import launch
            return launch(args.state)
        if args.action == 'manifest':
            disk = args.disk.resolve()
            # Materialize overlays with `qemu-img convert` before making a shareable manifest.
            info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(disk)]))
            details = info.get('format-specific', {}).get('data', {})
            if (info['format'] != 'qcow2' or info.get('backing-filename') or info.get('data-file')
                    or details.get('data-file') or info.get('encrypted') or details.get('encrypt')):
                raise TesterError('Export a standalone qcow2 with qemu-img convert first.')
            if args.output.exists():
                raise TesterError('Manifest output already exists.')
            m = {'schema': 1, 'id': args.id, 'target': args.target, 'arch': args.arch,
                 'boot': args.boot, 'build': args.build, 'user': args.user, 'cloud_init': True,
                 'image': {'url': args.url or disk.as_uri(), 'sha256': digest(disk), 'bytes': disk.stat().st_size}}
            # Validate before exposing a manifest to the launcher.
            import tempfile
            from .core import read_manifest
            with tempfile.NamedTemporaryFile(mode='w+', suffix='.json') as temp:
                json.dump(m, temp)
                temp.flush()
                read_manifest(temp.name)
            args.output.write_text(json.dumps(m, indent=2) + '\n')
            print(args.output)
            return 0
        session = Session(args.manifest, args.state)
        if args.action == 'doctor':
            tools = session.tools()
            print(json.dumps({'tools': tools, 'guest': session.manifest, 'state': str(session.directory)}, indent=2))
            return int(not all(tools.values()))
        if args.action == 'prepare':
            session.prepare()
        elif args.action == 'start':
            options = {k: getattr(args, k) for k in ('port', 'memory', 'cpus', 'headless', 'firmware', 'software', 'embedded')}
            if args.dry_run:
                print(json.dumps(session.command(**options), indent=2))
            else:
                return session.start(**options)
        elif args.action == 'status':
            print(session.status())
        elif args.action in ('stop', 'pause', 'resume'):
            session.qmp({'stop': 'system_powerdown', 'pause': 'stop', 'resume': 'cont'}[args.action])
        elif args.action == 'screenshot':
            print(session.screenshot(args.path))
        elif args.action == 'ssh':
            command = args.command[1:] if args.command[:1] == ['--'] else args.command
            return subprocess.call(session.ssh(command))
        elif args.action == 'sync':
            session.sync(args.path)
        elif args.action == 'dev':
            command = args.command[1:] if args.command[:1] == ['--'] else args.command
            session.dev(args.source, command, args.once)
        elif args.action == 'wait':
            (session.wait_graphical if args.graphical else session.wait)(args.timeout)
    except KeyboardInterrupt:
        return 130
    except (TesterError, ImportError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print('Luma Tester: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
