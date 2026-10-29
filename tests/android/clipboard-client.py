#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Execute the production native-HIDL SDK clipboard wrapper.

Host JVM dependency/transport fixtures do not qualify actual Android Binder IPC.
The rebuilt image must repeat the real APK/host clipboard round trips.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',required=True,type=Path)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[2]
    source=args.source_root.resolve()
    javac=str(source/'prebuilts/jdk/jdk17/linux-x86/bin/javac')
    java=str(source/'prebuilts/jdk/jdk17/linux-x86/bin/java')
    original=source/'lineage-sdk/sdk/src/java/lineageos/waydroid/Clipboard.java'
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory)
        wrapper=work/'source/sdk/src/java/lineageos/waydroid/Clipboard.java'
        wrapper.parent.mkdir(parents=True)
        shutil.copyfile(original,wrapper)
        subprocess.run(['patch','--batch','--fuzz=0','-p1','-d',str(work/'source'),'-i',str(repo/'patches/android-lineage-sdk/0004-luma-native-clipboard-reconnect.patch')],check=True)
        fixtures={
            'android/content/Context.java':'package android.content; public class Context { public Context getApplicationContext(){return this;} }',
            'android/os/RemoteException.java':'package android.os; public class RemoteException extends Exception {}',
            'android/util/Log.java':'package android.util; public class Log { public static int e(String tag,String message){return 0;} public static int e(String tag,String message,Throwable error){return 0;} }',
            'test/transport/Registry.java':'package test.transport; import vendor.waydroid.clipboard.V1_0.IWaydroidClipboard; public class Registry { public static IWaydroidClipboard endpoint; public static int lookups; }',
            'vendor/waydroid/clipboard/V1_0/IWaydroidClipboard.java':'package vendor.waydroid.clipboard.V1_0; import android.os.*; import test.transport.Registry; public interface IWaydroidClipboard { void sendClipboardData(String value) throws RemoteException; String getClipboardData() throws RemoteException; static IWaydroidClipboard getService(boolean retry) throws RemoteException { if(retry)throw new AssertionError(); Registry.lookups++; return Registry.endpoint; } }',
        }
        paths=[]
        for name,body in fixtures.items():
            path=work/'fixtures'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body+'\n');paths.append(str(path))
        classes=work/'classes';classes.mkdir()
        subprocess.run([javac,'-J-Xmx256m','--release','8','-d',str(classes),*paths,str(wrapper),str(repo/'tests/android/clipboard-client/ClipboardClientTest.java')],check=True)
        subprocess.run([java,'-Xmx128m','-cp',str(classes),'ClipboardClientTest'],check=True,timeout=10)
        print('Production HIDL SDK wrapper lifecycle checks passed with transport fixtures; no Android IPC claim')


if __name__=='__main__':main()
