/* SPDX-License-Identifier: Apache-2.0 */
package org.lineageos.platform.internal;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.RemoteInput;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/** Main-looper-owned reply grants. An acknowledgement means PendingIntent.send
 * returned successfully, not that a remote messaging server delivered the text.
 */
final class NotificationReplyDispatcher {
    private final Context context;
    private final Handler handler;
    private final int hostUid;
    private final Map<Integer, Target> targets = new HashMap<>();
    private final AtomicInteger pending = new AtomicInteger();

    private static final class Receipt {
        final byte[] digest;
        boolean accepted;
        Receipt(byte[] digest) { this.digest = digest; }
    }

    private static final class Target {
        final String actionId;
        final Notification.Action action;
        final RemoteInput input;
        final Map<String, Receipt> receipts = new HashMap<>();
        Target(String actionId, Notification.Action action, RemoteInput input) {
            this.actionId = actionId;
            this.action = action;
            this.input = input;
        }
    }

    NotificationReplyDispatcher(Context context, Handler handler, int hostUid) {
        this.context = context;
        this.handler = handler;
        this.hostUid = hostUid;
    }

    /** A single free-form input can be represented faithfully by one entry.
     * Authentication-required actions must use the app's own unlock flow.
     */
    static RemoteInput textInput(Notification.Action action) {
        if (action == null || action.actionIntent == null || action.actionIntent.isImmutable()
                || action.isAuthenticationRequired()) return null;
        RemoteInput[] inputs = action.getRemoteInputs();
        if (inputs == null || inputs.length != 1 || !inputs[0].getAllowFreeFormInput()
                || inputs[0].getResultKey() == null || inputs[0].getResultKey().isEmpty()) return null;
        return inputs[0];
    }

    private void onLooper() {
        if (Looper.myLooper() != handler.getLooper())
            throw new IllegalStateException("Notification replies require their owner looper");
    }

    void replace(int id, String actionId, Notification.Action action) {
        onLooper();
        targets.remove(id);
        RemoteInput input = textInput(action);
        if (input != null && actionId != null && targets.size() < 512)
            targets.put(id, new Target(actionId, action, input));
    }

    void remove(int id) { onLooper(); targets.remove(id); }
    void clear() { onLooper(); targets.clear(); }

    static boolean validText(String text) {
        if (text == null || text.length() > 4096) return false;
        boolean nonblank = false;
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c == 0 || Character.isLowSurrogate(c)) return false;
            if (Character.isHighSurrogate(c)) {
                if (++i >= text.length() || !Character.isLowSurrogate(text.charAt(i))) return false;
            }
            if (!Character.isWhitespace(c)) nonblank = true;
        }
        return nonblank && text.getBytes(StandardCharsets.UTF_8).length <= 4096;
    }

    /** Binder callers wait at most five seconds. Cancelled queued work cannot
     * run; work already sending may finish, so false must never trigger a replay.
     */
    boolean reply(int callerUid, int id, String actionId, String requestId, String text) {
        if (!HostCallerPolicy.allowed(callerUid, hostUid) || requestId == null
                || !requestId.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
                || !validText(text)) return false;
        if (Looper.myLooper() == handler.getLooper())
            return dispatch(id, actionId, requestId, text);
        if (pending.incrementAndGet() > 8) { pending.decrementAndGet(); return false; }
        FutureTask<Boolean> task = new FutureTask<>(() -> dispatch(id, actionId, requestId, text));
        if (!handler.post(() -> {
            try { task.run(); } finally { pending.decrementAndGet(); }
        })) { pending.decrementAndGet(); return false; }
        try {
            return task.get(5, TimeUnit.SECONDS);
        } catch (InterruptedException error) {
            task.cancel(false);
            Thread.currentThread().interrupt();
        } catch (Exception error) {
            task.cancel(false);
        }
        return false;
    }

    private boolean dispatch(int id, String actionId, String requestId, String text) {
        onLooper();
        Target target = targets.get(id);
        if (target == null || !target.actionId.equals(actionId)) return false;
        final byte[] digest;
        try {
            digest = MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException error) { return false; }
        Receipt previous = target.receipts.get(requestId);
        if (previous != null)
            return MessageDigest.isEqual(previous.digest, digest) && previous.accepted;
        // Never evict an active receipt: that would permit a duplicate send.
        if (target.receipts.size() >= 32) return false;
        Receipt receipt = new Receipt(digest);
        target.receipts.put(requestId, receipt);
        Bundle results = new Bundle();
        results.putCharSequence(target.input.getResultKey(), text);
        Intent fillIn = new Intent();
        RemoteInput.addResultsToIntent(new RemoteInput[] {target.input}, fillIn, results);
        RemoteInput.setResultsSource(fillIn, RemoteInput.SOURCE_FREE_FORM_INPUT);
        try {
            target.action.actionIntent.send(context, 0, fillIn);
            receipt.accepted = true;
        } catch (PendingIntent.CanceledException | RuntimeException error) {
            // No notification text, token or application exception in logs.
        }
        return receipt.accepted;
    }
}
