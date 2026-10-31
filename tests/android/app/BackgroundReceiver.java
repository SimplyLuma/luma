// SPDX-License-Identifier: Apache-2.0
package org.projectluma.androidtest;

/** Source-owned local delivery after the fixture has removed its task. */
public final class BackgroundReceiver extends android.content.BroadcastReceiver {
    @Override public void onReceive(android.content.Context context, android.content.Intent intent) {
        MainActivity.receipt(context, "background-notification-fired", "local alarm");
        MainActivity.postNotification(context);
    }
}
