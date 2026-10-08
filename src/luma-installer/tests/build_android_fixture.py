#!/usr/bin/env python3
"""Build a disposable, permission-free Android fixture with a supplied SDK/JDK.

Build tools and signing key are developer test inputs, never image payloads.
The activity writes only one private marker for data-retention verification.
"""
from pathlib import Path
import os, subprocess, sys, tempfile
sdk, java = map(Path, sys.argv[1:3])
root = Path(tempfile.mkdtemp(prefix='luma-install-apk-fixture-'))
(root/'AndroidManifest.xml').write_text('''<manifest xmlns:android="http://schemas.android.com/apk/res/android"
 package="org.projectluma.installtest" android:versionCode="1" android:versionName="1.0">
 <uses-sdk android:minSdkVersion="23" android:targetSdkVersion="28"/>
 <application android:label="Install Test" android:debuggable="true">
 <activity android:name=".MainActivity" android:exported="true">
 <intent-filter><action android:name="android.intent.action.MAIN"/>
 <category android:name="android.intent.category.LAUNCHER"/></intent-filter>
 </activity></application></manifest>''')
tools=sdk/'build-tools/35.0.0'
subprocess.run([str(tools/'aapt'),'package','-f','-M',str(root/'AndroidManifest.xml'),'-I',
 str(sdk/'platforms/android-35/android.jar'),'-F',str(root/'unsigned.apk')],check=True)
(root/'MainActivity.java').write_text('package org.projectluma.installtest;\npublic class MainActivity extends android.app.Activity {\n public void onCreate(android.os.Bundle state) {\n  super.onCreate(state);\n  try { java.io.File file = new java.io.File(getFilesDir(), "luma-install-retention-fixture");\n   if (!file.exists()) { java.io.FileOutputStream out = new java.io.FileOutputStream(file);\n    out.write("retained".getBytes("UTF-8")); out.close(); }\n  } catch (java.io.IOException e) { throw new RuntimeException(e); }\n  android.widget.TextView text = new android.widget.TextView(this);\n  text.setText("Luma Install disposable test"); setContentView(text);\n }\n}')
(root/'classes').mkdir()
subprocess.run([str(java/'bin/javac'),'-source','8','-target','8','-bootclasspath',
 str(sdk/'platforms/android-35/android.jar'),'-d',str(root/'classes'),str(root/'MainActivity.java')],check=True)
(root/'dex').mkdir()
subprocess.run([str(tools/'d8'),'--min-api','23','--output',str(root/'dex'),
 str(root/'classes/org/projectluma/installtest/MainActivity.class')],check=True,env={**os.environ,'JAVA_HOME':str(java)})
subprocess.run([str(tools/'aapt'),'add',str(root/'unsigned.apk'),'classes.dex'],cwd=root/'dex',check=True,stdout=subprocess.DEVNULL)
subprocess.run([str(java/'bin/keytool'),'-genkeypair','-keystore',str(root/'fixture.jks'),
 '-storepass','fixture-only','-keypass','fixture-only','-alias','fixture','-dname','CN=Luma disposable fixture',
 '-keyalg','RSA','-validity','30'],check=True,stdout=subprocess.DEVNULL)
subprocess.run([str(tools/'apksigner'),'sign','--ks',str(root/'fixture.jks'),'--ks-pass','pass:fixture-only',
 '--out',str(root/'InstallTest.apk'),str(root/'unsigned.apk')],check=True,env={**os.environ,'JAVA_HOME':str(java)})
print(root/'InstallTest.apk')
