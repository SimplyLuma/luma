// SPDX-License-Identifier: Apache-2.0
package org.projectluma.androidtest;

import android.content.Context;
import android.os.Process;
import org.json.JSONObject;

/** Read-only raw HIDL control for the privileged task-forwarding service. */
public final class NativeTaskProbe {
    private static final String DESCRIPTOR = "vendor.waydroid.task@1.0::IWaydroidTask";
    private static final String PACKAGE = "org.projectluma.androidtest";
    private static final String LABEL = "Android Integration Test";

    private static String read() throws Exception {
        Object endpoint = Class.forName("android.os.HwBinder")
                .getMethod("getService", String.class, String.class, boolean.class)
                .invoke(null, DESCRIPTOR, "default", false);
        if (endpoint == null) return "unavailable";
        Class<?> parcel = Class.forName("android.os.HwParcel");
        Object request = parcel.getConstructor().newInstance();
        Object reply = parcel.getConstructor().newInstance();
        try {
            parcel.getMethod("writeInterfaceToken", String.class).invoke(request, DESCRIPTOR);
            parcel.getMethod("writeString", String.class).invoke(request, PACKAGE);
            // IWaydroidTask.hal declares getAppName as synchronous transaction4.
            Class.forName("android.os.IHwBinder").getMethod("transact", int.class,
                    parcel, parcel, int.class).invoke(endpoint, 4, request, reply, 0);
            parcel.getMethod("verifySuccess").invoke(reply);
            Object value = parcel.getMethod("readString").invoke(reply);
            if (!(value instanceof String)) return "unavailable";
            if ("".equals(value)) return "empty";
            if (LABEL.equals(value)) return "fixture-label";
            return "other-label";
        } finally {
            parcel.getMethod("releaseTemporaryStorage").invoke(request);
            parcel.getMethod("release").invoke(reply);
        }
    }

    public static void run(Context context) {
        new Thread(() -> {
            try {
                MainActivity.receipt(context, "native-task-name", read());
            } catch (Exception error) {
                MainActivity.receipt(context, "native-task-name", "unavailable");
                String type = error.getClass().getSimpleName();
                if (error.getCause() != null) type += "/" + error.getCause().getClass().getSimpleName();
                MainActivity.receipt(context, "native-task-error-type", type);
            }
        }, "luma-native-task-probe").start();
    }

    /** Same read-only wire request as the app, run as trusted UID1000. */
    public static void main(String[] args) throws Exception {
        if (args.length != 1 || !"read".equals(args[0])) {
            throw new IllegalArgumentException("Only the fixed read control is supported");
        }
        JSONObject result = new JSONObject();
        result.put("uid", Process.myUid());
        try { result.put("state", read()); }
        catch (Exception error) { result.put("state", "unavailable"); }
        System.out.println(result.toString());
    }
}
