package org.projectluma.connect.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ComponentName
import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PersistableBundle
import android.provider.MediaStore
import android.provider.Settings
import org.projectluma.connect.R
import org.projectluma.connect.approval.ApprovalRequests
import org.projectluma.connect.core.Connect
import org.projectluma.connect.ime.LumaKeyboardService
import org.projectluma.connect.media.MediaRequests
import org.projectluma.connect.protocol.Adapter
import org.projectluma.connect.protocol.Capabilities
import org.projectluma.connect.protocol.Ids
import org.projectluma.connect.protocol.ProtocolException
import java.security.MessageDigest
import java.util.Base64

/**
 * Effects the paired computer may request on this phone. Every payload is
 * validated as strictly as the desktop validates the phone's payloads, and
 * each adapter re-checks that the user has not paused the feature.
 */
class PhoneAdapters(private val context: Context) {
    private val transfers = HashMap<String, Transfer>()

    private class Transfer(val uri: Uri, val name: String, val size: Long, var length: Long, val digest: MessageDigest)

    fun all(): Map<String, Adapter> = mapOf(
        Capabilities.DEVICE_RING to guarded(Capabilities.DEVICE_RING, ::ring),
        Capabilities.CLIPBOARD_WRITE to guarded(Capabilities.CLIPBOARD_WRITE, ::clipboard),
        Capabilities.LINKS_OPEN to guarded(Capabilities.LINKS_OPEN, ::link),
        Capabilities.FILES_WRITE to guarded(Capabilities.FILES_WRITE, ::file),
        "notifications.act" to guarded("notifications.act", ::notificationAction),
        Capabilities.MEDIA_CONTROL to guarded(Capabilities.MEDIA_CONTROL, ::media),
        Capabilities.CAMERA_STREAM to guarded(Capabilities.CAMERA_STREAM) { payload -> MediaRequests.camera(context, payload) },
        "screen.view" to guarded("screen.view") { payload -> MediaRequests.screen(context, payload) },
        Capabilities.DND_SET to guarded(Capabilities.DND_SET) { payload -> DndSync.apply(context, payload) },
        Capabilities.HOTSPOT_REQUEST to guarded(Capabilities.HOTSPOT_REQUEST, ::hotspot),
        Capabilities.AUTH_REQUEST to guarded(Capabilities.AUTH_REQUEST) { payload -> ApprovalRequests.ask(context, payload) },
        // Typing from the computer has its own switch, apart from using this phone as the computer's trackpad.
        Capabilities.INPUT_CONTROL to guarded(Capabilities.INPUT_CONTROL, LumaKeyboardService.PREFERENCE_TYPING) { payload ->
            LumaKeyboardService.control(payload)
        },
    )

    private fun guarded(capability: String, effect: (Map<String, Any?>) -> Map<String, Any?>): Adapter =
        guarded(capability, capability, effect)

    /** [preference] names the switch on this phone when one capability backs two features. */
    private fun guarded(capability: String, preference: String, effect: (Map<String, Any?>) -> Map<String, Any?>): Adapter = { _, payload ->
        if (!Connect.featureEnabled(preference) || Connect.linkState == Connect.LinkState.Paused) {
            throw SecurityException("paused on this phone")
        }
        effect(payload)
    }

    private fun desktopName() = Connect.desktop()?.name ?: "Your computer"

    private fun ring(payload: Map<String, Any?>): Map<String, Any?> {
        require(payload.keys == setOf("ring") && payload["ring"] is Boolean)
        if (payload["ring"] == true) Ringer.ring(context, desktopName()) else Ringer.stop(context)
        return mapOf("ringing" to Ringer.ringing)
    }

    private fun clipboard(payload: Map<String, Any?>): Map<String, Any?> {
        require(payload.keys.minus(setOf("text", "sensitive")).isEmpty())
        val text = payload["text"] as? String ?: throw ProtocolException("text required")
        require(text.length <= 256 * 1024)
        val sensitive = payload["sensitive"] ?: false
        require(sensitive is Boolean)
        val clip = ClipData.newPlainText("Luma Connect", text)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            clip.description.extras = PersistableBundle().apply {
                putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, sensitive)
                putBoolean(ClipDescription.EXTRA_IS_REMOTE_DEVICE, true)
            }
        }
        // Writing the clipboard is allowed from the background; only reading is restricted.
        ClipboardEcho.remember(text)
        context.getSystemService(ClipboardManager::class.java).setPrimaryClip(clip)
        return mapOf("accepted" to true)
    }

    private fun link(payload: Map<String, Any?>): Map<String, Any?> {
        require(payload.keys.minus(setOf("url", "title")).isEmpty())
        val url = payload["url"] as? String ?: throw ProtocolException("url required")
        val title = (payload["title"] as? String).orEmpty()
        require(url.length <= 4096 && title.length <= 256)
        val uri = Uri.parse(url)
        require(uri.scheme in setOf("http", "https") && !uri.host.isNullOrEmpty())
        val open = PendingIntent.getActivity(
            context, url.hashCode(), Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = Notifications.builder(context, Notifications.INCOMING)
            .setContentTitle(context.getString(R.string.link_from, desktopName()))
            .setContentText(title.ifEmpty { uri.host })
            .setSubText(uri.host)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        Notifications.manager(context).notify(Ids.token().hashCode(), notification)
        return mapOf("accepted" to true)
    }

    @Synchronized
    private fun file(payload: Map<String, Any?>): Map<String, Any?> {
        require(payload.keys == setOf("transfer", "name", "size", "offset", "data", "final", "sha256"))
        val transfer = payload["transfer"] as? String ?: throw ProtocolException("transfer")
        val name = payload["name"] as? String ?: throw ProtocolException("name")
        val size = payload["size"] as? Long ?: throw ProtocolException("size")
        val offset = payload["offset"] as? Long ?: throw ProtocolException("offset")
        val final = payload["final"] as? Boolean ?: throw ProtocolException("final")
        val sha256 = payload["sha256"]
        require(Ids.IDENTIFIER.matches(transfer))
        require(name.isNotEmpty() && name.length <= 128 && '/' !in name && name != "." && name != ".." && name.none { it.code < 0x20 })
        require(size in 0..(4L shl 30) && offset in 0..size)
        require(sha256 == null || (sha256 is String && Ids.DIGEST.matches(sha256)))
        val chunk = Base64.getDecoder().decode(payload["data"] as? String ?: throw ProtocolException("data"))
        require(chunk.size <= 512 * 1024 && offset + chunk.size <= size)

        val resolver = context.contentResolver
        var state = transfers[transfer]
        if (state == null) {
            require(offset == 0L)
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, name)
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: throw ProtocolException("storage unavailable")
            state = Transfer(uri, name, size, 0, MessageDigest.getInstance("SHA-256"))
            transfers[transfer] = state
        }
        require(state.name == name && state.size == size && state.length == offset)
        resolver.openOutputStream(state.uri, "wa")!!.use { it.write(chunk) }
        state.length += chunk.size
        state.digest.update(chunk)
        if (!final) return mapOf("received" to state.length)

        transfers.remove(transfer)
        val actual = state.digest.digest().joinToString("") { "%02x".format(it) }
        if (state.length != size || sha256 != actual) {
            resolver.delete(state.uri, null, null)
            throw ProtocolException("file failed verification")
        }
        resolver.update(state.uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
        // MediaStore may rename to avoid a collision ("photo (1).jpg"); report the name actually stored.
        val stored = resolver.query(state.uri, arrayOf(MediaStore.Downloads.DISPLAY_NAME), null, null, null)?.use {
            if (it.moveToFirst()) it.getString(0) else null
        } ?: name
        val type = resolver.getType(state.uri) ?: "*/*"
        val open = PendingIntent.getActivity(
            context, transfer.hashCode(),
            Intent(Intent.ACTION_VIEW).setDataAndType(state.uri, type)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE,
        )
        Notifications.manager(context).notify(
            transfer.hashCode(),
            Notifications.builder(context, Notifications.INCOMING)
                .setContentTitle(context.getString(R.string.file_from, desktopName(), stored))
                .setContentIntent(open)
                .setAutoCancel(true)
                .build(),
        )
        return mapOf("received" to size, "name" to stored)
    }

    /**
     * `hotspot.request` `{}`. Android has no API for an ordinary app to turn on the hotspot, so the
     * phone asks with a notification that opens hotspot settings. It does not report the network
     * name: reading it needs `WifiManager.getSoftApConfiguration`, a system API, and Luma Connect
     * does not ask for location access to guess it. The computer rescans instead.
     */
    private fun hotspot(payload: Map<String, Any?>): Map<String, Any?> {
        require(payload.isEmpty())
        RequestChannel.ensure(context)
        val open = PendingIntent.getActivity(
            context, ID_HOTSPOT, hotspotSettings(context).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE,
        )
        Notifications.manager(context).notify(
            ID_HOTSPOT,
            Notifications.builder(context, RequestChannel.ID)
                .setContentTitle(context.getString(R.string.hotspot_title, desktopName()))
                .setContentText(context.getString(R.string.hotspot_text))
                .setContentIntent(open)
                .setAutoCancel(true)
                .setTimeoutAfter(60_000)
                .build(),
        )
        return mapOf("error" to "needs-user")
    }

    private fun notificationAction(payload: Map<String, Any?>): Map<String, Any?> {
        require(payload.keys.minus(setOf("key", "action", "text", "operation")).isEmpty())
        val key = payload["key"] as? String ?: throw ProtocolException("key")
        val action = payload["action"] as? String ?: throw ProtocolException("action")
        val text = payload["text"] as? String
        require(Ids.IDENTIFIER.matches(key) && (action in setOf("dismiss", "open") || Ids.IDENTIFIER.matches(action)))
        require(text == null || text.length <= 4096)
        val service = NotificationMirrorService.instance ?: return mapOf("state" to "unknown")
        return mapOf("state" to if (service.act(key, action, text)) "complete" else "unknown")
    }

    private fun media(payload: Map<String, Any?>): Map<String, Any?> {
        require(payload.keys.minus(setOf("command", "position_ms")).isEmpty())
        val command = payload["command"] as? String ?: throw ProtocolException("command")
        require(command in setOf("play", "pause", "next", "previous", "seek"))
        val position = payload["position_ms"]
        require(position == null || (position is Long && position >= 0))
        val service = NotificationMirrorService.instance ?: throw SecurityException("notification access is off")
        service.media.control(command, position as Long?)
        return mapOf("accepted" to true)
    }
}

private const val ID_HOTSPOT = 42

/**
 * The most direct hotspot screen this phone's Settings app exports. There is no public action
 * constant. Checked against AOSP `packages/apps/Settings` AndroidManifest.xml (android16-release):
 * 1. `com.android.settings.WIFI_TETHER_SETTINGS` opens `Settings$WifiTetherSettingsActivity` (the hotspot page);
 * 2. `android.settings.TETHER_SETTINGS` (`Settings.ACTION_TETHER_SETTINGS`, a @SystemApi constant) opens
 *    `Settings$TetherSettingsActivity`;
 * 3. `com.android.settings/.TetherSettings`, an exported activity-alias kept "for compatibility with old shortcuts";
 * 4. the public `Settings.ACTION_WIRELESS_SETTINGS` as the last resort.
 * OEM Settings apps may export none of 1-3; the manifest's `<queries>` make them resolvable.
 */
fun hotspotSettings(context: Context): Intent {
    val candidates = listOf(
        Intent("com.android.settings.WIFI_TETHER_SETTINGS"),
        Intent("android.settings.TETHER_SETTINGS"),
        Intent().setComponent(ComponentName("com.android.settings", "com.android.settings.TetherSettings")),
    )
    val manager = context.packageManager
    return candidates.firstOrNull { intent ->
        manager.resolveActivity(intent, android.content.pm.PackageManager.MATCH_DEFAULT_ONLY)?.activityInfo?.exported == true
    } ?: Intent(Settings.ACTION_WIRELESS_SETTINGS)
}

/** High-importance channel for things the computer is waiting on: approvals and hotspot. */
object RequestChannel {
    const val ID = "requests"

    fun ensure(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(ID) != null) return
        manager.createNotificationChannel(NotificationChannel(ID, context.getString(R.string.channel_requests), NotificationManager.IMPORTANCE_HIGH))
    }
}

/** Prevents a clipboard value written by the computer from being sent straight back. */
object ClipboardEcho {
    @Volatile private var last: String? = null
    fun remember(text: String) { last = Connect.opaqueKey("clip", text) }
    fun isEcho(text: String) = last == Connect.opaqueKey("clip", text)
}
