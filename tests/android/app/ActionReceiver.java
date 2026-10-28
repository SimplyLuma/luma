// SPDX-License-Identifier: Apache-2.0
package org.projectluma.androidtest;

public final class ActionReceiver extends android.content.BroadcastReceiver {
    @Override public void onReceive(android.content.Context context, android.content.Intent intent) {
        android.os.Bundle reply = android.app.RemoteInput.getResultsFromIntent(intent);
        MainActivity.receipt(context, "action:" + intent.getAction(),
                reply == null ? "" : String.valueOf(reply.getCharSequence("reply")));
        context.getSystemService(android.app.NotificationManager.class).cancel(42);
    }
}
