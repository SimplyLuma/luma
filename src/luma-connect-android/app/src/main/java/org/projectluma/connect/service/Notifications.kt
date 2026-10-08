package org.projectluma.connect.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import org.projectluma.connect.R
import org.projectluma.connect.ui.MainActivity

object Notifications {
    const val LINK = "link"
    const val RING = "ring"
    const val INCOMING = "incoming"
    const val SHARING = "sharing"

    const val ID_LINK = 1
    const val ID_RING = 2
    const val ID_TRANSFER = 3
    const val ID_CAMERA = 4
    const val ID_SCREEN = 5
    const val ID_NEEDS_TAP = 6

    fun createChannels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannels(listOf(
            NotificationChannel(LINK, context.getString(R.string.channel_link), NotificationManager.IMPORTANCE_MIN).apply { setShowBadge(false) },
            // Ordinary apps cannot bypass Do Not Disturb; the alarm audio stream is what makes ringing audible.
            NotificationChannel(RING, context.getString(R.string.channel_ring), NotificationManager.IMPORTANCE_HIGH).apply { setSound(null, null) },
            NotificationChannel(INCOMING, context.getString(R.string.channel_incoming), NotificationManager.IMPORTANCE_DEFAULT),
            NotificationChannel(SHARING, context.getString(R.string.channel_sharing), NotificationManager.IMPORTANCE_HIGH).apply { setSound(null, null) },
        ))
    }

    fun builder(context: Context, channel: String): Notification.Builder =
        Notification.Builder(context, channel)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentIntent(openApp(context))

    fun openApp(context: Context): PendingIntent = PendingIntent.getActivity(
        context, 0, Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    fun action(context: Context, action: String, requestCode: Int, extras: Intent.() -> Unit = {}): PendingIntent =
        PendingIntent.getBroadcast(
            context, requestCode, Intent(context, ActionReceiver::class.java).setAction(action).apply(extras),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

    fun manager(context: Context): NotificationManager = context.getSystemService(NotificationManager::class.java)
}
