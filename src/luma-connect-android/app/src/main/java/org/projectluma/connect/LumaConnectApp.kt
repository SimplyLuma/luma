package org.projectluma.connect

import android.app.Application
import org.projectluma.connect.core.Connect
import org.projectluma.connect.service.Notifications

class LumaConnectApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Notifications.createChannels(this)
        Connect.init(this)
    }
}
