#!/usr/bin/python3
"""Build tiny, local, harmless fixtures. Does not install or run packages."""
from pathlib import Path
import os, platform, shutil, struct, subprocess, tempfile

root=Path(tempfile.mkdtemp(prefix='luma-install-fixtures-',dir='/var/tmp'))
app=root/'AppDir';app.mkdir()
(app/'AppRun').write_text('#!/bin/sh\nprintf "Luma Install AppImage fixture launched\\n"\n')
(app/'AppRun').chmod(0o755)
(app/'fixture.desktop').write_text('[Desktop Entry]\nType=Application\nName=Arrival Fixture\nX-AppImage-Version=1.0\nExec=AppRun\nIcon=package-x-generic\n')
subprocess.run(['mksquashfs',str(app),str(root/'payload.squashfs'),'-noappend','-quiet','-processors','1'],check=True)
header=bytearray(4096);header[:4]=b'\x7fELF';header[4:7]=b'\x02\x01\x01';header[8:11]=b'AI\x02'
struct.pack_into('<H',header,18,183 if platform.machine()=='aarch64' else 62)
(root/'ArrivalFixture.AppImage').write_bytes(header+(root/'payload.squashfs').read_bytes())

runtime_id='org.projectluma.InstallTest.Runtime';arch=platform.machine();branch='stable'
runtime=root/'runtime';(runtime/'usr/bin').mkdir(parents=True);(runtime/'usr/lib').mkdir()
(runtime/'usr/lib64').symlink_to('lib')
shutil.copy2('/usr/bin/bash',runtime/'usr/bin/sh')
ldd=subprocess.check_output(['ldd','/usr/bin/bash'],text=True)
for line in ldd.splitlines():
    for word in line.split():
        if word.startswith('/') and Path(word).is_file(): shutil.copy2(word,runtime/'usr/lib'/Path(word).name)
(runtime/'metadata').write_text(f'[Runtime]\nname={runtime_id}\nruntime={runtime_id}/{arch}/{branch}\nsdk={runtime_id}/{arch}/{branch}\n')
(runtime/'files').mkdir()
repo=root/'repo'
subprocess.run(['flatpak','build-export','--runtime',str(repo),str(runtime),branch],check=True)
subprocess.run(['flatpak','build-bundle','--runtime',str(repo),str(root/'runtime.flatpak'),runtime_id,branch],check=True)
for suffix in ('One','Two'):
    app_id='org.projectluma.InstallTest.'+suffix
    directory=root/suffix;(directory/'files/bin').mkdir(parents=True)
    exports=directory/'export/share/applications';exports.mkdir(parents=True)
    (directory/'files/bin/arrival-test').write_text('#!/usr/bin/sh\nprintf "Luma Install Flatpak fixture launched\\n"\n')
    (directory/'files/bin/arrival-test').chmod(0o755)
    (exports/(app_id+'.desktop')).write_text(f'[Desktop Entry]\nType=Application\nName=Arrival {suffix}\nExec=arrival-test\nIcon=package-x-generic\n')
    (directory/'metadata').write_text(f'[Application]\nname={app_id}\nruntime={runtime_id}/{arch}/{branch}\nsdk={runtime_id}/{arch}/{branch}\ncommand=arrival-test\n')
    subprocess.run(['flatpak','build-export',str(repo),str(directory),branch],check=True)
    subprocess.run(['flatpak','build-bundle',str(repo),str(root/(suffix+'.flatpak')),app_id,branch],check=True)
print('FIXTURE_ROOT='+str(root))
