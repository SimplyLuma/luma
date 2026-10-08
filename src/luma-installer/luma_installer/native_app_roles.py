# SPDX-License-Identifier: Apache-2.0
"""Native launcher delegation to OS-owned independent application baselines.

The role file is shipped in a signed OS deployment only when its installer
seeds the signed offline baseline. libflatpak supplies the actual installed
identity and trust. No catalogue or home-directory file can assign ownership.
"""
import json
import os
import re
from pathlib import Path
import stat
import sys

# Maintained whitelist, generated from the reviewed first-party registry.
# The OS contract may only assign roles; downloaded catalogues cannot extend it.
APPS = frozenset({'org.projectluma.Depot', 'org.projectluma.Reel', 'org.projectluma.Charlie', 'org.projectluma.Stage', 'org.projectluma.Session', 'org.projectluma.Tide', 'org.projectluma.Mods', 'org.projectluma.VoiceMemos', 'org.projectluma.Darkroom', 'org.projectluma.Calendar', 'org.projectluma.Contacts', 'org.projectluma.Messages', 'org.projectluma.Photos', 'org.projectluma.Phone', 'org.projectluma.Clock', 'org.projectluma.Notes', 'org.projectluma.Leaf', 'org.projectluma.Imager', 'org.projectluma.Camera', 'org.projectluma.Grid', 'org.gnome.Nautilus', 'org.projectluma.Connect', 'io.luma.Monitor', 'org.projectluma.Tasks', 'org.projectluma.Viewer', 'com.rhyme.viola', 'org.projectluma.Write', 'org.projectluma.Weather', 'org.projectluma.Displays', 'org.projectluma.Ari'})
CONTRACT = Path('/usr/share/luma/first-party-app-roles.json')
# This is the public application signing key already shipped in the maintained
# luma.flatpakrepo. Key rotation changes the signed host contract/package; a
# same-named user remote cannot substitute its own key and gain host services.
SIGNING_FINGERPRINTS = frozenset({'069E001599679B49042FE40885FCC398C02A8BEE'})


def required(app_id, path=CONTRACT):
    if app_id not in APPS: raise ValueError('unmaintained application role')
    try:
        info = path.lstat()
    except FileNotFoundError: return False  # developer/older non-baseline image
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('application role contract has an unsafe owner or type')
    if info.st_size > 65536: raise ValueError('application role contract is too large')
    doc=json.loads(path.read_text())
    if doc.get('schema') != 'org.projectluma.first-party-app-roles/v1':
        raise ValueError('unsupported application role contract')
    roles=doc.get('roles',{})
    if any(name not in APPS or role != 'signed-system-flatpak' for name,role in roles.items()):
        raise ValueError('invalid maintained application role')
    return roles.get(app_id) == 'signed-system-flatpak'


def installed(installation, app_id, cancellable=None):
    if app_id not in APPS:
        raise ValueError('unmaintained application role')
    from .depot_flatpak import validate_remote, BRANCHES
    validate_remote(installation.get_remote_by_name('luma',cancellable),'luma')
    refs=[ref for ref in installation.list_installed_refs(cancellable)
          if ref.get_name()==app_id and ref.format_ref().startswith('app/')]
    if (len(refs)!=1 or refs[0].get_origin()!='luma' or refs[0].get_branch() not in BRANCHES
            or not re.fullmatch(r'[0-9a-f]{64}', refs[0].get_commit() or '')):
        raise ValueError('the signed application baseline is missing or ambiguous')
    verify_deployed_commit(installation, refs[0], cancellable)
    return refs[0]


def verify_deployed_commit(installation, ref, cancellable=None, *, commit=None, exact_ref=None):
    import gi
    gi.require_version('OSTree', '1.0')
    from gi.repository import OSTree, GLib
    repository = OSTree.Repo.new(installation.get_path().get_child('repo'))
    repository.open(cancellable)
    ok, signatures = repository.remote_get_gpg_verify('luma')
    summary_ok, summary = repository.remote_get_gpg_verify_summary('luma')
    if not ok or not signatures or not summary_ok or not summary:
        raise ValueError('application source signature checks are disabled')
    commit = commit or ref.get_commit()
    if not re.fullmatch(r'[0-9a-f]{64}', commit):
        raise ValueError('application commit is malformed')
    result = repository.verify_commit_ext(commit, None, None, cancellable)
    for index in range(result.count_all()):
        signature = result.get_all(index).unpack()
        if (signature[OSTree.GpgSignatureAttr.VALID]
                and not any(signature[item] for item in (
                    OSTree.GpgSignatureAttr.SIG_EXPIRED, OSTree.GpgSignatureAttr.KEY_EXPIRED,
                    OSTree.GpgSignatureAttr.KEY_REVOKED, OSTree.GpgSignatureAttr.KEY_MISSING))
                and signature[OSTree.GpgSignatureAttr.FINGERPRINT_PRIMARY] in SIGNING_FINGERPRINTS):
            if exact_ref is not None:
                # A retained running deployment is no longer the installed
                # head. Its signed binding must still identify this exact app,
                # architecture and branch, rather than another signed payload.
                ok, value, _state = repository.load_commit(commit)
                if not ok:
                    raise ValueError('retained application commit is unavailable')
                bindings = value.get_child_value(0).lookup_value(
                    'ostree.ref-binding', GLib.VariantType.new('as'))
                if bindings is None or exact_ref not in bindings.unpack():
                    raise ValueError('retained commit has a different application binding')
            return
    raise ValueError('application commit has no valid maintained Luma signature')


def launch_if_owned(app_id):
    if Path('/.flatpak-info').exists(): return False
    try:
        if not required(app_id): return False
        import gi
        gi.require_version('Flatpak','1.0')
        from gi.repository import Flatpak
        ref=installed(Flatpak.Installation.new_system(None), app_id)
    except Exception as error:
        # Never open an empty native profile when the baseline needs repair.
        from luma_appkit.migration_startup import show_failure
        show_failure('This application’s signed installation needs repair. Open Depot or retry the Luma installer.')
        raise SystemExit(1) from error
    os.execv('/usr/bin/flatpak',['flatpak','run','--system',ref.format_ref(),*sys.argv[1:]])


def resolve_owned(app_id):
    """Resolve native C launch delegation without accepting a command or path."""
    if app_id not in APPS:
        raise ValueError('unmaintained application role')
    if Path('/.flatpak-info').exists():
        raise ValueError('native ownership resolution is unavailable inside a sandbox')
    if not required(app_id):
        return None
    import gi
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak
    ref = installed(Flatpak.Installation.new_system(None), app_id)
    # installed() verifies origin, commit signatures and remote checks. Keep
    # stdout an exact bounded ref, never caller-selected shell or path syntax.
    arch, branch = ref.get_arch(), ref.get_branch()
    from .depot_flatpak import BRANCHES
    if arch not in {'x86_64', 'aarch64'} or branch not in BRANCHES:
        raise ValueError('unsupported application architecture or branch')
    expected = f'app/{app_id}/{arch}/{branch}'
    if ref.get_name() != app_id or ref.format_ref() != expected:
        raise ValueError('the installed application has a different role identity')
    return expected


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != '--resolve' or args[1] not in APPS:
        print('Unsupported native application ownership request.', file=sys.stderr)
        return 1
    try:
        reference = resolve_owned(args[1])
    except Exception:
        print('The signed application installation needs repair.', file=sys.stderr)
        return 1
    if reference is None:
        return 0
    print(reference)
    return 10


if __name__ == '__main__':
    raise SystemExit(main())
