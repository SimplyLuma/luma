// SPDX-License-Identifier: Apache-2.0
package org.projectluma.androidtest;

import android.app.Activity;
import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.RemoteInput;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import org.json.JSONObject;

public final class MainActivity extends Activity {
    private EditText editor;
    private Uri document;
    private volatile boolean nativeGuardArmed;
    private boolean nativeGuardReady;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        TextView title = new TextView(this);
        title.setText("Luma Android integration fixture");
        layout.addView(title);
        editor = new EditText(this);
        editor.setContentDescription("Document contents");
        editor.setText(state == null ? "State survives resizing" : state.getString("text"));
        if (state != null && state.getString("uri") != null) document = Uri.parse(state.getString("uri"));
        layout.addView(editor, new LinearLayout.LayoutParams(-1, 0, 1));
        button(layout, "Post notification", () -> postNotification(this));
        button(layout, "Check privileged boundary", () -> BoundaryProbe.run(this));
        button(layout, "Check native clipboard boundary", () -> NativeClipboardProbe.run(this));
        button(layout, "Check native task boundary", () -> NativeTaskProbe.run(this));
        button(layout, "Copy synthetic text to Luma", () -> {
            android.content.ClipboardManager clipboard =
                    (android.content.ClipboardManager)getSystemService(CLIPBOARD_SERVICE);
            clipboard.setPrimaryClip(android.content.ClipData.newPlainText(
                    "Luma Android fixture", "luma-android-clipboard-fixture"));
            receipt(this, "clipboard-copy", "synthetic-android-text");
        });
        button(layout, "Check synthetic Luma clipboard",
                () -> checkSyntheticClipboard("clipboard-read"));
        button(layout, "Check after switching to a Luma app", () -> {
            receipt(this, "clipboard-host-switch-scheduled", "10 seconds");
            new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(
                    () -> checkSyntheticClipboard("clipboard-after-host-switch"), 10000);
        });
        button(layout, "Open document", () -> {
            Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("text/plain")
                    .addCategory(Intent.CATEGORY_OPENABLE)
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                            | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
            startActivityForResult(intent, 1);
        });
        button(layout, "Save document", () -> {
            if (document == null) return;
            try (OutputStream output = getContentResolver().openOutputStream(document, "wt")) {
                output.write(editor.getText().toString().getBytes(StandardCharsets.UTF_8));
                receipt(this, "document-saved", document.toString());
            } catch (Exception error) { throw new IllegalStateException(error); }
        });
        setContentView(layout);
        layout.post(() -> receipt(this, "layout", layout.getWidth() + "x" + layout.getHeight()));
        receipt(this, "created", state == null ? "fresh" : "restored");
        if (state == null && getIntent().getBooleanExtra("post_notification", false)) postNotification(this);
        if (state == null && getIntent().getBooleanExtra("probe_boundary", false)) BoundaryProbe.run(this);
        if (state == null && getIntent().getBooleanExtra("native_guard_wait", false)) {
            armNativeGuard();
        } else if (state == null) {
            runNativeGuardProbes();
        }
        if (state == null && getIntent().getBooleanExtra("background_notification", false)) {
            // The fixture targets API 28, so it needs no Android 12 exact-alarm
            // permission. This local alarm is not a network push/power test.
            PendingIntent alarm = PendingIntent.getBroadcast(this, 3,
                    new Intent(this, BackgroundReceiver.class),
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            getSystemService(AlarmManager.class).setExactAndAllowWhileIdle(
                    AlarmManager.ELAPSED_REALTIME_WAKEUP,
                    android.os.SystemClock.elapsedRealtime() + 30000, alarm);
            receipt(this, "background-notification-scheduled", "30 seconds");
            finishAndRemoveTask();
        }
    }

    private void runNativeGuardProbes() {
        if (getIntent().getBooleanExtra("probe_native_task", false)) NativeTaskProbe.run(this);
        if (getIntent().getBooleanExtra("probe_native_clipboard", false)) NativeClipboardProbe.run(this);
    }

    private void armNativeGuard() {
        String run = getIntent().getStringExtra("guard_run");
        if (run == null || !java.util.UUID.fromString(run).toString().equals(run))
            throw new IllegalArgumentException("Canonical guard run required");
        java.io.File trigger = new java.io.File(getFilesDir(), "native-guard-" + run + ".trigger");
        if (trigger.exists()) throw new IllegalStateException("Guard trigger already exists");
        nativeGuardArmed = true;
        new Thread(() -> {
            long deadline = android.os.SystemClock.elapsedRealtime() + 90000;
            while (nativeGuardArmed && android.os.SystemClock.elapsedRealtime() < deadline) {
                if (trigger.isFile()) {
                    trigger.delete();
                    receipt(this, "native-guard-released", "private-fixture-trigger");
                    runNativeGuardProbes();
                    return;
                }
                try { Thread.sleep(25); }
                catch (InterruptedException error) { Thread.currentThread().interrupt(); return; }
            }
            if (nativeGuardArmed) receipt(this, "native-guard-timeout", "not-released");
        }, "luma-native-guard-gate").start();
    }

    @Override public void onWindowFocusChanged(boolean focused) {
        super.onWindowFocusChanged(focused);
        if (focused && nativeGuardArmed && !nativeGuardReady) {
            nativeGuardReady = true;
            receipt(this, "native-guard-ready", "focused-before-controls");
        }
    }

    @Override public void onDestroy() {
        nativeGuardArmed = false;
        super.onDestroy();
    }

    private void checkSyntheticClipboard(String event) {
        android.content.ClipboardManager clipboard =
                (android.content.ClipboardManager)getSystemService(CLIPBOARD_SERVICE);
        android.content.ClipData clip = clipboard.getPrimaryClip();
        String result = "empty";
        if (clip != null && clip.getItemCount() > 0) {
            CharSequence value = clip.getItemAt(0).coerceToText(this);
            // Never log arbitrary host clipboard contents.
            if ("luma-host-clipboard-fixture".contentEquals(value == null ? "" : value)) {
                result = "matched-host-fixture";
            } else if ("luma-android-clipboard-fixture".contentEquals(value == null ? "" : value)) {
                result = "retained-android-fixture";
            } else {
                result = "unexpected-content";
            }
        }
        receipt(this, event, result);
        receipt(this, "android-window-focus", hasWindowFocus() ? "focused" : "unfocused");
    }

    private void button(LinearLayout layout, String text, Runnable action) {
        Button button = new Button(this);
        button.setText(text);
        button.setOnClickListener(view -> action.run());
        layout.addView(button);
    }

    @Override public void onSaveInstanceState(Bundle state) {
        state.putString("text", editor.getText().toString());
        if (document != null) state.putString("uri", document.toString());
        super.onSaveInstanceState(state);
        receipt(this, "state-saved", editor.getText().toString());
    }

    @Override public void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != 1 || result != RESULT_OK || data == null) return;
        document = data.getData();
        int granted = data.getFlags() & (Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        getContentResolver().takePersistableUriPermission(document, granted);
        try (InputStream input = getContentResolver().openInputStream(document)) {
            java.io.ByteArrayOutputStream bytes = new java.io.ByteArrayOutputStream();
            byte[] block = new byte[4096];
            int count;
            while ((count = input.read(block)) != -1) {
                if (bytes.size() + count > 1048576) throw new IllegalStateException("Fixture document too large");
                bytes.write(block, 0, count);
            }
            editor.setText(new String(bytes.toByteArray(), StandardCharsets.UTF_8));
            receipt(this, "document-opened", document.toString());
        } catch (Exception error) { throw new IllegalStateException(error); }
    }

    static void postNotification(Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel("integration", "Integration fixture", NotificationManager.IMPORTANCE_DEFAULT));
        if (!manager.areNotificationsEnabled()) {
            receipt(context, "notification-blocked", "permission-or-channel");
            return;
        }
        PendingIntent content = PendingIntent.getActivity(context, 0, new Intent(context, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent read = PendingIntent.getBroadcast(context, 1,
                new Intent(context, ActionReceiver.class).setAction("read"),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent reply = PendingIntent.getBroadcast(context, 2,
                new Intent(context, ActionReceiver.class).setAction("reply"),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_MUTABLE);
        Notification.Action replyAction = new Notification.Action.Builder(0, "Reply", reply)
                .addRemoteInput(new RemoteInput.Builder("reply").setLabel("Reply").build()).build();
        manager.notify(42, new Notification.Builder(context, "integration")
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("Android integration fixture")
                .setContentText("A real Android notification, with actions")
                .setContentIntent(content).setAutoCancel(true)
                .addAction(new Notification.Action.Builder(0, "Mark read", read).build())
                .addAction(replyAction).build());
        receipt(context, "notification-posted", "42");
    }

    static synchronized void receipt(Context context, String event, String value) {
        try {
            JSONObject object = new JSONObject().put("event", event).put("value", value)
                    .put("elapsed_ms", android.os.SystemClock.elapsedRealtime())
                    .put("uid", android.os.Process.myUid()).put("pid", android.os.Process.myPid());
            if (context instanceof Activity) {
                String run = ((Activity)context).getIntent().getStringExtra("guard_run");
                if (run != null) {
                    if (run.length() != 36 || !java.util.UUID.fromString(run).toString().equals(run))
                        throw new IllegalArgumentException("Noncanonical guard run ID");
                    object.put("guard_run", run);
                }
            }
            try (OutputStream output = context.openFileOutput("events.jsonl", Context.MODE_APPEND)) {
                output.write((object.toString() + "\n").getBytes(StandardCharsets.UTF_8));
            }
        } catch (Exception error) { throw new IllegalStateException(error); }
    }
}
