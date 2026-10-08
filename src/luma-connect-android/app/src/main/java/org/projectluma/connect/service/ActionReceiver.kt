package org.projectluma.connect.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.projectluma.connect.core.Connect
import org.projectluma.connect.media.CameraStreamService
import org.projectluma.connect.media.ScreenStreamService

class ActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            STOP_RING -> Ringer.stop(context)
            PAUSE -> {
                Connect.setLinkState(Connect.LinkState.Paused)
                LinkService.refresh(context)
            }
            RESUME -> {
                Connect.resume()
                LinkService.start(context)
            }
            STOP_CAMERA -> context.stopService(Intent(context, CameraStreamService::class.java))
            STOP_SCREEN -> context.stopService(Intent(context, ScreenStreamService::class.java))
        }
    }

    companion object {
        const val STOP_RING = "org.projectluma.connect.STOP_RING"
        const val PAUSE = "org.projectluma.connect.PAUSE"
        const val RESUME = "org.projectluma.connect.RESUME"
        const val STOP_CAMERA = "org.projectluma.connect.STOP_CAMERA"
        const val STOP_SCREEN = "org.projectluma.connect.STOP_SCREEN"
    }
}
