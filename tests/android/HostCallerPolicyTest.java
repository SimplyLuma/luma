/* SPDX-License-Identifier: Apache-2.0 */
package org.lineageos.platform.internal;

public final class HostCallerPolicyTest {
    public static void main(String[] args) {
        int checked = 0;
        for (int host : new int[]{-1, 0, 999, 1000, 1001, 9999, 10000, 10125, 99000, 1010125}) {
            for (int caller : new int[]{-1, 0, 999, 1000, 1001, 9999, 10000, 10125, 99000, 1010125}) {
                boolean expected = caller == 0 || caller == 1000
                    || (caller == host && (host == 1001 || host == 9999));
                if (HostCallerPolicy.allowed(caller, host) != expected)
                    throw new AssertionError("Admission mismatch: caller=" + caller + ", host=" + host);
                checked++;
            }
        }
        System.out.println("PASS: " + checked + " kernel UID / host identity combinations");
    }
}
