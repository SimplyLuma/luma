package org.projectluma.connect.calls

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.bluetooth.BluetoothManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.SystemClock
import org.projectluma.connect.R
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Adapter
import org.projectluma.connect.protocol.Capabilities
import org.projectluma.connect.protocol.Ids
import org.projectluma.connect.protocol.ProtocolException
import org.projectluma.connect.service.Notifications

/**
 * `bluetooth.bond` from the computer: `{address: "AA:BB:CC:DD:EE:FF", name: str ≤128}`.
 *
 * The computer sends its own Bluetooth address because an Android app cannot read the
 * phone's (it reads as 02:00:00:00:00:00), and because only the phone may start pairing and
 * later connect hands-free: Android makes the most recently connected hands-free device its
 * active call device, so the computer never connects to the phone.
 *
 * Nothing pairs in the background. The adapter only posts "Use <computer> for calls" and
 * answers `{"error": "needs-user"}`; [BluetoothBondActivity] explains, asks for
 * `BLUETOOTH_CONNECT` if needed and calls `createBond()`. There is no phone-to-computer
 * capability for the outcome: the computer watches BlueZ for exactly one new bond during its
 * 120-second window.
 *
 * `BluetoothDevice.createBond()` is used rather than CompanionDeviceManager: the computer
 * already supplied the exact address, so there is nothing to choose from a scan, it needs only
 * the one runtime permission, and it leaves CompanionDeviceManager free for the planned BLE
 * presence association with the same computer.
 */
object BluetoothBondAdapter {
    const val ID_NOTIFICATION = 61
    const val CHANNEL = "calls"
    const val WINDOW_MS = 120_000L

    private val ADDRESS = Regex("[0-9A-F]{2}(:[0-9A-F]{2}){5}")
    private val UNUSABLE = setOf("00:00:00:00:00:00", "02:00:00:00:00:00", "FF:FF:FF:FF:FF:FF")

    data class Request(val address: String, val name: String)

    data class Pending(
        val request: Request,
        /** Single-use handle carried by the notification, so an old notification cannot pair a newer address. */
        val token: String,
        /** `SystemClock.elapsedRealtime()` deadline; a clock change cannot extend it. */
        val deadline: Long,
    ) {
        val remainingMs: Long get() = deadline - SystemClock.elapsedRealtime()
    }

    @Volatile var pending: Pending? = null
        private set

    /** Register as `Capabilities.BLUETOOTH_BOND to BluetoothBondAdapter.adapter(context)`. */
    fun adapter(context: Context): Adapter {
        val app = context.applicationContext
        return { _, payload -> request(app, payload) }
    }

    /** Strict payload check. Throws [ProtocolException] or [IllegalArgumentException] (`invalid-request`). */
    fun parse(payload: Map<String, Any?>): Request {
        if (payload.keys != setOf("address", "name")) throw ProtocolException("invalid bond request")
        val address = payload["address"] as? String ?: throw ProtocolException("address")
        val name = payload["name"] as? String ?: throw ProtocolException("name")
        require(ADDRESS.matches(address) && address !in UNUSABLE)
        require(name.isNotEmpty() && name.length <= 128 && name.none { it.code < 0x20 || it.code == 0x7f })
        return Request(address, name)
    }

    fun hasConnectPermission(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.S ||
            context.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED

    private fun request(context: Context, payload: Map<String, Any?>): Map<String, Any?> {
        // SecurityException is reported to the computer as `revoked`, like every paused feature.
        if (!Connect.featureEnabled(Capabilities.BLUETOOTH_BOND) || Connect.linkState == Connect.LinkState.Paused) {
            throw SecurityException("paused on this phone")
        }
        val request = parse(payload)
        context.getSystemService(BluetoothManager::class.java)?.adapter ?: return mapOf("error" to "unavailable")
        val current = Pending(request, Ids.token(), SystemClock.elapsedRealtime() + WINDOW_MS)
        pending = current
        notify(context, current)
        // Checked by the activity too; with or without BLUETOOTH_CONNECT a person must tap.
        return mapOf("error" to "needs-user")
    }

    private fun notify(context: Context, current: Pending) {
        val manager = context.getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL) == null) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL, context.getString(R.string.channel_calls), NotificationManager.IMPORTANCE_HIGH),
            )
        }
        val open = PendingIntent.getActivity(
            context, ID_NOTIFICATION,
            Intent(context, BluetoothBondActivity::class.java)
                .putExtra(BluetoothBondActivity.EXTRA_TOKEN, current.token)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val public = Notifications.builder(context, CHANNEL)
            .setContentTitle(context.getString(R.string.calls_bond_notification_public))
            .build()
        manager.notify(
            ID_NOTIFICATION,
            Notifications.builder(context, CHANNEL)
                .setContentTitle(context.getString(R.string.calls_bond_notification_title, current.request.name))
                .setContentText(context.getString(R.string.calls_bond_notification_text))
                .setCategory(Notification.CATEGORY_RECOMMENDATION)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .setPublicVersion(public)
                .setContentIntent(open)
                .setAutoCancel(true)
                .setTimeoutAfter(current.remainingMs.coerceAtLeast(1))
                .build(),
        )
    }

    /** The waiting request if [token] matches and it has not expired. */
    fun current(token: String?): Pending? = pending?.takeIf { token != null && it.token == token && it.remainingMs > 0 }

    fun finish(token: String) {
        if (pending?.token == token) pending = null
    }
}
