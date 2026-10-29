/* SPDX-License-Identifier: Apache-2.0 */
package org.lineageos.platform.internal;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.RemoteInput;
import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import java.util.UUID;
import java.util.concurrent.FutureTask;

public final class NotificationReplyTest {
    static void check(boolean value) { if (!value) throw new AssertionError(); }
    static String request() { return UUID.randomUUID().toString(); }
    static Notification.Action action() {
        Notification.Action action = new Notification.Action();
        action.actionIntent = new PendingIntent();
        action.inputs = new RemoteInput[] {new RemoteInput()};
        return action;
    }
    public static void main(String[] ignored) throws Exception {
        Handler handler = new Handler();
        Looper.CURRENT.set(Looper.MAIN);
        NotificationReplyDispatcher dispatcher = new NotificationReplyDispatcher(new Context(), handler, 1003);
        Notification.Action action = action();
        dispatcher.replace(17, "reply-1", action);
        String id = request();
        check(dispatcher.reply(1000, 17, "reply-1", id, "fixture answer"));
        check(action.actionIntent.sends == 1);
        check(dispatcher.reply(1003, 17, "reply-1", id, "fixture answer"));
        check(!dispatcher.reply(1004, 17, "reply-1", request(), "fixture answer"));
        check("fixture answer".contentEquals(action.actionIntent.last.results.get("answer")));
        check(action.actionIntent.last.source == RemoteInput.SOURCE_FREE_FORM_INPUT);
        check(dispatcher.reply(1000, 17, "reply-1", id, "fixture answer"));
        check(!dispatcher.reply(1000, 17, "reply-1", id, "changed answer"));
        check(!dispatcher.reply(10000, 17, "reply-1", request(), "fixture answer"));
        check(!dispatcher.reply(1000, 17, "wrong", request(), "fixture answer"));
        for (String text : new String[] {"", "\t\n ", "a\0b", "\ud800", "\udc00", "x".repeat(4097), "é".repeat(2049)})
            check(!dispatcher.reply(1000, 17, "reply-1", request(), text));
        check(!dispatcher.reply(1000, 17, "reply-1", "bad UUID", "fixture answer"));
        check(action.actionIntent.sends == 1);
        dispatcher.replace(17, "reply-2", action);
        check(!dispatcher.reply(1000, 17, "reply-1", id, "fixture answer"));
        dispatcher.remove(17);
        check(!dispatcher.reply(1000, 17, "reply-2", request(), "fixture answer"));
        action.actionIntent.immutable = true;
        check(NotificationReplyDispatcher.textInput(action) == null);
        action.actionIntent.immutable = false;
        action.auth = true;
        check(NotificationReplyDispatcher.textInput(action) == null);
        action.auth = false;
        action.inputs = new RemoteInput[] {new RemoteInput(), new RemoteInput()};
        check(NotificationReplyDispatcher.textInput(action) == null);
        action.inputs = new RemoteInput[] {new RemoteInput()};
        action.inputs[0].free = false;
        check(NotificationReplyDispatcher.textInput(action) == null);
        action = action();
        dispatcher.replace(18, "reply-3", action);
        action.actionIntent.canceled = true;
        id = request();
        check(!dispatcher.reply(1000, 18, "reply-3", id, "fixture answer"));
        check(!dispatcher.reply(1000, 18, "reply-3", id, "fixture answer"));
        check(action.actionIntent.sends == 1);
        // Keep every receipt; filling the bound must not evict and resend.
        Notification.Action bounded = action();
        dispatcher.replace(19, "bounded", bounded);
        id = request();
        check(dispatcher.reply(1000, 19, "bounded", id, "first"));
        for (int i = 1; i < 32; i++) check(dispatcher.reply(1000, 19, "bounded", request(), "next"));
        check(!dispatcher.reply(1000, 19, "bounded", request(), "overflow"));
        check(dispatcher.reply(1000, 19, "bounded", id, "first"));
        check(bounded.actionIntent.sends == 32);
        Notification.Action queued = action();
        dispatcher.replace(20, "queued", queued);
        Looper.CURRENT.remove();
        FutureTask<Boolean> cancelled = new FutureTask<>(() -> dispatcher.reply(1000, 20, "queued", request(), "queued"));
        Thread waiter = new Thread(cancelled); waiter.start();
        check(!cancelled.get(7, java.util.concurrent.TimeUnit.SECONDS));
        handler.drain();
        check(queued.actionIntent.sends == 0);
        FutureTask<Boolean> valid = new FutureTask<>(() -> dispatcher.reply(1000, 20, "queued", request(), "queued"));
        waiter = new Thread(valid); waiter.start();
        long deadline = System.nanoTime() + 2_000_000_000L;
        while (handler.queue.isEmpty() && System.nanoTime() < deadline) Thread.yield();
        check(!handler.queue.isEmpty()); handler.drain();
        check(valid.get()); check(queued.actionIntent.sends == 1);
        // A running send can outlive the bounded wait. Its late completion
        // reconciles the same receipt, and never authorizes another send.
        Looper.CURRENT.set(Looper.MAIN);
        Notification.Action late = action();
        dispatcher.replace(21, "late", late);
        java.util.concurrent.CountDownLatch sending = new java.util.concurrent.CountDownLatch(1);
        java.util.concurrent.CountDownLatch finish = new java.util.concurrent.CountDownLatch(1);
        late.actionIntent.beforeSend = () -> {
            sending.countDown();
            try { finish.await(); } catch (InterruptedException error) { throw new AssertionError(error); }
        };
        final String lateId = request();
        Looper.CURRENT.remove();
        FutureTask<Boolean> uncertain = new FutureTask<>(() -> dispatcher.reply(1000, 21, "late", lateId, "late"));
        new Thread(uncertain).start();
        deadline = System.nanoTime() + 2_000_000_000L;
        while (handler.queue.isEmpty() && System.nanoTime() < deadline) Thread.yield();
        check(!handler.queue.isEmpty());
        Thread owner = new Thread(handler::drain); owner.start();
        check(sending.await(2, java.util.concurrent.TimeUnit.SECONDS));
        try { check(!uncertain.get(7, java.util.concurrent.TimeUnit.SECONDS)); }
        finally { finish.countDown(); }
        owner.join(2000); check(!owner.isAlive());
        Looper.CURRENT.set(Looper.MAIN);
        check(dispatcher.reply(1000, 21, "late", lateId, "late"));
        check(late.actionIntent.sends == 1);
        Looper.CURRENT.set(Looper.MAIN); dispatcher.clear();
        check(!dispatcher.reply(1000, 20, "queued", request(), "after reset"));
        System.out.println("PASS original RemoteInput handoff, UID/text admission, deduplication, bounds, revocation and cancelled queued work");
    }
}
