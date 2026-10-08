package org.projectluma.connect.service

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import org.projectluma.connect.R

/** "Find my phone": rings on the alarm stream so silent mode does not hide it, for at most one minute. */
object Ringer {
    private val main = Handler(Looper.getMainLooper())
    private var player: MediaPlayer? = null
    private var restoreVolume: Int? = null

    @Synchronized
    fun ring(context: Context, from: String) {
        if (player != null) return
        val audio = context.getSystemService(AudioManager::class.java)
        restoreVolume = audio.getStreamVolume(AudioManager.STREAM_ALARM)
        runCatching { audio.setStreamVolume(AudioManager.STREAM_ALARM, audio.getStreamMaxVolume(AudioManager.STREAM_ALARM), 0) }
        val uri = RingtoneManager.getActualDefaultRingtoneUri(context, RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
        player = MediaPlayer().apply {
            setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .setFlags(AudioAttributes.FLAG_AUDIBILITY_ENFORCED)
                    .build(),
            )
            setDataSource(context, uri)
            isLooping = true
            setWakeMode(context, android.os.PowerManager.SCREEN_DIM_WAKE_LOCK or android.os.PowerManager.ACQUIRE_CAUSES_WAKEUP)
            setOnPreparedListener { it.start() }
            prepareAsync()
        }
        context.getSystemService(Vibrator::class.java)?.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 600, 400), 0))
        val notification = Notifications.builder(context, Notifications.RING)
            .setContentTitle(context.getString(R.string.ring_title, from))
            .setCategory(android.app.Notification.CATEGORY_ALARM)
            .setOngoing(true)
            .setDeleteIntent(Notifications.action(context, ActionReceiver.STOP_RING, 10))
            .addAction(android.app.Notification.Action.Builder(null, context.getString(R.string.ring_stop), Notifications.action(context, ActionReceiver.STOP_RING, 11)).build())
            .build()
        Notifications.manager(context).notify(Notifications.ID_RING, notification)
        main.postDelayed({ stop(context) }, 60_000)
    }

    @Synchronized
    fun stop(context: Context) {
        player?.runCatching { stop(); release() }
        player = null
        context.getSystemService(Vibrator::class.java)?.cancel()
        restoreVolume?.let { volume ->
            runCatching { context.getSystemService(AudioManager::class.java).setStreamVolume(AudioManager.STREAM_ALARM, volume, 0) }
        }
        restoreVolume = null
        Notifications.manager(context).cancel(Notifications.ID_RING)
        main.removeCallbacksAndMessages(null)
    }

    val ringing: Boolean @Synchronized get() = player != null
}
