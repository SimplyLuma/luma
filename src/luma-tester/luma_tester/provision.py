# SPDX-License-Identifier: Apache-2.0
"""One-time, offline setup of a private VM from approved Luma installation media.

Never repartition an existing workspace. Setup writes a separate staging disk,
which becomes the workspace only after the installer reports success and exits.
The medium's own kickstart retains payload selection and signature verification.
"""
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

from .core import TesterError
from .vm import atomic_json

COMPLETE = 'TRY_LUMA_SETUP_COMPLETE'


def check_cancel(session):
    if (session.directory/'setup-cancel').exists():
        raise TesterError('Setup cancelled. Start again to retry; existing workspaces are safe.')


def approved(session):
    if session.manifest.get('media') != 'iso':
        return False
    from . import image_approval
    return any(item['sha256'] == session.manifest['image']['sha256'] and
               item['artifact_id'].endswith('/' + session.manifest['arch'])
               for item in image_approval.load(session.root)['images'])


def ready(session):
    try:
        value = json.loads((session.directory/'setup.json').read_text())
        return (value.get('phase') == 'ready' and
                value.get('sha256') == session.manifest['image']['sha256'] and
                (session.directory/'workspace.qcow2').is_file())
    except (OSError, ValueError, TypeError):
        return False


def kickstart(arch, public_key=None):
    console = 'ttyAMA0' if arch == 'aarch64' else 'ttyS0'
    access = ''
    if public_key is not None:
        if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]+={0,3}',public_key):
            raise TesterError('Invalid private VM control public key.')
        access='sshkey --username=luma "'+public_key+'"\nservices --enabled=sshd\n'
    return '''text
lang en_US.UTF-8
keyboard us
timezone UTC --utc
network --bootproto=dhcp --device=link --activate
ignoredisk --only-use=vda
zerombr
clearpart --all --initlabel --disklabel=gpt --drives=vda
autopart --type=btrfs --noswap
bootloader --timeout=0 --append="rhgb quiet"
firstboot --disable
user --name=luma --gecos="Luma" --groups=wheel --lock
rootpw --lock
''' + access + '''poweroff
%include /usr/share/anaconda/interactive-defaults.ks
%post --erroronfail
mkdir -p /etc/gdm /var/lib/AccountsService/users /etc/sudoers.d
printf '[daemon]\\nAutomaticLoginEnable=True\\nAutomaticLogin=luma\\n' > /etc/gdm/custom.conf
printf '[User]\\nSystemAccount=false\\n' > /var/lib/AccountsService/users/luma
printf 'luma ALL=(ALL) NOPASSWD: ALL\\n' > /etc/sudoers.d/90-try-luma
chmod 0440 /etc/sudoers.d/90-try-luma
mkdir -p /home/luma/.config
printf 'yes\\n' > /home/luma/.config/gnome-initial-setup-done
chown -R luma:luma /home/luma/.config
systemctl set-default graphical.target
restorecon -RF /etc/gdm /etc/sudoers.d /var/lib/AccountsService/users /home/luma
%end
%post --nochroot --erroronfail
printf '\\n''' + COMPLETE + '''\\n' > /dev/''' + console + '''
%end
'''


def boot_files(session):
    """Extract only the media's kernel/initrd; no mounting or host administrator."""
    directory = session.directory/'setup'
    directory.mkdir(mode=0o700, exist_ok=True)
    if not session.key.exists():
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(session.key)],check=True)
    session.key.chmod(0o600)
    public_key=' '.join(subprocess.check_output(['ssh-keygen','-y','-f',str(session.key)],text=True).split()[:2])
    configuration=kickstart(session.manifest['arch'],public_key)
    try:
        import pycdlib
    except ImportError:
        # xorriso is already shipped in the Linux and macOS runtime.
        info = subprocess.check_output(['xorriso', '-indev', str(session.base), '-pvd_info'],
                                       stderr=subprocess.STDOUT, text=True)
        match = re.search(r"Volume [Ii]d\s*:\s*'([^']+)'", info)
        if not match:
            raise TesterError('Cannot read the Luma image volume label.')
        label = match[1]
        for name in ('vmlinuz', 'initrd.img'):
            target = directory/name
            if not target.exists():
                temporary = target.with_suffix('.new')
                subprocess.run(['xorriso', '-osirrox', 'on', '-indev', str(session.base),
                                '-extract', '/images/pxeboot/'+name, str(temporary)],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                temporary.replace(target)
        (directory/'ks.cfg').write_text(configuration)
        subprocess.run(['xorriso', '-as', 'mkisofs', '-quiet', '-o', str(directory/'setup.iso.new'),
                        '-V', 'TRY_LUMA_SETUP', '-J', '-r', str(directory/'ks.cfg')], check=True)
    else:
        image = pycdlib.PyCdlib()
        image.open(str(session.base))
        try:
            label = image.pvd.volume_identifier.decode('ascii').strip()
            for name, iso_name in (('vmlinuz', 'VMLINUZ'), ('initrd.img', 'INITRD.IMG')):
                target = directory/name
                if not target.exists():
                    temporary = target.with_suffix('.new')
                    image.get_file_from_iso(str(temporary), rr_path='/images/pxeboot/'+name)
                    temporary.replace(target)
        finally:
            image.close()
        seed = pycdlib.PyCdlib()
        seed.new(interchange_level=3, joliet=3, rock_ridge='1.09', vol_ident='TRY_LUMA_SETUP')
        try:
            data = configuration.encode()
            seed.add_fp(io.BytesIO(data), len(data), iso_path='/KS.CFG;1', rr_name='ks.cfg', joliet_path='/ks.cfg')
            seed.write(str(directory/'setup.iso.new'))
        finally:
            seed.close()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,32}', label):
        raise TesterError('Unsupported Luma image volume label.')
    (directory/'setup.iso.new').replace(directory/'setup.iso')
    return directory, label


def prepare(session, lease, options, report=print):
    check_cancel(session)
    if ready(session):
        return
    directory = session.directory
    state = directory/'setup.json'
    def phase(message):
        atomic_json(state, dict(phase='preparing', message=message,
                               sha256=session.manifest['image']['sha256']))
        report(message)
    phase('Preparing Luma for its first start…')
    boot, label = boot_files(session)
    stage = directory/'workspace-install.qcow2'
    # This name is reserved for incomplete setup only. Existing disk.qcow2 and
    # completed workspace.qcow2 are never opened by the installer.
    if (directory/'workspace.qcow2').exists():
        raise TesterError('An existing workspace needs recovery; it will not be overwritten.')
    if stage.exists():
        stage.unlink()
    from . import vm, host
    capacity = vm.load(session.root)['disk_gib'] * 1024**3
    if capacity < 32*1024**3 or shutil.disk_usage(directory).free < 16*1024**3:
        raise TesterError('First setup needs a virtual disk of at least 32 GB and 16 GB free on this computer.')
    subprocess.run(['qemu-img', 'create', '-q', '-f', 'qcow2', str(stage), str(capacity)], check=True)
    command = session.command(**dict(options, embedded=False, headless=True,
                                    graphics='software', provisioning=True, disk_path=stage))
    # The private setup process has no display, passthrough, shared folders or
    # guest SSH listener. Its QMP endpoint is separate from the visible VM.
    command[command.index('-qmp')+1] = 'unix:'+str(session.runtime/'setup-qmp')+',server=on,wait=off'
    for flag in ('-nic', '-netdev'):
        if flag in command:
            i = command.index(flag)
            command[i+1] = command[i+1].split(',hostfwd=')[0]
    console = 'ttyAMA0' if session.manifest['arch'] == 'aarch64' else 'ttyS0'
    log = directory/'setup-console.log'
    command[command.index('-serial')+1] = 'file:'+str(log)
    seed = dict(driver='raw', **{'node-name':'setupseed','read-only':True},
                file=dict(driver='file',filename=str(boot/'setup.iso')))
    command += ['-blockdev', json.dumps(seed), '-device', 'scsi-cd,drive=setupseed',
                '-kernel', str(boot/'vmlinuz'), '-initrd', str(boot/'initrd.img'),
                '-append', 'inst.ks=hd:LABEL=TRY_LUMA_SETUP:/ks.cfg inst.stage2=hd:LABEL='+label+
                ' inst.text inst.notmux console='+console, '-no-reboot']
    phase('Setting up your persistent Luma workspace. This happens only once…')
    with (directory/'setup-qemu.log').open('w') as output:
        child = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                  **({} if host.windows() else dict(pass_fds=(lease.fileno(),))))
        import signal
        previous = {}
        def cancel(signum, frame):
            child.terminate()
            raise TesterError('Setup interrupted. Start again to retry; existing workspaces are safe.')
        try:
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous[signum] = signal.signal(signum, cancel)
            deadline = time.monotonic()+3600
            last_message = ''
            while child.poll() is None:
                check_cancel(session)
                if time.monotonic() > deadline:
                    raise TesterError('Luma setup timed out. Startup details are saved in '+str(directory))
                if log.exists():
                    with log.open('rb') as stream:
                        stream.seek(max(0, log.stat().st_size-32768))
                        tail=stream.read().decode('utf-8',errors='replace')
                    milestones=[('Configuring storage','Preparing your virtual disk…'),
                        ('Installing the software','Installing Luma into your workspace…'),
                        ('Writing objects','Copying the verified Luma system…'),
                        ('Running post-installation scripts','Finishing your Luma workspace…')]
                    found=[(tail.rfind(token),message) for token,message in milestones if token in tail]
                    if found:
                        message=max(found)[1]
                        if message!=last_message:phase(message);last_message=message
                time.sleep(1)
            text = log.read_text(errors='replace') if log.exists() else ''
            if child.returncode or COMPLETE not in {line.strip() for line in text.splitlines()}:
                raise TesterError('Luma setup did not finish. Details: '+str(directory/'setup-console.log'))
        finally:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:child.kill();child.wait()
            for signum, handler in previous.items():
                signal.signal(signum, handler)
            (session.runtime/'setup-qmp').unlink(missing_ok=True)
    stage.replace(directory/'workspace.qcow2')
    atomic_json(state,dict(phase='ready',sha256=session.manifest['image']['sha256'],
                           message='Opening your Luma desktop…'))
    report('Opening your Luma desktop…')
