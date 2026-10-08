package org.projectluma.connect.media

import android.Manifest
import android.app.Notification
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.SurfaceTexture
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.util.Range
import android.util.Size
import org.projectluma.connect.R
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.MediaStream
import org.projectluma.connect.service.ActionReceiver
import org.projectluma.connect.service.Notifications
import java.util.concurrent.Executor

/**
 * The phone as a camera and microphone for the paired computer. It runs only
 * after a tap on this phone, shows a persistent "Camera is on" notification
 * with Stop, and ends when the computer closes the stream.
 */
class CameraStreamService : Service() {
    private var cameraThread: HandlerThread? = null
    private var camera: CameraDevice? = null
    private var captureSession: CameraCaptureSession? = null
    private var stream: MediaStream? = null
    private var encoder: VideoEncoder? = null
    private var audio: AudioEncoder? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val name = Connect.desktop()?.name ?: ""
        var types = ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
        val wantsAudio = intent?.getBooleanExtra("audio", false) == true &&
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (wantsAudio) types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        startForeground(Notifications.ID_CAMERA, liveNotification(name), types)
        if (intent == null || stream != null) return START_NOT_STICKY
        val session = intent.getStringExtra("session") ?: return stopNow()
        val port = intent.getIntExtra("port", 0)
        val facing = intent.getStringExtra("facing") ?: "back"
        val width = intent.getIntExtra("width", 1280)
        val height = intent.getIntExtra("height", 720)
        val fps = intent.getIntExtra("fps", 30)
        val bitrate = intent.getIntExtra("bitrate", 4_000_000)
        val codecs = intent.getStringArrayListExtra("codecs") ?: arrayListOf("h264")
        Connect.background.execute {
            runCatching { start(session, port, facing, width, height, fps, bitrate, wantsAudio, codecs) }.onFailure { stopNow() }
        }
        return START_NOT_STICKY
    }

    private fun liveNotification(name: String): Notification =
        Notifications.builder(this, Notifications.SHARING)
            .setContentTitle(getString(R.string.camera_live, name))
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, getString(R.string.action_stop), Notifications.action(this, ActionReceiver.STOP_CAMERA, 40)).build())
            .build()

    private fun start(session: String, port: Int, facing: String, width: Int, height: Int, fps: Int, bitrate: Int, withAudio: Boolean, codecs: List<String>) {
        val choice = Codecs.choose(codecs) ?: throw IllegalStateException("no shared video codec")
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) throw SecurityException("camera")
        val manager = getSystemService(CameraManager::class.java)
        val lensFacing = if (facing == "front") CameraCharacteristics.LENS_FACING_FRONT else CameraCharacteristics.LENS_FACING_BACK
        val id = manager.cameraIdList.firstOrNull { manager.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) == lensFacing }
            ?: manager.cameraIdList.first()
        val characteristics = manager.getCameraCharacteristics(id)
        val sizes = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)!!.getOutputSizes(SurfaceTexture::class.java)
        val size = chooseSize(sizes, width, height)
        val rotation = characteristics.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 0
        val desktop = Connect.desktop() ?: throw IllegalStateException("not paired")
        val media = MediaStream.open(Connect.identity(), desktop, port, MediaStream.header(session, "camera", size.width, size.height, fps, rotation, withAudio, choice.codec))
        stream = media
        val video = VideoEncoder(size.width, size.height, fps, bitrate, media, choice).also { it.onStopped = { stopNow() } }
        encoder = video
        if (withAudio && checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            audio = AudioEncoder.start(media)
        }
        val thread = HandlerThread("luma-connect-camera").apply { start() }
        cameraThread = thread
        val handler = Handler(thread.looper)
        val executor = Executor { handler.post(it) }
        manager.openCamera(id, executor, object : CameraDevice.StateCallback() {
            override fun onOpened(device: CameraDevice) {
                camera = device
                val request = device.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply {
                    addTarget(video.surface)
                    set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, Range(fps, fps))
                    set(CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE, CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE_ON)
                }
                device.createCaptureSession(SessionConfiguration(
                    SessionConfiguration.SESSION_REGULAR, listOf(OutputConfiguration(video.surface)), executor,
                    object : CameraCaptureSession.StateCallback() {
                        override fun onConfigured(capture: CameraCaptureSession) {
                            captureSession = capture
                            capture.setRepeatingRequest(request.build(), null, handler)
                        }
                        override fun onConfigureFailed(capture: CameraCaptureSession) = stopNow().let { }
                    },
                ))
            }
            override fun onDisconnected(device: CameraDevice) = stopNow().let { }
            override fun onError(device: CameraDevice, error: Int) = stopNow().let { }
        })
        // The desktop closes the stream to end the session.
        Connect.background.execute { runCatching { media.readControl { } }; stopNow() }
    }

    private fun chooseSize(sizes: Array<Size>, width: Int, height: Int): Size {
        val fitting = sizes.filter { it.width <= width && it.height <= height && it.width % 2 == 0 && it.height % 2 == 0 }
        return fitting.maxByOrNull { it.width * it.height } ?: sizes.minBy { it.width * it.height }
    }

    private fun stopNow(): Int {
        stopSelf()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        runCatching { captureSession?.close() }
        runCatching { camera?.close() }
        audio?.stop()
        encoder?.stop()
        stream?.close()
        cameraThread?.quitSafely()
        super.onDestroy()
    }

    companion object {
        fun intent(context: Context, request: MediaRequests.Request, audio: Boolean): Intent =
            Intent(context, CameraStreamService::class.java)
                .putExtra("session", request.session)
                .putExtra("port", request.port)
                .putExtra("facing", request.options["facing"] as String)
                .putExtra("width", (request.options["width"] as Long).toInt())
                .putExtra("height", (request.options["height"] as Long).toInt())
                .putExtra("fps", (request.options["fps"] as Long).toInt())
                .putExtra("bitrate", (request.options["bitrate"] as Long).toInt())
                .putStringArrayListExtra("codecs", ArrayList(MediaRequests.codecs(request.options)))
                .putExtra("audio", audio)
    }
}
