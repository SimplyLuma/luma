# SPDX-License-Identifier: Apache-2.0
"""Install the signed offline application baseline through normal Flatpak bundles.

Only the immutable OS-owned inventory assigns first-party roles. Seeding never
updates an existing app, changes its channel, or reinstalls a deliberately
removed app recorded in the durable seed journal. User profile migration is a
separate per-user transaction on the first ordinary launch.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from .native_app_roles import APPS, CONTRACT, SIGNING_FINGERPRINTS, verify_deployed_commit

ROOT = Path('/usr/share/luma/app-baseline')
STATE = Path('/var/lib/luma/app-baseline')
RUNTIMES = frozenset({'org.projectluma.Platform', 'org.projectluma.Platform.GL.default', 'org.projectluma.Platform.Locale'})


def protected(path, *, directory=False):
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('The offline application baseline has an unsafe parent.')
    info = path.lstat()
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or info.st_uid != 0 or info.st_mode & 0o022):
        raise ValueError('The offline application baseline is not OS-owned.')
    return info


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(root=ROOT):
    protected(root, directory=True)
    path = root / 'manifest.json'
    if protected(path).st_size > 1048576: raise ValueError('The baseline inventory is too large.')
    doc = json.loads(path.read_text())
    if (doc.get('schema') != 'org.projectluma.offline-app-baseline/v1'
            or doc.get('default_channel') != 'beta' or type(doc.get('bundles')) is not list
            or not 1 <= len(doc['bundles']) <= len(APPS)+len(RUNTIMES)):
        raise ValueError('The baseline inventory is unsupported.')
    arch = doc.get('architecture')
    if arch not in {'x86_64','aarch64'}: raise ValueError('The baseline architecture is unsupported.')
    records = []; seen = set(); applications = set(); runtimes = set()
    for row in doc['bundles']:
        if set(row) != {'ref','commit','sha256','bytes','file'}: raise ValueError('Incomplete baseline bundle.')
        ref = row['ref']
        if not isinstance(ref,str) or ref in seen: raise ValueError('Duplicate baseline ref.')
        parts = ref.split('/')
        if len(parts)!=4 or parts[2] != arch: raise ValueError('The baseline ref architecture differs.')
        kind, name, _arch, branch = parts
        if kind == 'app' and name in APPS and branch == 'beta': applications.add(name)
        elif kind == 'runtime' and name in RUNTIMES and branch == '44': runtimes.add(name)
        else: raise ValueError('The baseline contains an unmaintained role or channel.')
        for key in ('commit','sha256'):
            if not isinstance(row[key],str) or not re.fullmatch('[0-9a-f]{64}',row[key]): raise ValueError('Invalid baseline digest.')
        if (row['file'] != row['sha256']+'.flatpak' or type(row['bytes']) is not int
                or not 0 < row['bytes'] <= 8589934592): raise ValueError('Invalid baseline bundle size or path.')
        bundle = root / row['file']
        if protected(bundle).st_size != row['bytes'] or digest(bundle) != row['sha256']:
            raise ValueError('The baseline bundle differs from the signed OS inventory.')
        seen.add(ref); records.append(row)
    if applications != set(APPS) or runtimes != set(RUNTIMES):
        raise ValueError('The shipped independent-app baseline is incomplete.')
    key = root / 'luma-depot.gpg'
    if protected(key).st_size > 65536 or digest(key) != doc.get('public_key_sha256'):
        raise ValueError('The baseline public signing key differs.')
    role = root / 'roles.json'
    if protected(role).st_size > 65536 or digest(role) != doc.get('role_contract_sha256'):
        raise ValueError('The application role contract differs from the baseline.')
    roles = json.loads(role.read_text())
    if (roles.get('schema') != 'org.projectluma.first-party-app-roles/v1'
            or roles.get('roles') != {app:'signed-system-flatpak' for app in APPS}):
        raise ValueError('The application baseline has incomplete native role ownership.')
    return doc, records, key


def write_state(path, doc):
    data = (json.dumps(doc,sort_keys=True,indent=2)+'\n').encode()
    fd, temporary = tempfile.mkstemp(prefix='.seed-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def seed(root=ROOT, state=STATE, *, installation=None, runner=subprocess.run):
    if os.getuid()!=0: raise PermissionError('Only the signed OS baseline owner may seed system apps.')
    doc, records, key = inventory(root)  # all bytes are admitted before writes
    protected(CONTRACT)
    if digest(CONTRACT) != doc['role_contract_sha256']:
        raise ValueError('The signed OS native roles and offline baseline differ.')
    import gi
    gi.require_version('Flatpak','1.0')
    from gi.repository import Flatpak, Gio
    from .depot_flatpak import ensure_luma_remote, validate_remote
    installation = installation or Flatpak.Installation.new_system(None)
    ensure_luma_remote(installations=[installation])
    validate_remote(installation.get_remote_by_name('luma',None),'luma')
    # BundleRef is read from the exact hash-admitted file; its embedded identity
    # cannot select another ref or source. install --gpg-file verifies the actual
    # commit with the maintained key again before deployment.
    for row in records:
        bundle=Flatpak.BundleRef.new(Gio.File.new_for_path(str(root/row['file'])))
        if bundle.format_ref()!=row['ref'] or bundle.get_commit()!=row['commit']:
            raise ValueError('The bundle identity differs from the OS-owned inventory.')
    with tempfile.TemporaryDirectory(prefix='luma-baseline-key-') as home:
        result=runner(['gpg','--batch','--no-options','--homedir',home,'--with-colons','--show-keys',str(key)],
                      capture_output=True,text=True,check=True,timeout=30)
        fingerprints = set(); primary = False
        for line in result.stdout.splitlines():
            if line.startswith('pub:'): primary = True
            elif line.startswith('sub:'): primary = False
            elif primary and line.startswith('fpr:'):
                fingerprints.add(line.split(':')[9]); primary = False
        if not fingerprints or not fingerprints <= SIGNING_FINGERPRINTS:
            raise ValueError('The baseline public key contains an unmaintained signing identity.')
    state.mkdir(parents=True,exist_ok=True,mode=0o700);protected(state,directory=True)
    lock=state/'seed.lock'
    fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077:raise ValueError('Unsafe baseline lock.')
        fcntl.flock(fd,fcntl.LOCK_EX)
        journal=state/'seeded.json'
        history={'schema':'org.projectluma.app-baseline-seed/v1','applications':{},'result':'PENDING'}
        if journal.exists() or journal.is_symlink():
            if protected(journal).st_size>1048576:raise ValueError('The baseline journal is too large.')
            history=json.loads(journal.read_text())
            if history.get('schema')!='org.projectluma.app-baseline-seed/v1' or not isinstance(history.get('applications'),dict):
                raise ValueError('The baseline journal needs repair.')
        outcomes=[]
        for row in sorted(records,key=lambda row:(row['ref'].startswith('app/'),row['ref'])):
            parts=row['ref'].split('/');kind,name=parts[:2]
            installed=[ref for ref in installation.list_installed_refs(None) if ref.get_name()==name and ref.format_ref().startswith(kind+'/')]
            if kind=='app' and name in history['applications']:
                outcomes.append({'ref':row['ref'],'result':'preserved-prior-user-choice'});continue
            if installed:
                if len(installed)!=1 or installed[0].get_origin()!='luma':raise ValueError('An existing baseline app has an ambiguous source.')
                verify_deployed_commit(installation,installed[0])
                result='preserved-existing-deployment'
            else:
                runner(['flatpak','install','--system','--noninteractive','--assumeyes','--no-related','--bundle',
                        '--gpg-file='+str(key),str(root/row['file'])],check=True,timeout=600)
                installation.drop_caches(None)
                installed=[ref for ref in installation.list_installed_refs(None) if ref.format_ref()==row['ref']]
                if len(installed)!=1 or installed[0].get_origin()!='luma' or installed[0].get_commit()!=row['commit']:
                    raise ValueError('The installed baseline differs from its admitted bundle.')
                verify_deployed_commit(installation,installed[0]);result='installed-signed-offline-baseline'
            if kind=='app':
                history['applications'][name]={'ref':installed[0].format_ref(),'commit':installed[0].get_commit(),'seeded_unix':time.time()}
                write_state(journal,history)  # survive interruption without replaying completed choices
            outcomes.append({'ref':row['ref'],'result':result})
        history.update(result='PASS',inventory_sha256=digest(root/'manifest.json'),completed_unix=time.time(),outcomes=outcomes)
        write_state(journal,history);return history
    finally:os.close(fd)


def main():
    print(json.dumps(seed(),sort_keys=True))

if __name__=='__main__':main()
