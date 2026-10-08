package org.projectluma.connect.clipboard

import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Capabilities

class ClipboardTileService : TileService() {
    override fun onStartListening() {
        qsTile?.apply {
            state = if (Connect.canSend(Capabilities.CLIPBOARD_WRITE)) Tile.STATE_INACTIVE else Tile.STATE_UNAVAILABLE
            updateTile()
        }
    }

    @Suppress("DEPRECATION")
    override fun onClick() {
        val intent = Intent(this, ClipboardSendActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startActivityAndCollapse(PendingIntent.getActivity(this, 0, intent, PendingIntent.FLAG_IMMUTABLE))
        } else {
            startActivityAndCollapse(intent)
        }
    }
}
