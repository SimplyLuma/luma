/* SPDX-License-Identifier: Apache-2.0 */
package org.lineageos.platform.internal;

/** Admission for the existing, non-UID-remapped Waydroid host ABI. */
final class HostCallerPolicy {
    private HostCallerPolicy() {}

    static boolean allowed(int callerUid, int hostUid) {
        // Android root and system_server are part of the trusted framework.
        if (callerUid == 0 || callerUid == 1000) return true;
        // Do not admit an APK/isolated/profile UID as the desktop identity.
        // UID-remapped runtimes require a separately authenticated transport.
        return hostUid >= 1000 && hostUid < 10000 && callerUid == hostUid;
    }
}
