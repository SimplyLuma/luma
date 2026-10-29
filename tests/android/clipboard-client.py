#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compile pinned AIDL and execute the patched SDK clipboard wrapper.

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
    parser.add_argument('--android-jar',required=True,type=Path)
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
        subprocess.run(['patch','--batch','--fuzz=0','-p1','-d',str(work/'source'),'-i',str(repo/'patches/android-lineage-sdk/0004-luma-host-clipboard-aidl.patch')],check=True)
        aidl=wrapper.with_name('IClipboard.aidl')
        generated=work/'generated';generated.mkdir()
        subprocess.run([str(source/'prebuilts/build-tools/linux-x86/bin/aidl'),'--lang=java','-I'+str(work/'source/sdk/src/java'),'-o'+str(generated),str(aidl)],check=True)
        # Verify the real compiler output against the tested host descriptor and
        # operation ordering, then compile with the official public Android API.
        actual=(generated/'lineageos/waydroid/IClipboard.java').read_text()
        assert 'lineageos.waydroid.IClipboard' in actual
        assert 'TRANSACTION_sendClipboardData = (android.os.IBinder.FIRST_CALL_TRANSACTION + 0)' in actual
        assert 'TRANSACTION_getClipboardData = (android.os.IBinder.FIRST_CALL_TRANSACTION + 1)' in actual
        subprocess.run([javac,'-J-Xmx256m','--release','8','-cp',str(args.android_jar.resolve()),'-d',str(work/'api-classes'),str(generated/'lineageos/waydroid/IClipboard.java')],check=True)
        fixtures={
            'android/content/Context.java':'package android.content; public class Context { public Context getApplicationContext(){return this;} }',
            'android/os/IBinder.java':'package android.os; public interface IBinder { boolean isBinderAlive(); }',
            'android/os/RemoteException.java':'package android.os; public class RemoteException extends Exception {}',
            'android/os/ServiceManager.java':'package android.os; public class ServiceManager { public static IBinder endpoint; public static int lookups; public static IBinder getService(String name){if(!"waydroidclipboard".equals(name))throw new AssertionError();lookups++;return endpoint;} }',
            'android/util/Log.java':'package android.util; public class Log { public static int e(String tag,String message){return 0;} public static int e(String tag,String message,Throwable error){return 0;} }',
            'lineageos/waydroid/IClipboard.java':'package lineageos.waydroid; import android.os.*; public interface IClipboard { IBinder asBinder(); void sendClipboardData(String value) throws RemoteException; String getClipboardData() throws RemoteException; class Stub { public static IClipboard asInterface(IBinder binder){return (IClipboard)binder;} } }',
        }
        paths=[]
        for name,body in fixtures.items():
            path=work/'fixtures'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body+'\n');paths.append(str(path))
        classes=work/'classes';classes.mkdir()
        subprocess.run([javac,'-J-Xmx256m','--release','8','-d',str(classes),*paths,str(wrapper),str(repo/'tests/android/clipboard-client/ClipboardClientTest.java')],check=True)
        subprocess.run([java,'-Xmx128m','-cp',str(classes),'ClipboardClientTest'],check=True,timeout=10)
        print('Pinned AIDL generation/API compilation and production SDK wrapper checks passed; no Android IPC claim')


if __name__=='__main__':main()
