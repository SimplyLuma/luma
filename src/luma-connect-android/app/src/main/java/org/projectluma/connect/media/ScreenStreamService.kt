package org.projectluma.connect.media

import android.app.Notification
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.DisplayMetrics
import android.view.WindowManager
import org.projectluma.connect.R
import org.projectluma.connect.control.RemoteControlService
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.MediaStream
import org.projectluma.connect.service.ActionReceiver
import org.projectluma.connect.service.Notifications

/**
 * Shares this phone's screen with the paired computer after Android's own
 * screen-capture consent. If the computer was granted `screen.control` and
 * the Luma Connect accessibility service is on, its taps and typing are
 * applied here. Android 15 stops capture when the phone locks.
 */
class ScreenStreamService : Service() {
    private var projection: MediaProjection? = null
    private var display: VirtualDisplay? = null
    private var encoder: VideoEncoder? = null
    private var stream: MediaStream? = null
    private val main = Handler(Looper.getMainLooper())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val name = Connect.desktop()?.name ?: ""
        startForeground(
            Notifications.ID_SCREEN,
            Notifications.builder(this, Notifications.SHARING)
                .setContentTitle(getString(R.string.screen_live, name))
                .setOngoing(true)
                .addAction(Notification.Action.Builder(null, getString(R.string.action_stop), Notifications.action(this, ActionReceiver.STOP_SCREEN, 50)).build())
                .build(),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION,
        )
        if (intent == null || projection != null) return START_NOT_STICKY
        val resultCode = intent.getIntExtra("resultCode", 0)
        @Suppress("DEPRECATION")
        val data = (if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra("data", Intent::class.java) else intent.getParcelableExtra("data"))
            ?: return stop()
        // The projection must be obtained after the mediaProjection foreground service has started.
        val granted = getSystemService(MediaProjectionManager::class.java).getMediaProjection(resultCode, data) ?: return stop()
        projection = granted
        granted.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() = main.post { stopSelf() }.let { }
        }, main)
        val session = intent.getStringExtra("session") ?: return stop()
        val port = intent.getIntExtra("port", 0)
        val maxSize = intent.getIntExtra("max_size", 1600)
        val fps = intent.getIntExtra("fps", 30)
        val bitrate = intent.getIntExtra("bitrate", 6_000_000)
        val codecs = intent.getStringArrayListExtra("codecs") ?: arrayListOf("h264")
        Connect.background.execute { runCatching { start(granted, session, port, maxSize, fps, bitrate, codecs) }.onFailure { main.post { stopSelf() } } }
        return START_NOT_STICKY
    }

    private fun start(granted: MediaProjection, session: String, port: Int, maxSize: Int, fps: Int, bitrate: Int, codecs: List<String>) {
        val choice = Codecs.choose(codecs) ?: throw IllegalStateException("no shared video codec")
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        getSystemService(WindowManager::class.java).defaultDisplay.getRealMetrics(metrics)
        val scale = minOf(1.0, maxSize.toDouble() / maxOf(metrics.widthPixels, metrics.heightPixels))
        val width = ((metrics.widthPixels * scale).toInt() / 2) * 2
        val height = ((metrics.heightPixels * scale).toInt() / 2) * 2
        val desktop = Connect.desktop() ?: throw IllegalStateException("not paired")
        val media = MediaStream.open(Connect.identity(), desktop, port, MediaStream.header(session, "screen", width, height, fps, 0, false, choice.codec))
        stream = media
        val video = VideoEncoder(width, height, fps, bitrate, media, choice).also { it.onStopped = { main.post { stopSelf() } } }
        encoder = video
        main.post {
            display = granted.createVirtualDisplay(
                "Luma Connect", width, height, metrics.densityDpi, DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                video.surface, null, main,
            )
        }
        val controlAllowed = "screen.control" in desktop.incoming && Connect.featureEnabled("screen.control")
        runCatching {
            media.readControl { event -> if (controlAllowed) RemoteControlService.instance?.apply(event, width, height) }
        }
        main.post { stopSelf() }
    }

    private fun stop(): Int {
        stopSelf()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        runCatching { display?.release() }
        encoder?.stop()
        stream?.close()
        runCatching { projection?.stop() }
        super.onDestroy()
    }

    companion object {
        fun intent(context: Context, request: MediaRequests.Request, resultCode: Int, data: Intent): Intent =
            Intent(context, ScreenStreamService::class.java)
                .putExtra("resultCode", resultCode)
                .putExtra("data", data)
                .putExtra("session", request.session)
                .putExtra("port", request.port)
                .putExtra("max_size", (request.options["max_size"] as Long).toInt())
                .putExtra("fps", (request.options["fps"] as Long).toInt())
                .putExtra("bitrate", (request.options["bitrate"] as Long).toInt())
                .putStringArrayListExtra("codecs", ArrayList(MediaRequests.codecs(request.options)))
    }
}
