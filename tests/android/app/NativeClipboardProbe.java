// SPDX-License-Identifier: Apache-2.0
package org.projectluma.androidtest;

import android.content.Context;
import android.os.Process;
import java.lang.reflect.Method;
import org.json.JSONObject;

/** Raw native HAL probe; ordinary APK reads/writes must not bypass ClipboardService. */
public final class NativeClipboardProbe {
    private static final String CLASS = "vendor.waydroid.clipboard.V1_0.IWaydroidClipboard";
    private static final String HOST = "luma-host-clipboard-fixture";
    private static final String ANDROID = "luma-android-clipboard-fixture";
    private static final String ATTEMPT = "luma-android-forbidden-clipboard-write";

    private static Object service() throws Exception {
        Class<?> type = Class.forName(CLASS);
        return type.getMethod("getService", boolean.class).invoke(null, false);
    }
    private static String read(Object endpoint) throws Exception {
        if (endpoint == null) return "unavailable";
        Method method = Class.forName(CLASS).getMethod("getClipboardData");
        Object value = method.invoke(endpoint);
        if (!(value instanceof String)) return "unavailable";
        if ("".equals(value)) return "empty";
        if (HOST.equals(value)) return "host-fixture";
        if (ANDROID.equals(value)) return "android-fixture";
        if (ATTEMPT.equals(value)) return "forbidden-write-fixture";
        return "other-content"; // Never record arbitrary clipboard contents.
    }

    public static void run(Context context) {
        new Thread(() -> {
            try {
                Object endpoint = service();
                MainActivity.receipt(context, "native-clipboard-before", read(endpoint));
                if (endpoint == null) return;
                Class.forName(CLASS).getMethod("sendClipboardData", String.class)
                        .invoke(endpoint, ATTEMPT);
                // A successful HIDL transport is not proof the write was admitted.
                MainActivity.receipt(context, "native-clipboard-write", "request-returned");
                MainActivity.receipt(context, "native-clipboard-after", read(endpoint));
            } catch (Exception error) {
                MainActivity.receipt(context, "native-clipboard-probe", "unavailable");
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
        try { result.put("state", read(service())); }
        catch (Exception error) { result.put("state", "unavailable"); }
        System.out.println(result.toString());
    }
}
