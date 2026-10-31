#!/usr/bin/python3 -B
# SPDX-License-Identifier: Apache-2.0
"""Actual uninstall policy in a new private Flatpak installation, no app launch.

Caller must provide a new own fixture directory and a private child HOME.
Unsigned local dummy exports are isolated structural fixtures, never app trust
or signed-release qualification. Default user/system installations are unused.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import gi

gi.require_version('Flatpak','1.0')
from gi.repository import Flatpak, Gio
from luma_installer import retired_apps

root=Path(sys.argv[1]);assert root.is_absolute() and not root.exists()
root.mkdir(mode=0o700)
fixture_home=Path.home();assert fixture_home.is_relative_to(root.parent) and fixture_home!=root
assert fixture_home.is_dir() and fixture_home.stat().st_uid==os.geteuid()
repo=root/'repo';arch=platform.machine();assert arch in ('x86_64','aarch64')
apps=('org.projectluma.Displays','org.projectluma.RetirementFixtureUnrelated')
refs=[]
for app in apps:
    build=root/app;(build/'files/bin').mkdir(parents=True)
    command=build/'files/bin/not-executed';command.write_text('#!/bin/sh\nexit 99\n');command.chmod(0o700)
    (build/'metadata').write_text('[Application]\nname='+app+'\nruntime=org.projectluma.RetirementFixtureRuntime/'+arch+'/44\nsdk=org.projectluma.RetirementFixtureRuntime/'+arch+'/44\ncommand=not-executed\n')
    subprocess.run(['flatpak','build-finish',str(build)],check=True,timeout=30)
    subprocess.run(['flatpak','build-export','--no-update-summary','--arch='+arch,str(repo),str(build),'beta'],check=True,timeout=30)
    refs.append('app/'+app+'/'+arch+'/beta')
subprocess.run(['flatpak','build-update-repo',str(repo)],check=True,timeout=30)
path=root/'installation';installation=Flatpak.Installation.new_for_path(Gio.File.new_for_path(str(path)),True,None)
assert Path(installation.get_path().get_path())==path
remote=Flatpak.Remote.new('retirement-fixture');remote.set_url(repo.as_uri());remote.set_gpg_verify(False);installation.add_remote(remote,True,None)
transaction=Flatpak.Transaction.new_for_installation(installation,None)
transaction.set_no_interaction(True);transaction.set_disable_dependencies(True);transaction.set_disable_related(True)
for ref in refs:transaction.add_install('retirement-fixture',ref,None)
assert transaction.run(None)
installation.drop_caches(None)
assert {r.format_ref() for r in installation.list_installed_refs(None)}==set(refs)
marker=fixture_home/'.var/app/org.projectluma.Displays/retained-data';marker.parent.mkdir(parents=True);marker.write_bytes(b'private retirement fixture data\n');original=hashlib.sha256(marker.read_bytes()).hexdigest()
removed=retired_apps.reconcile([installation]);assert removed==(refs[0],)
remaining={r.format_ref() for r in installation.list_installed_refs(None)}
assert remaining=={refs[1]} and hashlib.sha256(marker.read_bytes()).hexdigest()==original
assert retired_apps.reconcile([installation])==()
result={'scope':'Actual isolated unsigned Flatpak fixture only; no app execution/default installations/network repository','removed':list(removed),'remaining':sorted(remaining),'data_sha256':original,'repeat_noop':True}
(root/'PASS.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
