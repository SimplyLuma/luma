package org.projectluma.connect.media

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import org.projectluma.connect.R
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Ids
import org.projectluma.connect.protocol.ProtocolException
import org.projectluma.connect.service.Notifications

/**
 * The computer's requests for the camera or the screen. Android does not let
 * an app start the camera or screen capture from the background, so the
 * phone always asks the person with a notification and starts only on a tap.
 */
object MediaRequests {
    data class Request(val kind: String, val session: String, val port: Int, val options: Map<String, Any?>, val created: Long)

    @Volatile var pending: Request? = null
        private set

    fun camera(context: Context, payload: Map<String, Any?>): Map<String, Any?> {
        require(payload.keys.minus("codecs") == setOf("session", "port", "facing", "width", "height", "fps", "bitrate", "audio"))
        require(payload["facing"] in setOf("back", "front") && payload["audio"] is Boolean)
        validateCommon(payload, "width", "height")
        return ask(context, Request("camera", payload["session"] as String, (payload["port"] as Long).toInt(), payload, System.currentTimeMillis()))
    }

    fun screen(context: Context, payload: Map<String, Any?>): Map<String, Any?> {
        require(payload.keys.minus("codecs") == setOf("session", "port", "max_size", "fps", "bitrate"))
        validateCommon(payload, "max_size")
        return ask(context, Request("screen", payload["session"] as String, (payload["port"] as Long).toInt(), payload, System.currentTimeMillis()))
    }

    /** The computer's decodable codecs, most preferred first. Older computers send none and decode H.264. */
    fun codecs(options: Map<String, Any?>): List<String> =
        (options["codecs"] as? List<*>)?.map { it as String } ?: listOf("h264")

    private fun validateCommon(payload: Map<String, Any?>, vararg dimensions: String) {
        payload["codecs"]?.let { value ->
            val list = value as? List<*> ?: throw ProtocolException("codecs")
            require(list.isNotEmpty() && list.size <= 5 && list.all { it is String && it in Codecs.MIME } && list.toSet().size == list.size)
        }
        val session = payload["session"] as? String ?: throw ProtocolException("session")
        require(Ids.IDENTIFIER.matches(session))
        require((payload["port"] as? Long)?.let { it in 1024L..65535L } == true)
        require((payload["fps"] as? Long)?.let { it in 1L..60L } == true)
        require((payload["bitrate"] as? Long)?.let { it in 100_000L..40_000_000L } == true)
        dimensions.forEach { require((payload[it] as? Long)?.let { value -> value in 144L..4096L } == true) }
    }

    private fun ask(context: Context, request: Request): Map<String, Any?> {
        pending = request
        val name = Connect.desktop()?.name ?: ""
        val start = PendingIntent.getActivity(
            context, 30, Intent(context, MediaConsentActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val title = context.getString(if (request.kind == "camera") R.string.needs_tap_camera else R.string.needs_tap_screen, name)
        Notifications.manager(context).notify(
            Notifications.ID_NEEDS_TAP,
            Notifications.builder(context, Notifications.SHARING)
                .setContentTitle(title)
                .setContentText(context.getString(R.string.tap_to_start))
                .setContentIntent(start)
                .setAutoCancel(true)
                .setTimeoutAfter(30_000)
                .build(),
        )
        return mapOf("error" to "needs-user")
    }

    /** The desktop session expires 30 seconds after it was opened. */
    fun take(): Request? {
        val request = pending ?: return null
        pending = null
        return request.takeIf { System.currentTimeMillis() - it.created < 28_000 }
    }
}
