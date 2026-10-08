package org.projectluma.connect.service

import android.app.Notification
import android.app.RemoteInput
import android.content.ComponentName
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.media.MediaMetadata
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import androidx.core.app.NotificationCompat
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Capabilities
import java.util.concurrent.ConcurrentHashMap

/**
 * Mirrors this phone's notifications and now-playing media to the paired
 * computer, and carries the computer's replies, actions and dismissals back.
 *
 * Only what a person would want on a larger screen is sent: ongoing and
 * foreground-service notifications, group summaries, media players (sent as
 * media state instead), Luma Connect's own notifications and apps the user
 * muted are skipped.
 */
class NotificationMirrorService : NotificationListenerService() {
    private data class Mirrored(val sbn: StatusBarNotification, val actions: Map<String, Int>)

    private val mirrored = ConcurrentHashMap<String, Mirrored>()
    private val lastSent = ConcurrentHashMap<String, Long>()
    lateinit var media: MediaMirror
        private set

    override fun onListenerConnected() {
        instance = this
        media = MediaMirror(this)
        media.start()
        if (!Connect.canSend(Capabilities.NOTIFICATIONS_MIRROR)) return
        Connect.send(Capabilities.NOTIFICATIONS_MIRROR, mapOf("op" to "clear"))
        runCatching { activeNotifications }.getOrNull()
            ?.sortedByDescending { it.postTime }
            ?.take(20)
            ?.reversed()
            ?.forEach { onNotificationPosted(it) }
    }

    override fun onListenerDisconnected() {
        if (instance === this) instance = null
        if (::media.isInitialized) media.stop()
        // The system can unbind a listener after an update or low memory; ask to be bound again.
        runCatching { requestRebind(ComponentName(this, NotificationMirrorService::class.java)) }
    }

    /** After the link to the computer comes back, re-send current notifications silently so both sides agree. */
    fun resync() {
        if (!Connect.canSend(Capabilities.NOTIFICATIONS_MIRROR)) return
        mirrored.clear()
        lastSent.clear()
        Connect.send(Capabilities.NOTIFICATIONS_MIRROR, mapOf("op" to "clear"))
        runCatching { activeNotifications }.getOrNull()?.sortedBy { it.postTime }?.takeLast(20)?.forEach { onNotificationPosted(it) }
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (!shouldMirror(sbn)) return
        val key = Connect.opaqueKey("notification", sbn.key)
        val now = System.currentTimeMillis()
        // Coalesce rapid updates (progress, typing indicators) to one per second per notification.
        val previous = lastSent[key]
        if (previous != null && now - previous < 1_000 && mirrored.containsKey(key)) return
        lastSent[key] = now

        val notification = sbn.notification
        val extras = notification.extras
        val actionIds = LinkedHashMap<String, Int>()
        val actions = notification.actions.orEmpty().withIndex()
            .filter { (_, action) -> action.actionIntent != null && !action.title.isNullOrBlank() }
            .take(4)
            .map { (index, action) ->
                val id = Connect.opaqueKey("action", sbn.key, index.toString())
                actionIds[id] = index
                mapOf(
                    "id" to id,
                    "label" to action.title.toString().take(128),
                    "reply" to (action.remoteInputs?.any { it.allowFreeFormInput } == true),
                )
            }
        mirrored[key] = Mirrored(sbn, actionIds)

        val conversation = runCatching {
            NotificationCompat.MessagingStyle.extractMessagingStyleFromNotification(notification)
        }.getOrNull()?.messages?.takeLast(10)?.map {
            mapOf(
                "sender" to (it.person?.name ?: it.sender ?: "").toString().take(128),
                "text" to (it.text ?: "").toString().take(4096),
                "when" to it.timestamp,
            )
        }
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val text = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT) ?: extras.getCharSequence(Notification.EXTRA_TEXT))?.toString().orEmpty()
        val ranking = Ranking().takeIf { currentRanking.getRanking(sbn.key, it) }
        val silent = ranking?.let { it.importance < android.app.NotificationManager.IMPORTANCE_DEFAULT } ?: false

        Connect.send(
            Capabilities.NOTIFICATIONS_MIRROR,
            mapOf(
                "op" to "post",
                "key" to key,
                "app" to appName(sbn).take(128),
                "package" to sbn.packageName.take(256),
                "title" to title.take(512),
                "text" to text.take(4096),
                "when" to notification.`when`.takeIf { it > 0 }.let { it ?: sbn.postTime },
                "silent" to silent,
                "actions" to actions,
                "conversation" to conversation,
            ),
        )
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification) {
        val key = Connect.opaqueKey("notification", sbn.key)
        if (mirrored.remove(key) == null) return
        lastSent.remove(key)
        if (Connect.canSend(Capabilities.NOTIFICATIONS_MIRROR)) {
            Connect.send(Capabilities.NOTIFICATIONS_MIRROR, mapOf("op" to "remove", "key" to key))
        }
    }

    private fun shouldMirror(sbn: StatusBarNotification): Boolean {
        if (!Connect.canSend(Capabilities.NOTIFICATIONS_MIRROR)) return false
        if (sbn.packageName == packageName || Connect.appMuted(sbn.packageName)) return false
        val notification = sbn.notification
        val flags = notification.flags
        val skipped = Notification.FLAG_ONGOING_EVENT or Notification.FLAG_FOREGROUND_SERVICE or Notification.FLAG_GROUP_SUMMARY or Notification.FLAG_LOCAL_ONLY
        if (flags and skipped != 0) return false
        // Known system reposts that are not useful on a computer (low-battery warning, Samsung media activity).
        if (sbn.packageName == "com.android.systemui" && sbn.tag?.contains("low_battery") == true) return false
        if (sbn.tag == "MediaOngoingActivity") return false
        if (notification.extras.getString(Notification.EXTRA_TEMPLATE) == Notification.MediaStyle::class.java.name) return false
        if (notification.category == Notification.CATEGORY_TRANSPORT || notification.category == Notification.CATEGORY_PROGRESS) return false
        val extras = notification.extras
        return !extras.getCharSequence(Notification.EXTRA_TITLE).isNullOrBlank() || !extras.getCharSequence(Notification.EXTRA_TEXT).isNullOrBlank()
    }

    @Suppress("DEPRECATION")
    private fun appName(sbn: StatusBarNotification): String {
        // The system attaches ApplicationInfo to every notification, so no package-visibility query is needed.
        val info = sbn.notification.extras.getParcelable<ApplicationInfo>("android.appInfo")
        return info?.let { packageManager.getApplicationLabel(it).toString() } ?: sbn.packageName
    }

    /** Performs the computer's choice on the live notification. Returns false when it no longer exists. */
    fun act(key: String, action: String, text: String?): Boolean {
        val entry = mirrored[key] ?: return false
        val sbn = entry.sbn
        return runCatching {
            when (action) {
                "dismiss" -> cancelNotification(sbn.key)
                "open" -> sbn.notification.contentIntent?.send() ?: return false
                else -> {
                    val index = entry.actions[action] ?: return false
                    val target = sbn.notification.actions[index]
                    val inputs = target.remoteInputs?.filter { it.allowFreeFormInput }.orEmpty()
                    if (inputs.isNotEmpty()) {
                        val reply = text ?: return false
                        val intent = Intent()
                        val results = Bundle().apply { inputs.forEach { putCharSequence(it.resultKey, reply) } }
                        RemoteInput.addResultsToIntent(inputs.toTypedArray(), intent, results)
                        target.actionIntent.send(this, 0, intent)
                    } else {
                        target.actionIntent.send()
                    }
                }
            }
            true
        }.getOrDefault(false)
    }

    companion object {
        @Volatile var instance: NotificationMirrorService? = null
            private set
    }
}

/** Now-playing state for the computer's media controls, from sessions visible to the notification listener. */
class MediaMirror(private val service: NotificationListenerService) {
    private val main = Handler(Looper.getMainLooper())
    private val manager = service.getSystemService(MediaSessionManager::class.java)
    private val component = ComponentName(service, NotificationMirrorService::class.java)
    private var controller: MediaController? = null
    private var lastPayload: Map<String, Any?>? = null

    private val callback = object : MediaController.Callback() {
        override fun onMetadataChanged(metadata: MediaMetadata?) = schedule()
        override fun onPlaybackStateChanged(state: PlaybackState?) = schedule()
        override fun onSessionDestroyed() = sessionsChanged(runCatching { manager.getActiveSessions(component) }.getOrDefault(emptyList()))
    }
    private val sessionsListener = MediaSessionManager.OnActiveSessionsChangedListener { sessionsChanged(it.orEmpty()) }
    private val publish = Runnable { send() }

    fun start() {
        runCatching {
            manager.addOnActiveSessionsChangedListener(sessionsListener, component, main)
            sessionsChanged(manager.getActiveSessions(component))
        }
    }

    fun stop() {
        runCatching { manager.removeOnActiveSessionsChangedListener(sessionsListener) }
        controller?.unregisterCallback(callback)
        controller = null
    }

    private fun sessionsChanged(sessions: List<MediaController>) {
        val next = sessions.firstOrNull { it.playbackState?.state == PlaybackState.STATE_PLAYING } ?: sessions.firstOrNull()
        if (next?.sessionToken == controller?.sessionToken) return
        controller?.unregisterCallback(callback)
        controller = next
        next?.registerCallback(callback, main)
        schedule()
    }

    private fun schedule() {
        main.removeCallbacks(publish)
        main.postDelayed(publish, 400)
    }

    private fun send() {
        if (!Connect.canSend(Capabilities.MEDIA_MIRROR)) return
        val current = controller
        val metadata = current?.metadata
        val state = current?.playbackState
        val actions = state?.actions ?: 0L
        val payload = mapOf(
            "state" to when (state?.state) {
                PlaybackState.STATE_PLAYING, PlaybackState.STATE_BUFFERING -> "playing"
                PlaybackState.STATE_PAUSED -> "paused"
                PlaybackState.STATE_STOPPED -> "stopped"
                else -> "none"
            },
            "title" to metadata?.getString(MediaMetadata.METADATA_KEY_TITLE).orEmpty().take(512),
            "artist" to metadata?.getString(MediaMetadata.METADATA_KEY_ARTIST).orEmpty().take(512),
            "album" to metadata?.getString(MediaMetadata.METADATA_KEY_ALBUM).orEmpty().take(512),
            "app" to (current?.packageName?.let { packageLabel(it) } ?: "").take(128),
            "duration_ms" to metadata?.getLong(MediaMetadata.METADATA_KEY_DURATION)?.takeIf { it > 0 },
            "position_ms" to state?.position?.takeIf { it >= 0 },
            "actions" to buildList {
                if (actions and PlaybackState.ACTION_PLAY != 0L) add("play")
                if (actions and PlaybackState.ACTION_PAUSE != 0L) add("pause")
                if (actions and PlaybackState.ACTION_SKIP_TO_NEXT != 0L) add("next")
                if (actions and PlaybackState.ACTION_SKIP_TO_PREVIOUS != 0L) add("previous")
                if (actions and PlaybackState.ACTION_SEEK_TO != 0L) add("seek")
            },
        )
        // Position advances continuously; only structural changes are worth a message.
        if (payload.minus("position_ms") == lastPayload?.minus("position_ms")) return
        lastPayload = payload
        Connect.send(Capabilities.MEDIA_MIRROR, payload)
    }

    private fun packageLabel(packageName: String): String = runCatching {
        service.packageManager.getApplicationLabel(service.packageManager.getApplicationInfo(packageName, 0)).toString()
    }.getOrDefault(packageName)

    fun control(command: String, position: Long?) {
        val controls = controller?.transportControls ?: throw IllegalStateException("nothing is playing")
        main.post {
            when (command) {
                "play" -> controls.play()
                "pause" -> controls.pause()
                "next" -> controls.skipToNext()
                "previous" -> controls.skipToPrevious()
                "seek" -> controls.seekTo(position ?: 0)
            }
        }
    }
}
