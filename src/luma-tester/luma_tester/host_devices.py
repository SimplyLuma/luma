# SPDX-License-Identifier: Apache-2.0
"""Host inventory; USB devices and integrated camera pipelines are distinct."""
import os
import re
from pathlib import Path


def usb_devices(sys=Path('/sys'), dev=Path('/dev'), mountinfo=Path('/proc/self/mountinfo')):
    base=sys/'bus/usb/devices'
    try:mounted={line.split()[2] for line in mountinfo.read_text().splitlines()}
    except OSError:mounted=set()
    mounted_paths=[]
    for disk in (sys/'class/block').glob('*'):
        try:
            if (disk/'dev').read_text().strip() in mounted:mounted_paths.append(disk.resolve())
        except OSError:pass
    found=[]
    for path in sorted(base.glob('*')):
        try:
            if ':' in path.name or not (path/'busnum').is_file():continue
            classes={(p/'bInterfaceClass').read_text().strip().lower()
                     for p in base.glob(path.name+':*') if (p/'bInterfaceClass').is_file()}
            # Host hubs, keyboards/mice and network adapters are not transfer targets.
            if not classes or '09' in classes or (classes & {'03','02','0a','e0'} and not classes & {'0e','08'}):continue
            kind='camera' if '0e' in classes else 'storage' if '08' in classes else 'audio' if '01' in classes else 'usb'
            bus,address=int((path/'busnum').read_text()),int((path/'devnum').read_text())
            product=path/'product'
            name=product.read_text().strip() if product.exists() else 'USB '+(path/'idVendor').read_text().strip()+':'+(path/'idProduct').read_text().strip()
            node=dev/f'bus/usb/{bus:03}/{address:03}'
            busy=any(p.is_relative_to(path.resolve()) for p in mounted_paths)
            found.append(dict(name=name,bus=bus,address=address,kind=kind,mounted=busy,
                              accessible=os.access(node,os.R_OK|os.W_OK)))
        except (OSError,ValueError):continue
    return found


def integrated_cameras(sys=Path('/sys')):
    """Group an IPU's capture endpoints into one camera, not 32 devices."""
    found={}
    for path in (sys/'class/video4linux').glob('video*'):
        try:
            device=(path/'device').resolve(strict=True)
            name=(path/'name').read_text().strip()
            if any(part.startswith('usb') for part in device.parts):continue
            name=re.sub(r'\s+(?:ISYS\s+)?Capture(?:\s+\d+)?$', '', name, flags=re.IGNORECASE)
            name='Integrated camera ('+name+')'
            found.setdefault(str(device),dict(name=name,kind='integrated',accessible=False,
                reason='Camera sharing for this built-in camera is coming soon.'))
        except OSError:continue
    return list(found.values())
