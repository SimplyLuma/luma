#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Assemble Windows x64 Tester using a qualified native LumaUI runtime.

Inputs are unpacked, checksum-verified runtime packages. No host installation
or guest OS image is included. Run under the normal build resource admission.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--qemu-prefix',type=Path,required=True)
    parser.add_argument('--pycdlib-wheel',type=Path,required=True)
    parser.add_argument('--openssh-zip',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--release',action='store_true')
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(repo/'src/luma-tester'))
    from luma_tester import __version__
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    root=out/'Luma Tester'
    # Preserve notices and the tested GTK/Python/LumaUI closure, replacing app code.
    shutil.copytree(args.runtime,root,ignore=shutil.ignore_patterns('app','__pycache__','*.pyc','*.exe','*.cmd','application-source.json'))
    shutil.copytree(args.runtime/'app/luma_appkit',root/'app/luma_appkit')
    # Executables in bin were deliberately excluded above; restore required Python/tools.
    for path in (args.runtime/'bin').glob('*.exe'):shutil.copy2(path,root/'bin'/path.name)
    for path in (args.qemu_prefix/'bin').glob('*.dll'):shutil.copy2(path,root/'bin'/path.name)
    for name in ('qemu-img','qemu-system-x86_64','qemu-system-aarch64'):
        shutil.copy2(args.qemu_prefix/('bin/'+name+'.exe'),root/('bin/'+name+'.exe'))
    for relative in ('lib/girepository-1.0','lib/gio/modules','share/qemu','share/licenses'):
        source=args.qemu_prefix/relative
        if source.exists():shutil.copytree(source,root/relative,dirs_exist_ok=True)
    kit=repo/'src/luma-platform/appkit'
    shutil.rmtree(root/'app/luma_appkit')
    shutil.copytree(kit/'luma_appkit',root/'app/luma_appkit',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for file in kit.glob('*.css'):shutil.copy2(file,root/'app'/file.name)
    for name in ('fonts','icons'):
        if (kit/name).is_dir():shutil.copytree(kit/name,root/('app/'+name),dirs_exist_ok=True)
    shutil.copytree(repo/'src/luma-tester/luma_tester',root/'app/luma_tester',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    identity=root/'app/luma_tester/__init__.py'
    if args.release:identity.write_text(identity.read_text().replace("DOWNLOAD_SOURCE = 'test'","DOWNLOAD_SOURCE = 'tester'"))
    shutil.copy2(repo/'packaging/standalone/tester/windows-bootstrap.py',root/'app/luma_port_boot.py')
    meta=dict(app_id='io.luma.Tester',name='Luma Tester',version=__version__,module='luma_tester.windows',env={'LUMA_PLATFORM':'windows','LUMA_APP_ID':'io.luma.Tester'})
    (root/'app/luma-port-app.json').write_text(json.dumps(meta,indent=2)+'\n')
    icon=root/'share/icons/hicolor/scalable/apps/io.luma.Tester.svg';icon.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(repo/'src/luma-tester/luma_tester/io.luma.Tester.svg',icon)
    launcher=next(args.runtime.glob('*.exe'));shutil.copy2(launcher,root/'Luma Tester.exe')
    command='''@echo off
setlocal
set "PATH=%~dp0helpers\\openssh;%~dp0bin;%SystemRoot%\\System32;%SystemRoot%"
set "PYTHONHOME=%~dp0"
set "PYTHONPATH=%~dp0app"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
"%~dp0bin\\python.exe" -m luma_port_boot %*
'''
    (root/'Luma Tester (console).cmd').write_text(command,newline='\r\n')
    # Pure Python NoCloud writer; retain its distribution notices.
    site=next((root/'lib').glob('python3.*'))/'site-packages'
    with zipfile.ZipFile(args.pycdlib_wheel) as wheel:
        for item in wheel.infolist():
            if not item.is_dir():
                target=site/item.filename
                if not target.resolve().is_relative_to(site.resolve()):raise ValueError('Invalid wheel path')
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(wheel.read(item))
    helpers=root/'helpers/openssh';helpers.mkdir(parents=True)
    with zipfile.ZipFile(args.openssh_zip) as archive:
        for item in archive.infolist():
            relative=Path(item.filename).parts[1:]
            if item.is_dir() or not relative:continue
            target=helpers.joinpath(*relative)
            if not target.resolve().is_relative_to(helpers.resolve()):raise ValueError('Invalid OpenSSH path')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(item))
    # Keep firmware names consistent with the shared controller's immutable code/vars split.
    for folder,pairs in {'aarch64':{'QEMU_EFI.fd':'edk2-aarch64-code.fd','QEMU_EFI-pflash.raw':'edk2-aarch64-code.fd','vars-template-pflash.raw':'edk2-arm-vars.fd'},'ovmf':{'OVMF_CODE.fd':'edk2-x86_64-code.fd','OVMF_VARS.fd':'edk2-i386-vars.fd'}}.items():
        destination=root/'firmware'/folder;destination.mkdir(parents=True)
        for name,source in pairs.items():shutil.copy2(args.qemu_prefix/'share/qemu'/source,destination/name)
    (root/'README-WINDOWS.txt').write_text('Luma Tester '+__version__+'\nExtract the whole folder and open Luma Tester.exe.\nImages download inside Tester. Enable Windows Hypervisor Platform for native CPU acceleration.\nARM guests on x86 use slower emulation. This candidate is unsigned.\n')
    receipt=dict(version=__version__,platform='windows-x86_64',source_commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip(),download_source='tester' if args.release else 'test',signed=False,files=[])
    for path in sorted(root.rglob('*')):
        if path.is_file():receipt['files'].append(dict(path=str(path.relative_to(root)),bytes=path.stat().st_size,sha256=hashlib.file_digest(path.open('rb'),'sha256').hexdigest()))
    (out/'build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    archive=out/('Luma-Tester-'+__version__+'-windows-x86_64.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as package:
        for path in sorted(root.rglob('*')):
            if path.is_file():package.write(path,Path(root.name)/path.relative_to(root))
    (out/'SHA256SUMS').write_text(hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()+'  '+archive.name+'\n')
    print(archive,flush=True)


if __name__=='__main__':main()
