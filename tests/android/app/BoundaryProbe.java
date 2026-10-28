// SPDX-License-Identifier: Apache-2.0
package org.projectluma.androidtest;

import android.content.Context;
import android.os.IBinder;
import android.os.Parcel;

/** Read-only probe from this APK's real, unprivileged process identity. */
final class BoundaryProbe {
    static void run(Context context) {
        new Thread(() -> {
            String outcome = "unavailable";
            Parcel request = Parcel.obtain();
            Parcel reply = Parcel.obtain();
            try {
                Class<?> manager = Class.forName("android.os.ServiceManager");
                IBinder service = (IBinder) manager.getDeclaredMethod("getService", String.class)
                        .invoke(null, "waydroidplatform");
                if (service != null) {
                    request.writeInterfaceToken("lineageos.waydroid.IPlatform");
                    // getAppsInfo, with no paths, commands or mutable data.
                    if (service.transact(3, request, reply, 0)) {
                        reply.readException();
                        outcome = "allowed";
                    }
                }
            } catch (SecurityException denied) {
                outcome = "denied";
            } catch (Exception unavailable) {
                outcome = "unavailable:" + unavailable.getClass().getSimpleName();
            } finally {
                request.recycle();
                reply.recycle();
            }
            MainActivity.receipt(context, "platform-boundary", outcome);
        }, "fixture-boundary-probe").start();
    }
}
