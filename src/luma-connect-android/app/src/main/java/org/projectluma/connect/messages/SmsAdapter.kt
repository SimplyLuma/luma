package org.projectluma.connect.messages

import android.Manifest
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.database.Cursor
import android.net.Uri
import android.os.Build
import android.provider.ContactsContract
import android.provider.Telephony
import android.telephony.PhoneNumberUtils
import android.telephony.SmsManager
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Adapter
import org.projectluma.connect.protocol.Ids
import org.projectluma.connect.protocol.ProtocolException

/**
 * `messages.read` and `messages.send` in the exact shape of the desktop's
 * `NativeMessages` (luma_continuity/native.py), so Prairie Messages on the
 * computer can use this phone as its message source.
 *
 * SMS only: MMS parts and RCS are not readable through public APIs. Google Play
 * allows SMS permissions only under a reviewed exception, so these permissions
 * are declared only in the `full` build; in the Play build [available] is false
 * and the adapters are not registered.
 */
class SmsAdapter(private val context: Context) {
    private val preferences = context.getSharedPreferences("luma-connect-sms", Context.MODE_PRIVATE)

    fun all(): Map<String, Adapter> = if (!available(context)) emptyMap() else mapOf(
        "messages.read" to { _, payload -> read(payload) },
        "messages.send" to { _, payload -> send(payload) },
    )

    private fun read(payload: Map<String, Any?>): Map<String, Any?> {
        requireGranted(Manifest.permission.READ_SMS)
        return when (payload.keys) {
            setOf("query", "limit") -> threads(payload["query"] as? String ?: throw ProtocolException("query"), limit(payload))
            setOf("address", "limit") -> messages(payload["address"] as? String ?: throw ProtocolException("address"), limit(payload))
            // Picture attachments are not available from the SMS provider.
            else -> throw ProtocolException("unsupported message query")
        }
    }

    private fun limit(payload: Map<String, Any?>): Int {
        val value = payload["limit"] as? Long ?: throw ProtocolException("limit")
        require(value in 1..100)
        return value.toInt()
    }

    private data class Row(val id: Long, val address: String, val body: String, val date: Long, val type: Int, val read: Boolean)

    private fun rows(selection: String?, args: Array<String>?, max: Int): List<Row> =
        context.contentResolver.query(
            Telephony.Sms.CONTENT_URI,
            arrayOf(Telephony.Sms._ID, Telephony.Sms.ADDRESS, Telephony.Sms.BODY, Telephony.Sms.DATE, Telephony.Sms.TYPE, Telephony.Sms.READ),
            selection, args, "${Telephony.Sms.DATE} DESC",
        )?.use { cursor -> buildList { while (size < max && cursor.moveToNext()) row(cursor)?.let(::add) } }.orEmpty()

    private fun row(cursor: Cursor): Row? {
        val address = cursor.getString(1)?.takeIf { it.isNotBlank() } ?: return null
        return Row(cursor.getLong(0), address, cursor.getString(2).orEmpty(), cursor.getLong(3), cursor.getInt(4), cursor.getInt(5) == 1)
    }

    /**
     * Newest conversations first, reading one latest message per conversation and looking up
     * contact names only for conversations that are returned or searched. A full-history scan
     * with a contact lookup per number took longer than the computer waits on a large inbox.
     */
    private fun threads(query: String, limit: Int): Map<String, Any?> {
        require(query.length <= 128)
        val names = HashMap<String, String>()
        val threads = ArrayList<Map<String, Any?>>()
        val seen = ArrayList<String>()
        for (thread in conversationIds() ?: return scannedThreads(query, limit)) {
            if (threads.size > limit) break
            val latest = rows("${Telephony.Sms.THREAD_ID} = ?", arrayOf(thread.toString()), 1).firstOrNull() ?: continue
            if (seen.any { sameNumber(it, latest.address) }) continue
            seen += latest.address
            val name = names.getOrPut(latest.address) { displayName(latest.address) }
            if (query.isNotEmpty() && !name.contains(query, true) && !latest.address.contains(query)) continue
            threads += thread(latest, name, unread(thread))
        }
        return mapOf("threads" to threads.take(limit), "truncated" to (threads.size > limit))
    }

    private fun thread(row: Row, name: String, unread: Int): Map<String, Any?> = mapOf(
        "address" to row.address, "display_name" to name.take(512), "preview" to row.body.take(512),
        "updated" to row.date / 1000, "unread" to unread.toLong(),
    )

    /** Conversation ids, newest first, or null when this phone's SMS provider has no conversations view. */
    private fun conversationIds(): List<Long>? = runCatching {
        context.contentResolver.query(CONVERSATIONS, arrayOf(Telephony.Sms.Conversations.THREAD_ID), null, null, "${Telephony.Sms.DATE} DESC")?.use { cursor ->
            buildList { while (size < MAX_CONVERSATIONS && cursor.moveToNext()) add(cursor.getLong(0)) }
        }
    }.getOrNull()

    private fun unread(thread: Long): Int = context.contentResolver.query(
        Telephony.Sms.CONTENT_URI, arrayOf(Telephony.Sms._ID),
        "${Telephony.Sms.THREAD_ID} = ? AND ${Telephony.Sms.TYPE} = ? AND ${Telephony.Sms.READ} = 0",
        arrayOf(thread.toString(), Telephony.Sms.MESSAGE_TYPE_INBOX.toString()), null,
    )?.use { it.count } ?: 0

    /** Fallback: groups recent messages by number. */
    private fun scannedThreads(query: String, limit: Int): Map<String, Any?> {
        val latest = LinkedHashMap<String, Row>()
        val unread = HashMap<String, Int>()
        for (row in rows(null, null, 2_000)) {
            val key = latest.keys.firstOrNull { sameNumber(it, row.address) } ?: row.address
            if (key !in latest) latest[key] = row
            if (row.type == Telephony.Sms.MESSAGE_TYPE_INBOX && !row.read) unread[key] = (unread[key] ?: 0) + 1
        }
        val threads = ArrayList<Map<String, Any?>>()
        for ((key, row) in latest) {
            if (threads.size > limit) break
            val name = displayName(row.address)
            if (query.isNotEmpty() && !name.contains(query, true) && !row.address.contains(query)) continue
            threads += thread(row, name, unread[key] ?: 0)
        }
        return mapOf("threads" to threads.take(limit), "truncated" to (threads.size > limit))
    }

    private fun messages(address: String, limit: Int): Map<String, Any?> {
        require(address.length in 1..64)
        val matching = messagesFor(address, limit).reversed()
        return mapOf("messages" to matching.map { row ->
            val outgoing = row.type != Telephony.Sms.MESSAGE_TYPE_INBOX
            mapOf(
                "uid" to (preferences.getString("sent.${row.id}", null) ?: "android-sms:${row.id}"),
                // The desktop canonicalized the address it asked for; answer in the same form.
                "address" to address,
                "body" to row.body.take(16_000),
                "timestamp" to row.date / 1000,
                "direction" to if (outgoing) "outgoing" else "incoming",
                "state" to when (row.type) {
                    Telephony.Sms.MESSAGE_TYPE_INBOX -> if (row.read) "read" else "received"
                    Telephony.Sms.MESSAGE_TYPE_SENT -> "sent"
                    Telephony.Sms.MESSAGE_TYPE_FAILED -> "failed"
                    else -> "queued"
                },
                "attachments" to emptyList<Any>(),
                "quote" to null,
            )
        })
    }

    /**
     * The newest messages with this number. The provider narrows by the number's last digits in
     * SQLite, so only candidate rows cross into this process; [sameNumber] decides. Transferring
     * thousands of recent rows took seconds per conversation on a real phone.
     */
    private fun messagesFor(address: String, limit: Int): List<Row> {
        val digits = address.filter(Char::isDigit)
        val (selection, args) = if (digits.length >= 4) {
            "${Telephony.Sms.ADDRESS} LIKE ?" to arrayOf("%" + digits.takeLast(4))
        } else {
            "${Telephony.Sms.ADDRESS} = ?" to arrayOf(address)
        }
        val candidates = context.contentResolver.query(
            Telephony.Sms.CONTENT_URI, arrayOf(Telephony.Sms._ID, Telephony.Sms.ADDRESS), selection, args, "${Telephony.Sms.DATE} DESC",
        )?.use { cursor -> buildList { while (cursor.moveToNext()) { val candidate = cursor.getString(1); if (candidate != null && sameNumber(candidate, address)) add(cursor.getLong(0)) } } }.orEmpty()
        if (candidates.isEmpty()) return emptyList()
        return candidates.take(limit).chunked(99).flatMap { ids ->
            rows("${Telephony.Sms._ID} IN (${ids.joinToString(",") { "?" }})", ids.map(Long::toString).toTypedArray(), ids.size)
        }.sortedByDescending { it.date }.take(limit)
    }

    /**
     * Sends through the phone's SIM. The receipt's uid is also reported for the
     * stored message once Android writes it, so the desktop reconciles instead of
     * importing a duplicate.
     */
    private fun send(payload: Map<String, Any?>): Map<String, Any?> {
        requireGranted(Manifest.permission.SEND_SMS)
        require(payload.keys == setOf("address", "body", "operation"))
        val address = payload["address"] as? String ?: throw ProtocolException("address")
        val body = payload["body"] as? String ?: throw ProtocolException("body")
        val operation = payload["operation"] as? String ?: throw ProtocolException("operation")
        require(Ids.IDENTIFIER.matches(operation) || operation.length in 1..128)
        require(address.length in 3..64 && address.all { it.isDigit() || it in "+*#() -" })
        require(body.isNotEmpty() && body.toByteArray().size <= 4096)
        val uid = "android-send:$operation"
        val manager = if (Build.VERSION.SDK_INT >= 31) context.getSystemService(SmsManager::class.java) else @Suppress("DEPRECATION") SmsManager.getDefault()
        val parts = manager.divideMessage(body)
        val action = "org.projectluma.connect.SMS_SENT.$operation"
        val sent = PendingIntent.getBroadcast(context, operation.hashCode(), Intent(action).setPackage(context.packageName), PendingIntent.FLAG_IMMUTABLE)
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(receiverContext: Context, intent: Intent) {
                runCatching { receiverContext.unregisterReceiver(this) }
                // Android records messages sent by an app that is not the default SMS app; link the newest match to this operation.
                Connect.background.execute { linkSent(address, body, uid) }
            }
        }
        if (Build.VERSION.SDK_INT >= 33) context.registerReceiver(receiver, IntentFilter(action), Context.RECEIVER_NOT_EXPORTED)
        else @Suppress("UnspecifiedRegisterReceiverFlag") context.registerReceiver(receiver, IntentFilter(action))
        if (parts.size == 1) {
            manager.sendTextMessage(address, null, body, sent, null)
        } else {
            manager.sendMultipartTextMessage(address, null, parts, ArrayList(List(parts.size) { index -> if (index == parts.lastIndex) sent else null }), null)
        }
        return mapOf("uid" to uid, "state" to "queued")
    }

    private fun linkSent(address: String, body: String, uid: String) {
        repeat(10) {
            val match = rows("${Telephony.Sms.TYPE} IN (?, ?, ?)", arrayOf(
                Telephony.Sms.MESSAGE_TYPE_SENT.toString(), Telephony.Sms.MESSAGE_TYPE_OUTBOX.toString(), Telephony.Sms.MESSAGE_TYPE_FAILED.toString(),
            ), 50).firstOrNull { it.body == body && sameNumber(it.address, address) && preferences.getString("sent.${it.id}", null) == null }
            if (match != null) {
                preferences.edit().putString("sent.${match.id}", uid).apply()
                return
            }
            Thread.sleep(500)
        }
    }

    /**
     * `areSamePhoneNumber` needs a country to compare national and international forms and
     * returns false for an empty one, so exact and digit-suffix matches come first.
     */
    private fun sameNumber(a: String, b: String): Boolean {
        if (a == b) return true
        val left = a.filter(Char::isDigit)
        val right = b.filter(Char::isDigit)
        if (left.isNotEmpty() && left == right) return true
        if (left.length >= 7 && right.length >= 7 && left.takeLast(9) == right.takeLast(9)) return true
        val country = context.getSystemService(android.telephony.TelephonyManager::class.java)?.networkCountryIso?.uppercase().orEmpty()
        return country.isNotEmpty() && PhoneNumberUtils.areSamePhoneNumber(a, b, country)
    }

    private fun displayName(address: String): String {
        if (context.checkSelfPermission(Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED) return address
        val uri = Uri.withAppendedPath(ContactsContract.PhoneLookup.CONTENT_FILTER_URI, Uri.encode(address))
        return runCatching {
            context.contentResolver.query(uri, arrayOf(ContactsContract.PhoneLookup.DISPLAY_NAME), null, null, null)?.use {
                if (it.moveToFirst()) it.getString(0) else null
            }
        }.getOrNull() ?: address
    }

    private fun requireGranted(permission: String) {
        if (context.checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) throw SecurityException("$permission not granted")
    }

    companion object {
        private val CONVERSATIONS: Uri = Uri.parse("content://sms/conversations")
        private const val MAX_CONVERSATIONS = 500

        /** Whether this build declares SMS access at all (the `full` build does; the Play build does not). */
        fun available(context: Context): Boolean = runCatching {
            @Suppress("DEPRECATION")
            context.packageManager.getPackageInfo(context.packageName, PackageManager.GET_PERMISSIONS)
                .requestedPermissions.orEmpty().contains(Manifest.permission.READ_SMS)
        }.getOrDefault(false)
    }
}
