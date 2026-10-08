package org.projectluma.connect.media

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.os.Bundle
import android.widget.Toast
import org.projectluma.connect.service.Notifications

/** Visible, user-started entry point for camera and screen sessions. */
class MediaConsentActivity : Activity() {
    private var request: MediaRequests.Request? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Notifications.manager(this).cancel(Notifications.ID_NEEDS_TAP)
        request = MediaRequests.take()
        val current = request
        if (current == null) {
            Toast.makeText(this, "That request expired. Try again from your computer.", Toast.LENGTH_LONG).show()
            finish()
            return
        }
        if (current.kind == "camera") {
            val needed = buildList {
                add(Manifest.permission.CAMERA)
                if (current.options["audio"] == true) add(Manifest.permission.RECORD_AUDIO)
            }.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
            if (needed.isEmpty()) startCamera(current) else requestPermissions(needed.toTypedArray(), 1)
        } else {
            val manager = getSystemService(MediaProjectionManager::class.java)
            // "See my phone" mirrors the whole display; Android 14+ otherwise defaults to picking a single app.
            val consent = if (android.os.Build.VERSION.SDK_INT >= 34) {
                manager.createScreenCaptureIntent(android.media.projection.MediaProjectionConfig.createConfigForDefaultDisplay())
            } else {
                manager.createScreenCaptureIntent()
            }
            @Suppress("DEPRECATION")
            startActivityForResult(consent, 2)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, results: IntArray) {
        val current = request ?: return finish()
        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) startCamera(current) else finish()
    }

    private fun startCamera(request: MediaRequests.Request) {
        startForegroundService(CameraStreamService.intent(this, request, audio = request.options["audio"] == true &&
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED))
        finish()
    }

    @Deprecated("Activity result API is unnecessary for a single system consent")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        val current = request
        if (requestCode == 2 && resultCode == RESULT_OK && data != null && current != null) {
            startForegroundService(ScreenStreamService.intent(this, current, resultCode, data))
        }
        finish()
    }
}
