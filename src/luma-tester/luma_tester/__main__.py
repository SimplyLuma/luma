# SPDX-License-Identifier: Apache-2.0
import argparse
import json
from pathlib import Path
import subprocess
import sys

from .core import Session, TesterError, digest, run


def main():
    parser = argparse.ArgumentParser(description='Try Luma — QEMU mobile and desktop previews')
    parser.add_argument('--state', help='Private data directory (or LUMA_TESTER_HOME)')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('gui', help='Open the native launcher')
    sub.add_parser('version', help='Show the application version and download attribution mode')
    catalog = sub.add_parser('catalog', help='List published Hub artifacts; this does not start a download')
    catalog.add_argument('--channel', choices=['nightly', 'beta', 'stable'])
    catalog.add_argument('--arch', choices=['x86_64', 'aarch64'])
    catalog.add_argument('--kind', choices=['os_image', 'tester_app'])
    fetch = sub.add_parser('download', help='Download and verify a published file (does not install or boot it)')
    fetch.add_argument('artifact_id')
    fetch.add_argument('--output', required=True, type=Path)
    fetch.add_argument('--experience', choices=['desktop', 'tablet', 'mobile', 'tv', 'home', 'watch', 'other'], default='desktop')
    fetch.add_argument('--purpose', choices=['first_download', 'update', 'explicit_redownload'], default='first_download')
    iso=sub.add_parser('import-iso',help='Register a local generic UEFI installation ISO')
    iso.add_argument('path',type=Path)
    iso.add_argument('--arch',choices=['aarch64','x86_64'],required=True)
    integration = sub.add_parser('install-desktop', help='Add this portable build to Applications and the dock')
    integration.add_argument('--data-home', type=Path, help='Desktop session data directory')
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
    publish.add_argument('--artifact-id', help='Exact matching Hub catalog artifact, when published')
    for action in ['doctor', 'prepare', 'start', 'status', 'stop', 'pause', 'resume', 'home', 'screenshot', 'ssh', 'sync', 'wait', 'dev', 'snapshots', 'snapshot', 'restore', 'device']:
        p = sub.add_parser(action)
        p.add_argument('manifest', type=Path)
        if action == 'device':
            from .device_profiles import PROFILES
            p.add_argument('--profile',choices=[*PROFILES,'custom'],default='phone')
            p.add_argument('--posture',choices=['flat','folded','book'],default='flat')
            p.add_argument('--rotation',type=int,choices=[0,90,180,270],default=0)
            p.add_argument('--width',type=int,default=432)
            p.add_argument('--height',type=int,default=960)
            p.add_argument('--scale',type=float,default=1)
        elif action == 'start':
            p.add_argument('--port', type=int, default=2228)
            p.add_argument('--memory', type=int, help='MiB (defaults to saved VM settings)')
            p.add_argument('--cpus', type=int, help='Defaults to saved VM settings')
            p.add_argument('--headless', action='store_true')
            p.add_argument('--embedded', action='store_true', help='Private Unix display socket for the LumaUI viewer')
            p.add_argument('--software', action='store_true', help='Use TCG even when KVM is available')
            p.add_argument('--graphics', choices=['auto', 'virgl', 'software'])
            p.add_argument('--render-node', help='GPU render node, e.g. /dev/dri/renderD128')
            p.add_argument('--firmware')
            p.add_argument('--experience',choices=['desktop','mobile','tablet','tv','smart-device'])
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
        elif action in ('snapshot', 'restore'):
            p.add_argument('name', help='Named disk snapshot; guest must be shut down')
    args = parser.parse_args()
    try:
        if args.action == 'version':
            from . import __version__, DOWNLOAD_SOURCE
            print(json.dumps({'version': __version__, 'download_source': DOWNLOAD_SOURCE}))
            return 0
        if args.action in ('catalog', 'download'):
            from .downloads import HubClient, download, architecture_notice
            client = HubClient()
            filters = {name: getattr(args, name) for name in ('channel', 'arch', 'kind')
                       if getattr(args, name, None)}
            artifacts = client.catalog(**filters)
            if args.action == 'catalog':
                print(json.dumps({'artifacts': artifacts, 'notice': architecture_notice(
                    {a.get('arch') for a in artifacts if a.get('kind') == 'os_image'})}, indent=2))
            else:
                artifact = next((a for a in artifacts if a['artifact_id'] == args.artifact_id), None)
                if artifact is None:
                    raise TesterError('This artifact is not available in the verified Hub catalog.')
                print(download(artifact, args.output, experience=args.experience,
                               purpose=args.purpose, client=client, report=print))
            return 0
        if args.action == 'gui':
            from .gui import launch
            return launch(args.state)
        if args.action == 'import-iso':
            from .experiences import import_iso
            from .core import data_root
            print(import_iso(args.path,data_root(args.state),args.arch))
            return 0
        if args.action == 'install-desktop':
            from .desktop import install
            print(install(data_home=args.data_home, state=args.state))
            return 0
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
            if args.artifact_id:
                m['artifact_id'] = args.artifact_id
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
        elif args.action == 'device':
            from . import device_profiles,vm
            value=device_profiles.validate({key:getattr(args,key) for key in ('profile','posture','rotation','width','height','scale')})
            print(json.dumps(device_profiles.apply(session,value),indent=2))
            vm.save(session.root,dict(vm.load(session.root),device=value))
        elif args.action in ('snapshots', 'snapshot', 'restore'):
            from . import vm
            if args.action == 'snapshots':
                print(json.dumps(vm.checkpoints(session), indent=2))
            elif args.action == 'snapshot':
                print(json.dumps(vm.checkpoint(session, args.name), indent=2))
            else:
                vm.restore(session, args.name)
        elif args.action == 'start':
            options = {k: getattr(args, k) for k in ('port', 'memory', 'cpus', 'headless', 'firmware', 'software', 'embedded', 'graphics', 'render_node', 'experience')}
            if args.dry_run:
                print(json.dumps(session.command(**options), indent=2))
            else:
                return session.start(**options)
        elif args.action == 'status':
            print(session.status())
        elif args.action == 'stop':
            session.shutdown()
        elif args.action in ('pause', 'resume'):
            session.qmp({'pause': 'stop', 'resume': 'cont'}[args.action])
        elif args.action == 'home':
            session.wait_graphical()
            session.home()
        elif args.action == 'screenshot':
            print(session.screenshot(args.path))
        elif args.action == 'ssh':
            command = args.command[1:] if args.command[:1] == ['--'] else args.command
            if command:
                from . import host
                return host.run_ssh(session.ssh(command)).returncode
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
    except (TesterError, ImportError, OSError, ValueError, KeyError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print('Try Luma: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
