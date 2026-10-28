#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the native Apple Silicon Try Luma app with its complete QEMU runtime."""
import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--source-commit',required=True)
    p.add_argument('--release',action='store_true',help='Attribute public downloads to Try Luma; previews default to test')
    p.add_argument('--gpu-runtime',type=Path,help='Qualified, relocated macOS/VirGL prefix from stage-macos-gpu.py')
    a=p.parse_args()
    if sys.platform!='darwin' or os.uname().machine!='arm64':p.error('Build on an Apple Silicon Mac.')
    repo=Path(__file__).resolve().parents[2];out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    brew=Path(os.environ.get('LUMA_PORT_BREW','/opt/homebrew'))
    required=['qemu-system-aarch64','qemu-system-x86_64','qemu-img','xorriso']
    for name in required:
        if not (brew/'bin'/name).exists():p.error('Missing Homebrew tool: '+name)
    sys.path.insert(0,str(repo/'src/luma-tester'))
    from luma_tester import __version__
    assets=out/'inputs';firmware=assets/'firmware';firmware.mkdir(parents=True,exist_ok=True)
    for folder,pairs in {'aarch64':{'QEMU_EFI.fd':'edk2-aarch64-code.fd','QEMU_EFI-pflash.raw':'edk2-aarch64-code.fd','vars-template-pflash.raw':'edk2-arm-vars.fd'},
                         'ovmf':{'OVMF_CODE.fd':'edk2-x86_64-code.fd','OVMF_VARS.fd':'edk2-i386-vars.fd'}}.items():
        target=firmware/folder;target.mkdir(exist_ok=True)
        for dest,source in pairs.items():shutil.copy2(brew/'share/qemu'/source,target/dest)
    # VM images are obtained through the catalog; no multi-GB disk in the app.
    manifest=dict(app_id='io.luma.Tester',name='Try Luma',version=__version__,module='luma_tester.macos',
        app_dir=str(repo/'src/luma-tester'),repo_root=str(repo),kit_dir=str(repo/'src/luma-platform/appkit/luma_appkit'),
        packages=['luma_tester'],extra_typelibs=['GVnc-1.0'],native_helpers={name:str(brew/'bin'/name) for name in required},
        resource_paths={'firmware':str(firmware),'share/qemu':str(brew/'share/qemu')},
        icon=str(repo/'src/luma-tester/luma_tester/io.luma.Tester.svg'),env={'LUMA_PLATFORM':'macos','LUMA_APP_NAME':'Try Luma'})
    file=out/'manifest.json';file.write_text(json.dumps(manifest,indent=2)+'\n')
    backend=repo/'tools/luma-port/mac/build'
    env=dict(os.environ,LUMA_PORT_NO_DMG='1',LUMA_BUILD_JOBS='2')
    subprocess.run([sys.executable,str(backend),str(file),str(out)],env=env,check=True)
    app=out/'Try Luma.app';contents=app/'Contents'
    if a.gpu_runtime:
        runtime=json.loads((a.gpu_runtime/'runtime.json').read_text())
        if runtime.get('backend') not in ('angle-metal','apple-opengl') or runtime.get('arch')!='aarch64':
            p.error('Expected a qualified Apple Silicon graphics runtime.')
        shutil.copytree(a.gpu_runtime,contents/'Resources/mac-gpu',symlinks=True)
    if a.release:
        identity=contents/'Resources/app/luma_tester/__init__.py'
        identity.write_text(identity.read_text().replace("DOWNLOAD_SOURCE = 'test'", "DOWNLOAD_SOURCE = 'tester'"))
    entitlements=out/'qemu-entitlements.plist'
    entitlements.write_bytes(plistlib.dumps({'com.apple.security.hypervisor':True,'com.apple.security.cs.allow-jit':True}))
    for name in ('qemu-system-aarch64','qemu-system-x86_64'):
        subprocess.run(['codesign','--force','--sign','-','--entitlements',str(entitlements),str(contents/'Helpers'/name)],check=True)
    notices=contents/'Resources/ThirdPartyNotices';notices.mkdir(exist_ok=True)
    shutil.copy2(repo/'LICENSE.md',notices/'Luma-LICENSE')
    shutil.copy2(brew/'share/qemu/edk2-licenses.txt',notices/'edk2-licenses.txt')
    formulae=subprocess.check_output([str(brew/'bin/brew'),'list','--formula'],text=True).splitlines()
    for formula in formulae:
        prefix=Path(subprocess.check_output([str(brew/'bin/brew'),'--prefix',formula],text=True).strip())
        for pattern in ('COPYING*','LICENSE*','COPYRIGHT*'):
            for source in prefix.glob(pattern):
                if source.is_file():
                    dest=notices/formula;dest.mkdir(exist_ok=True);shutil.copy2(source,dest/source.name)
    info=contents/'Info.plist';value=plistlib.loads(info.read_bytes())
    value['NSMicrophoneUsageDescription']='Share your microphone with a Luma virtual machine when enabled.'
    value['NSCameraUsageDescription']='Share your camera with a Luma virtual machine when enabled.'
    info.write_bytes(plistlib.dumps(value))
    subprocess.run(['codesign','--force','--sign','-',str(app)],check=True)
    subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
    receipt=dict(source_commit=a.source_commit,version=__version__,host='macos-arm64',signed='ad-hoc',notarized=False,download_source='tester' if a.release else 'test',files=[])
    for path in sorted(contents.rglob('*')):
        if path.is_file() and not path.is_symlink():receipt['files'].append(dict(path=str(path.relative_to(contents)),bytes=path.stat().st_size,sha256=hashlib.file_digest(path.open('rb'),'sha256').hexdigest()))
    (out/'build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    loader=importlib.machinery.SourceFileLoader('mac_backend',str(backend));spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    dmg=module.make_dmg(app,out,manifest)
    (out/'SHA256SUMS').write_text(hashlib.file_digest(dmg.open('rb'),'sha256').hexdigest()+'  '+dmg.name+'\n')
    print(dmg,flush=True)


if __name__=='__main__':main()
