package org.projectluma.connect.approval

import android.app.Notification
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.SystemClock
import org.projectluma.connect.R
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Approval
import org.projectluma.connect.service.Notifications
import org.projectluma.connect.service.RequestChannel

/**
 * `auth.request` from the computer. Nothing is approved in the background: the phone
 * shows a notification, and only a tap, a visible explanation and a biometric or
 * screen-lock check in [ApprovalActivity] can produce a signed approval.
 */
object ApprovalRequests {
    const val ID_NOTIFICATION = 41

    data class Pending(
        val request: Approval.Request,
        val desktopPin: String,
        val desktopName: String,
        /** `SystemClock.elapsedRealtime()` deadline, so a clock change on the phone cannot extend it. */
        val deadline: Long,
    ) {
        val remainingMs: Long get() = deadline - SystemClock.elapsedRealtime()
    }

    @Volatile var pending: Pending? = null
        private set

    fun ask(context: Context, payload: Map<String, Any?>): Map<String, Any?> {
        val now = System.currentTimeMillis() / 1000
        val request = Approval.parseRequest(payload, now)
        val desktop = Connect.desktop() ?: throw SecurityException("not paired")
        val lifetime = (request.expires - now).coerceIn(0, Approval.MAX_LIFETIME_SECONDS)
        val current = Pending(request, desktop.pin, desktop.name, SystemClock.elapsedRealtime() + lifetime * 1000)
        pending = current
        notify(context, current)
        return mapOf("error" to "needs-user")
    }

    private fun notify(context: Context, current: Pending) {
        RequestChannel.ensure(context)
        val open = PendingIntent.getActivity(
            context, ID_NOTIFICATION,
            Intent(context, ApprovalActivity::class.java)
                .putExtra(ApprovalActivity.EXTRA_REQUEST, current.request.request)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val title = context.getString(R.string.approval_notification_title, current.desktopName)
        // The lock screen shows only that a request is waiting, never what it is for.
        val public = Notifications.builder(context, RequestChannel.ID)
            .setContentTitle(context.getString(R.string.approval_notification_public))
            .build()
        Notifications.manager(context).notify(
            ID_NOTIFICATION,
            Notifications.builder(context, RequestChannel.ID)
                .setContentTitle(title)
                .setContentText(context.getString(R.string.approval_notification_text, current.request.reason, current.request.app))
                .setCategory(Notification.CATEGORY_REMINDER)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .setPublicVersion(public)
                .setContentIntent(open)
                .setAutoCancel(true)
                .setTimeoutAfter(current.remainingMs.coerceAtLeast(1))
                .build(),
        )
    }

    /** The waiting request if it is still current. */
    fun current(request: String?): Pending? = pending?.takeIf { it.request.request == request && it.remainingMs > 0 }

    fun finish(request: String) {
        if (pending?.request?.request == request) pending = null
    }
}
