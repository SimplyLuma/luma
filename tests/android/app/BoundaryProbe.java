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
        probe(context, "waydroidclipboard", "lineageos.waydroid.IClipboard",
                0x00010000, "host-clipboard-boundary");
        probe(context, "waydroidusermonitor", "lineageos.waydroid.IUserMonitor",
                0x00010000, "host-user-monitor-boundary");
        probeProperty(context);
    }

    // Fixed, volatile fixture key only. The test driver checks native-service
    // positive controls and resets the key; no power or control key is touched.
    private static void probeProperty(Context context) {
        new Thread(() -> {
            String outcome = "unavailable";
            Process operation = null;
            try {
                operation = new ProcessBuilder("/system/bin/setprop",
                        "luma.test.property_boundary", "fixture-app").redirectErrorStream(true).start();
                if (operation.waitFor(5, java.util.concurrent.TimeUnit.SECONDS)) {
                    outcome = operation.exitValue() == 0 ? "accepted" :
                            "rejected:" + operation.exitValue();
                } else {
                    outcome = "unavailable:timeout";
                }
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                outcome = "unavailable:interrupted";
            } catch (Exception unavailable) {
                outcome = "unavailable:" + unavailable.getClass().getSimpleName();
            } finally {
                if (operation != null) {
                    if (operation.isAlive()) operation.destroyForcibly();
                    try { operation.getInputStream().close(); } catch (java.io.IOException ignored) {}
                }
            }
            MainActivity.receipt(context, "property-boundary", outcome);
        }, "fixture-property-probe").start();
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
