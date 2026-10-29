#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compile Android reply code against API33, then exercise it with JVM fixtures.

This is production dispatcher logic with explicit Android dependency fixtures,
not real Binder, PendingIntent delivery, notification rendering or device proof.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-root', required=True, type=Path)
args = parser.parse_args()
repo = Path(__file__).resolve().parents[2]
source = args.source_root.resolve()
java_bin = source / 'prebuilts/jdk/jdk17/linux-x86/bin'
sdk = source / 'prebuilts/sdk/33/public/android.jar'
dispatcher = repo / 'patches/android-lineage-sdk/java/NotificationReplyDispatcher.java'
caller_policy = repo / 'patches/android-lineage-sdk/java/HostCallerPolicy.java'

fixtures = {
    'android/content/Context.java': 'package android.content; public class Context {}',
    'android/content/Intent.java': 'package android.content; public class Intent { public android.os.Bundle results; public int source; }',
    'android/os/Bundle.java': 'package android.os; public class Bundle extends java.util.HashMap<String,CharSequence> { public void putCharSequence(String k,CharSequence v){put(k,v);} }',
    'android/os/Looper.java': 'package android.os; public class Looper { public static final Looper MAIN=new Looper(); public static final ThreadLocal<Looper> CURRENT=new ThreadLocal<>(); public static Looper myLooper(){return CURRENT.get();} }',
    'android/os/Handler.java': '''package android.os; public class Handler {
        public final java.util.concurrent.BlockingQueue<Runnable> queue=new java.util.concurrent.LinkedBlockingQueue<>();
        public boolean alive=true;
        public Looper getLooper(){return Looper.MAIN;}
        public boolean post(Runnable r){if(!alive)return false;return queue.add(r);}
        public void drain(){Looper.CURRENT.set(Looper.MAIN);try{Runnable r;while((r=queue.poll())!=null)r.run();}finally{Looper.CURRENT.remove();}}
    }''',
    'android/app/PendingIntent.java': '''package android.app; public class PendingIntent {
        public static class CanceledException extends Exception {}
        public boolean immutable,canceled;public int sends;public android.content.Intent last;public Runnable beforeSend;
        public boolean isImmutable(){return immutable;}
        public void send(android.content.Context c,int code,android.content.Intent i)throws CanceledException {
            sends++;last=i;if(beforeSend!=null)beforeSend.run();if(canceled)throw new CanceledException();
        }
    }''',
    'android/app/Notification.java': '''package android.app; public class Notification { public static class Action {
        public PendingIntent actionIntent;public RemoteInput[] inputs;public boolean auth;
        public RemoteInput[] getRemoteInputs(){return inputs;}public boolean isAuthenticationRequired(){return auth;}
    }}''',
    'android/app/RemoteInput.java': '''package android.app; public class RemoteInput {
        public static final int SOURCE_FREE_FORM_INPUT=0;public boolean free=true;public String key="answer";
        public boolean getAllowFreeFormInput(){return free;}public String getResultKey(){return key;}
        public static void addResultsToIntent(RemoteInput[] inputs,android.content.Intent i,android.os.Bundle b){i.results=b;}
        public static void setResultsSource(android.content.Intent i,int s){i.source=s;}
    }''',
}

with tempfile.TemporaryDirectory() as directory:
    work = Path(directory)
    classes = work / 'classes'
    classes.mkdir()
    # Use actual, pinned API33 signatures first. Android stubs do not run on JVM.
    subprocess.run([str(java_bin / 'javac'), '-J-Xmx256m', '--release', '11',
                    '-classpath', str(sdk), '-d', str(classes), str(dispatcher), str(caller_policy)], check=True)
    # Apply against the preserved post-Waydroid / lifecycle baseline. Clipboard
    # patch0004 touches a separate file and does not change this protocol.
    prepared = work / 'sdk'
    names = ('sdk/src/java/lineageos/waydroid/INotifications.aidl',
             'sdk/src/java/lineageos/waydroid/Notifications.java',
             'lineage/lib/main/java/org/lineageos/platform/internal/WayDroidService.java')
    for name in names:
        target = prepared / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / 'lineage-sdk' / name, target)
    subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(prepared), '-i',
                    str(repo / 'patches/android-lineage-sdk/0005-luma-native-notification-replies.patch')],
                   check=True)
    assert (prepared / 'lineage/lib/main/java/org/lineageos/platform/internal/NotificationReplyDispatcher.java').read_bytes() == dispatcher.read_bytes()
    generated = work / 'generated'
    generated.mkdir()
    subprocess.run([str(source / 'prebuilts/build-tools/linux-x86/bin/aidl'),
                    '--structured', '-o' + str(generated),
                    str(prepared / names[0])], check=True, timeout=20)
    protocol = (generated / 'lineageos/waydroid/INotifications.java').read_text()
    for name, number in (('registerListener', 0), ('notify', 1), ('closeNotification', 2),
                         ('getCapabilities', 3), ('notifyWithReply', 4)):
        assert f'TRANSACTION_{name} = (android.os.IBinder.FIRST_CALL_TRANSACTION + {number})' in protocol
    for name, number in (('onActionInvoked', 0), ('onHostDismissed', 1),
                         ('onHostReset', 2), ('onReplyRequested', 3)):
        assert f'TRANSACTION_{name} = (android.os.IBinder.FIRST_CALL_TRANSACTION + {number})' in protocol
    subprocess.run([str(java_bin / 'javac'), '-J-Xmx256m', '--release', '11',
                    '-classpath', str(sdk), '-d', str(classes),
                    str(generated / 'lineageos/waydroid/INotifications.java')], check=True)
    shutil.rmtree(classes)
    classes.mkdir()
    paths = []
    for name, body in fixtures.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body + '\n')
        paths.append(str(path))
    subprocess.run([str(java_bin / 'javac'), '-J-Xmx256m', '--release', '11',
                    '-d', str(classes), *paths, str(dispatcher), str(caller_policy),
                    str(repo / 'tests/android/notification-reply/NotificationReplyTest.java')], check=True)
    subprocess.run([str(java_bin / 'java'), '-Xmx128m', '-cp', str(classes),
                    'org.lineageos.platform.internal.NotificationReplyTest'], check=True, timeout=25)
print('PASS actual API33 compilation and production dispatcher dependency-fixture checks; no Android IPC claim')
