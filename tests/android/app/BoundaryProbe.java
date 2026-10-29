// SPDX-License-Identifier: Apache-2.0
package org.projectluma.androidtest;

import android.content.Context;
import android.os.IBinder;
import android.os.Parcel;

/** Read-only probe from this APK's real, unprivileged process identity. */
final class BoundaryProbe {
    static void run(Context context) {
        probe(context, "waydroidplatform", "lineageos.waydroid.IPlatform", 3,
                "platform-boundary");
        // An unknown transaction has no hardware side effects. Compare the
        // real endpoint's permission denial with the root caller control.
        probe(context, "waydroidhardware", "lineageos.waydroid.IHardware",
                0x00010000, "root-hardware-boundary");
    }

    private static void probe(Context context, String name, String descriptor,
                              int transaction, String event) {
        new Thread(() -> {
            String outcome = "unavailable";
            Parcel request = Parcel.obtain();
            Parcel reply = Parcel.obtain();
            try {
                Class<?> manager = Class.forName("android.os.ServiceManager");
                IBinder service = (IBinder) manager.getDeclaredMethod("getService", String.class)
                        .invoke(null, name);
                if (service != null) {
                    request.writeInterfaceToken(descriptor);
                    if (service.transact(transaction, request, reply, 0)) {
                        if (reply.dataSize() < 4) {
                            outcome = "unavailable:empty-reply";
                        } else {
                            reply.readException();
                            outcome = "allowed";
                        }
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
            MainActivity.receipt(context, event, outcome);
        }, "fixture-boundary-probe").start();
    }
}
