#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Relocate a qualified Metal/VirGL runtime into an isolated app resource prefix.

Inputs must be pinned, checksummed release inputs with their licenses and source
provenance. This does not install or replace the user's Homebrew tools.
"""
import argparse
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu',required=True,type=Path)
    parser.add_argument('--egl',required=True,type=Path)
    parser.add_argument('--gles',required=True,type=Path)
    parser.add_argument('--provenance',required=True,type=Path)
    parser.add_argument('--notices',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if sys.platform!='darwin':parser.error('Run on macOS.')
    if args.output.exists():parser.error('Output must be a new directory.')
    provenance=json.loads(args.provenance.read_text())
    if not provenance.get('inputs') or not provenance.get('sources'):
        parser.error('Pinned input checksums and corresponding sources are required.')
    path=Path(__file__).resolve().parents[2]/'tools/luma-port/mac/build'
    loader=importlib.machinery.SourceFileLoader('mac_backend',str(path))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    backend=importlib.util.module_from_spec(spec);loader.exec_module(backend)
    root=args.output.resolve();root.mkdir(parents=True)
    bundler=backend.Bundler(root,[args.egl.parent,args.gles.parent])
    helper=root/'Helpers/qemu-system-aarch64';helper.parent.mkdir()
    shutil.copy2(args.qemu,helper);helper.chmod(0o755)
    bundler.track(helper,args.qemu);bundler.walk(helper)
    bundler.add_library(args.egl,'libEGL.dylib')
    bundler.add_library(args.gles,'libGLESv2.dylib')
    bundler.rewrite_all()
    # ANGLE's EGL loader resolves its GLES implementation by framework name.
    framework=root/'Frameworks/GLESv2.framework'
    target=framework/'Versions/A';target.mkdir(parents=True)
    library=root/'Frameworks/libGLESv2.dylib'
    library.replace(target/'GLESv2')
    library.symlink_to('GLESv2.framework/Versions/A/GLESv2')
    (framework/'Versions/Current').symlink_to('A')
    (framework/'GLESv2').symlink_to('Versions/Current/GLESv2')
    resources=target/'Resources';resources.mkdir()
    (resources/'Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='org.chromium.angle.GLESv2',
        CFBundleExecutable='GLESv2',CFBundleName='GLESv2',CFBundlePackageType='FMWK',CFBundleVersion='1')))
    (framework/'Resources').symlink_to('Versions/Current/Resources')
    subprocess.run(['codesign','--force','--sign','-',str(framework)],check=True)
    entitlements=root/'qemu-entitlements.plist'
    entitlements.write_bytes(plistlib.dumps({'com.apple.security.hypervisor':True,'com.apple.security.cs.allow-jit':True}))
    subprocess.run(['codesign','--force','--sign','-','--entitlements',str(entitlements),str(helper)],check=True)
    shutil.copytree(args.notices,root/'ThirdPartyNotices')
    (root/'runtime.json').write_text(json.dumps(dict(schema=1,backend='angle-metal',arch='aarch64',provenance=provenance),indent=2)+'\n')
    print(root)


if __name__=='__main__':main()
