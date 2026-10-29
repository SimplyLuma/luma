// SPDX-License-Identifier: Apache-2.0
package org.projectluma.androidtest;

import android.content.Context;
import android.os.Process;
import org.json.JSONObject;

/** Raw native HAL probe; ordinary APK reads/writes must not bypass ClipboardService. */
public final class NativeClipboardProbe {
    private static final String CLASS = "vendor.waydroid.clipboard.V1_0.IWaydroidClipboard";
    private static final String DESCRIPTOR = "vendor.waydroid.clipboard@1.0::IWaydroidClipboard";
    private static final String HOST = "luma-host-clipboard-fixture";
    private static final String ANDROID = "luma-android-clipboard-fixture";
    private static final String ATTEMPT = "luma-android-forbidden-clipboard-write";

    private static Object service() throws Exception {
        Class<?> type = Class.forName(CLASS);
        return type.getMethod("getService", boolean.class).invoke(null, false);
    }
    private static Object rawService() throws Exception {
        // The image's generated vendor interface is not a registered app
        // shared library. Exercise its HIDL wire ABI without granting access
        // to the private platform jar or changing hidden-API policy.
        return Class.forName("android.os.HwBinder")
                .getMethod("getService", String.class, String.class, boolean.class)
                .invoke(null, DESCRIPTOR, "default", false);
    }
    private static Object rawTransaction(Object endpoint, int code) throws Exception {
        Class<?> parcel = Class.forName("android.os.HwParcel");
        Object request = parcel.getConstructor().newInstance();
        Object reply = parcel.getConstructor().newInstance();
        try {
            parcel.getMethod("writeInterfaceToken", String.class).invoke(request, DESCRIPTOR);
            // IWaydroidClipboard.hal:1 setter,2 getter; both synchronous.
            if (code == 1) parcel.getMethod("writeString", String.class).invoke(request, ATTEMPT);
            Class.forName("android.os.IHwBinder").getMethod("transact", int.class,
                    parcel, parcel, int.class).invoke(endpoint, code, request, reply, 0);
            parcel.getMethod("verifySuccess").invoke(reply);
            return code == 2 ? parcel.getMethod("readString").invoke(reply) : null;
        } finally {
            parcel.getMethod("releaseTemporaryStorage").invoke(request);
            parcel.getMethod("release").invoke(reply);
        }
    }
    private static String read(Object endpoint, boolean raw) throws Exception {
        if (endpoint == null) return "unavailable";
        Object value = raw ? rawTransaction(endpoint, 2)
                : Class.forName(CLASS).getMethod("getClipboardData").invoke(endpoint);
        if (!(value instanceof String)) return "unavailable";
        if ("".equals(value)) return "empty";
        if (HOST.equals(value)) return "host-fixture";
        if (ANDROID.equals(value)) return "android-fixture";
        if (ATTEMPT.equals(value)) return "forbidden-write-fixture";
        return "other-content"; // Never record arbitrary clipboard contents.
    }

    public static void run(Context context) {
        new Thread(() -> {
            String phase = "service";
            try {
                Object endpoint = rawService();
                phase = "read-before";
                MainActivity.receipt(context, "native-clipboard-before", read(endpoint, true));
                if (endpoint == null) return;
                phase = "write";
                rawTransaction(endpoint, 1);
                // A successful HIDL transport is not proof the write was admitted.
                MainActivity.receipt(context, "native-clipboard-write", "request-returned");
                phase = "read-after";
                MainActivity.receipt(context, "native-clipboard-after", read(endpoint, true));
            } catch (Exception error) {
                MainActivity.receipt(context, "native-clipboard-probe", "unavailable");
                String type = error.getClass().getSimpleName();
                if (error.getCause() != null) type += "/" + error.getCause().getClass().getSimpleName();
                MainActivity.receipt(context, "native-clipboard-error-type", phase + ":" + type);
            }
        }, "luma-native-clipboard-probe").start();
    }

    /** Fixed read-only native-service control, run via app_process as UID1000. */
    public static void main(String[] args) throws Exception {
        JSONObject result = new JSONObject();
        result.put("uid", Process.myUid());
        if (args.length != 1 || !"read".equals(args[0])) {
            throw new IllegalArgumentException("Only the fixed read control is supported");
        }
        try { result.put("state", read(service(), false)); }
        catch (Exception error) { result.put("state", "unavailable"); }
        System.out.println(result.toString());
    }
}
