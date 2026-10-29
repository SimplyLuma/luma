# SPDX-License-Identifier: Apache-2.0
"""Read a fixed synthetic HIDL control as Android UID1000 in a disposable VM.

No arbitrary clipboard text or process environment is printed.
"""
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--service", choices=("clipboard", "task"), default="clipboard")
parser.add_argument("--expect", required=True, choices=("android-fixture", "host-fixture", "forbidden-write-fixture", "fixture-label"))
args=parser.parse_args()
if (args.service == "task") != (args.expect == "fixture-label"):
    parser.error("Task requires fixture-label; clipboard requires a recognized text fixture")
import json,pathlib,shlex,subprocess
package='org.projectluma.androidtest'
base=['lxc-attach','-P','/var/lib/waydroid/lxc','-n','waydroid','--clear-env','--']
def android(*args):return subprocess.check_output([*base,'/system/bin/'+args[0],*args[1:]],text=True,stderr=subprocess.PIPE,timeout=20).strip()
subprocess.run(['gdbus','call','--system','--dest','id.waydro.Container','--object-path','/ContainerManager','--method','id.waydro.ContainerManager.Unfreeze'],check=True,stdout=subprocess.DEVNULL,timeout=15)
paths=android('pm','path',package).splitlines()
assert len(paths)==1 and paths[0].startswith('package:/data/app/')
apk=paths[0][len('package:'):]
assert '..' not in pathlib.PurePosixPath(apk).parts
allowed={'ANDROID_ROOT','ANDROID_DATA','ANDROID_ART_ROOT','ANDROID_RUNTIME_ROOT','ANDROID_I18N_ROOT','ANDROID_TZDATA_ROOT','BOOTCLASSPATH','DEX2OATBOOTCLASSPATH'}
environment={}
# This is a public init configuration, not any user's process environment.
init_environment=None
for path in ('/system/etc/init/hw/init.environ.rc','/init.environ.rc','/system/etc/init/init.environ.rc'):
    try:
        init_environment=android('cat',path)
        break
    except subprocess.CalledProcessError:continue
if init_environment is None:raise RuntimeError('Public Android init environment file not found')
init_environment += '\n' + android('cat','/data/system/environ/classpath')
for line in init_environment.splitlines():
    fields=shlex.split(line,comments=True)
    if len(fields)==3 and fields[0]=='export' and fields[1] in allowed:
        assert '$' not in fields[2] and all(p.startswith('/') for p in fields[2].split(':'))
        environment[fields[1]]=fields[2]
assert 'BOOTCLASSPATH' in environment
environment['CLASSPATH']=apk+':/system/framework/org.lineageos.platform.jar'
command=[*base[:-1],'--uid=1000','--gid=1000','--','/system/bin/env',*[key+'='+value for key,value in sorted(environment.items())],'/system/bin/app_process','/system/bin','org.projectluma.androidtest.'+('NativeTaskProbe' if args.service=='task' else 'NativeClipboardProbe'),'read']
result=subprocess.run(command,text=True,capture_output=True,timeout=30)
if result.returncode:
    raise RuntimeError('Trusted synthetic app_process control failed with exit '+str(result.returncode))
records=[json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
assert len(records)==1 and records[0]['uid']==1000
assert records[0]['state'] in {'empty','host-fixture','android-fixture','forbidden-write-fixture','other-content','fixture-label','other-label','unavailable'}
print(json.dumps({'role':'native-system-control','service':args.service,**records[0]}),flush=True)

if records[0]["state"] != args.expect:
    raise SystemExit("Trusted native control did not match the nonempty fixture")
