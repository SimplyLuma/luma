# SPDX-License-Identifier: Apache-2.0
"""Display geometry and posture independent of guest CPU and image identity."""
from copy import deepcopy
from .core import TesterError
from . import host

# Panel pixels follow manufacturer specifications; scale is a Tester UI density,
# not Android's device density. Default stays compatible with existing sessions.
PROFILES = {
    'phone': dict(label='Luma Phone', width=432, height=960, scale=1, kind='phone'),
    'pixel-pro': dict(label='Pixel 10 Pro', width=1280, height=2856, scale=3, kind='phone'),
    'pixel-xl': dict(label='Pixel 10 Pro XL', width=1344, height=2992, scale=3, kind='phone'),
    'fold': dict(label='Pixel 10 Pro Fold', width=2076, height=2152, scale=2.5, kind='fold'),
    'duo': dict(label='Surface Duo', width=2784, height=1800, scale=2.5, kind='dual', gap=84),
    'titan': dict(label='Unihertz Titan', width=1432, height=1436, scale=3, kind='keyboard'),
}
DEFAULT = dict(profile='phone', posture='flat', rotation=0, width=432, height=960, scale=1)


def validate(value):
    if not isinstance(value,dict):raise TesterError('Choose a device configuration.')
    result=dict(DEFAULT,**value)
    if result['profile'] not in (*PROFILES,'custom') or result['posture'] not in ('flat','folded','book'):
        raise TesterError('Unknown device or posture.')
    if type(result['rotation']) is not int or result['rotation'] not in (0,90,180,270):
        raise TesterError('Choose a quarter-turn rotation.')
    for key in ('width','height'):
        if type(result[key]) is not int or not 320<=result[key]<=4096:
            raise TesterError('Choose a screen size from 320 to 4096 pixels.')
    if type(result['scale']) not in (int,float) or not 1<=result['scale']<=4:
        raise TesterError('Choose a UI scale from 1 to 4.')
    return result


def describe(value):
    config=validate(value);profile=PROFILES.get(config['profile'])
    base=deepcopy(profile) if profile else dict(label='Custom',kind='phone',**{key:config[key] for key in ('width','height','scale')})
    width,height,scale=base['width'],base['height'],base['scale']
    posture=config['posture'] if base['kind'] in ('fold','dual') else 'flat'
    hinge=None;regions=[]
    if base['kind']=='fold' and posture=='folded':width,height=1080,2364
    if base['kind']=='dual':
        pane=(width-base['gap'])//2
        if posture=='folded':width=pane
        else:
            hinge=dict(x=pane,y=0,width=base['gap'],height=height)
            regions=[dict(x=0,y=0,width=pane,height=height),dict(x=pane+base['gap'],y=0,width=pane,height=height)]
    if not regions:regions=[dict(x=0,y=0,width=width,height=height)]
    rotation=config['rotation']
    def rotate(rect):
        x,y,w,h=(rect[key] for key in ('x','y','width','height'))
        if rotation==90:return dict(x=height-y-h,y=x,width=h,height=w)
        if rotation==180:return dict(x=width-x-w,y=height-y-h,width=w,height=h)
        if rotation==270:return dict(x=y,y=width-x-w,width=h,height=w)
        return rect
    regions=[rotate(rect) for rect in regions]
    hinge=rotate(hinge) if hinge else None
    if rotation in (90,270):width,height=height,width
    native_pixels=dict(width=width,height=height)
    native_scale=scale
    # QEMU's VNC scanout is capped at 2160 rows. High-density panels render at
    # half density in either orientation, preserving logical app dimensions and
    # keeping posture transitions at the same density. Panel pixels remain
    # explicit metadata; this is a UI preview, not a native-panel capture.
    divisor=2 if profile and scale>=2 else 1
    while height/divisor>2160 or width/divisor>3840:divisor*=2
    def reduced(rect):
        return {key:round(value/divisor) for key,value in rect.items()}
    regions=[reduced(rect) for rect in regions]
    hinge=reduced(hinge) if hinge else None
    width,height=round(width/divisor),round(height/divisor)
    scale/=divisor
    return dict(schema=1,profile=config['profile'],label=base['label'],kind=base['kind'],posture=posture,
        rotation=rotation,pixels=dict(width=width,height=height),scale=scale,
        native_pixels=native_pixels,native_scale=native_scale,render_divisor=divisor,
        logical=dict(width=round(width/scale),height=round(height/scale)),
        regions=regions,hinge=hinge,keyboard='physical' if base['kind']=='keyboard' else 'touch',
        hinge_angle=0 if posture=='folded' else 90 if posture=='book' else 180)


def apply(session, value):
    """Acknowledge only after the guest output and input configuration succeed."""
    import json
    import subprocess
    from pathlib import Path
    from .vm import atomic_json
    if session.manifest['target']!='mobile' or not session.manifest['cloud_init']:
        raise TesterError('Device controls require a Mobile developer image.')
    if not session.manifest.get('capabilities', {}).get('dynamic_display', True):
        raise TesterError('This image uses a fixed phone display.')
    description=describe(value)
    script=Path(__file__).with_name('device_guest.py').read_text()
    result=host.run_ssh(session.ssh(['python3','-c',script]),input=json.dumps(description),
        text=True,capture_output=True,timeout=65)
    if result.returncode:
        raise TesterError('Could not change the device: '+result.stderr.strip()[-1000:])
    actual=json.loads(result.stdout)
    atomic_json(session.directory/'device.json',actual)
    return actual
