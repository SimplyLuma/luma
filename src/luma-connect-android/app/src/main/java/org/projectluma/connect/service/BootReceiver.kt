package org.projectluma.connect.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.projectluma.connect.core.Connect

/** Restores the link after a reboot or update. BOOT_COMPLETED may start a connectedDevice service. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (Connect.paired) LinkService.start(context)
    }
}
