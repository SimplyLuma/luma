package org.projectluma.connect.share

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import org.projectluma.connect.R
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Capabilities
import org.projectluma.connect.protocol.FileOffer
import org.projectluma.connect.protocol.FileStreamSender
import org.projectluma.connect.protocol.Ids
import org.projectluma.connect.protocol.StreamFailedException
import org.projectluma.connect.protocol.Streams
import org.projectluma.connect.service.Notifications
import java.io.IOException
import java.security.MessageDigest
import java.util.Base64

/**
 * Sends shared files to the computer. The content URI grant from the share sheet is
 * read immediately; the Link service keeps the process alive.
 *
 * Files go on a `luma-files/1` stream: the file is hashed first (a second full read,
 * shown as indeterminate progress), offered once with `files.write` `{"stream": ...}`,
 * then sent as raw bytes. A dropped connection is resumed with a new offer for the same
 * transfer, up to three offers. A computer that predates streams answers
 * `invalid-request` or `unavailable`, and the file is sent in 512 KiB chunks instead.
 */
object FileSender {
    private const val CHUNK = 512 * 1024
    private const val ATTEMPTS = 3

    /** Pin of a computer that answered without stream support during this process's lifetime. */
    @Volatile private var chunkedOnly: String? = null

    fun send(context: Context, uris: List<Uri>) {
        val app = context.applicationContext
        val name = Connect.desktop()?.name ?: return
        Connect.background.execute {
            var sent = 0
            val notification = Notifications.builder(app, Notifications.INCOMING).setContentTitle(app.getString(R.string.sending_files, name)).setOnlyAlertOnce(true)
            for (uri in uris) {
                val ok = runCatching { sendOne(app, uri) { progress ->
                    val builder = if (progress < 0) notification.setProgress(0, 0, true) else notification.setProgress(1000, (progress * 1000).toInt(), false)
                    Notifications.manager(app).notify(Notifications.ID_TRANSFER, builder.build())
                } }.getOrDefault(false)
                if (ok) sent++
            }
            val done = Notifications.builder(app, Notifications.INCOMING)
                .setContentTitle(app.getString(if (sent == uris.size) R.string.sent_files else R.string.send_failed, name))
                .setAutoCancel(true)
                .build()
            Notifications.manager(app).notify(Notifications.ID_TRANSFER, done)
        }
    }

    /** [progress] receives 0..1, or a negative value while the file is being hashed. */
    private fun sendOne(context: Context, uri: Uri, progress: (Double) -> Unit): Boolean {
        val resolver = context.contentResolver
        var displayName = "shared-file"
        var size = -1L
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use {
            if (it.moveToFirst()) {
                displayName = it.getString(0) ?: displayName
                if (!it.isNull(1)) size = it.getLong(1)
            }
        }
        displayName = sanitize(displayName)
        if (size < 0) {
            // Providers that cannot report a size are measured first so the receiver can bound the transfer.
            size = resolver.openInputStream(uri)?.use { stream -> stream.skip(Long.MAX_VALUE) } ?: return false
        }
        if (size > Streams.MAX_FILE) return false
        val desktop = Connect.desktop() ?: return false
        if (chunkedOnly != desktop.pin) {
            when (sendStream(context, uri, displayName, size, progress)) {
                true -> return true
                false -> return false
                null -> chunkedOnly = desktop.pin
            }
        }
        return sendChunked(context, uri, displayName, size, progress)
    }

    /** Keeps at most 128 code points, without "/", controls or a split surrogate pair. */
    private fun sanitize(name: String): String {
        val cleaned = StringBuilder()
        var points = 0
        var index = 0
        while (index < name.length && points < 128) {
            val point = name.codePointAt(index)
            index += Character.charCount(point)
            if (point < 0x20 || point == 0x7f) continue
            cleaned.appendCodePoint(if (point == '/'.code) '_'.code else point)
            points++
        }
        return cleaned.toString().takeUnless { it.isEmpty() || it == "." || it == ".." } ?: "shared-file"
    }

    /** Returns true when sent, false when refused or failed, null when the computer has no stream support. */
    private fun sendStream(context: Context, uri: Uri, name: String, size: Long, progress: (Double) -> Unit): Boolean? {
        val resolver = context.contentResolver
        progress(-1.0)
        val digest = resolver.openInputStream(uri)?.use { Streams.sha256(it) } ?: return false
        val offer = FileOffer(Ids.token(), name, size, digest)
        repeat(ATTEMPTS) { attempt ->
            val receipt = Connect.callNow(Capabilities.FILES_WRITE, offer.payload(), timeoutMs = 30_000)
            if (Streams.shouldFallBack(receipt)) return null
            val session = runCatching { Streams.fileSession(receipt, size) }.getOrElse { return false }
            val desktop = Connect.desktop() ?: return false
            val host = desktop.host ?: return false
            try {
                val input = resolver.openInputStream(uri) ?: return false
                input.use {
                    FileStreamSender(Connect.identity(), desktop.pin, host).send(session, size, it) { sent, total ->
                        progress(if (total == 0L) 1.0 else sent.toDouble() / total)
                    }
                }
                return true
            } catch (_: StreamFailedException) {
                return false // the computer verified and refused the file; resending the same bytes will not help
            } catch (_: IOException) {
                if (attempt + 1 < ATTEMPTS) Thread.sleep(1_000L * (attempt + 1))
            }
        }
        return false
    }

    private fun sendChunked(context: Context, uri: Uri, displayName: String, size: Long, progress: (Double) -> Unit): Boolean {
        val resolver = context.contentResolver
        val transfer = Ids.token()
        val digest = MessageDigest.getInstance("SHA-256")
        val input = resolver.openInputStream(uri) ?: return false
        input.use { stream ->
            var offset = 0L
            val buffer = ByteArray(CHUNK)
            do {
                var filled = 0
                while (filled < CHUNK) {
                    val read = stream.read(buffer, filled, CHUNK - filled)
                    if (read < 0) break
                    filled += read
                }
                val chunk = buffer.copyOf(filled)
                digest.update(chunk)
                val final = offset + filled >= size
                if (offset + filled > size) return false
                val receipt = Connect.callNow(Capabilities.FILES_WRITE, mapOf(
                    "transfer" to transfer, "name" to displayName, "size" to size, "offset" to offset,
                    "data" to Base64.getEncoder().encodeToString(chunk), "final" to final,
                    "sha256" to if (final) digest.digest().joinToString("") { "%02x".format(it) } else null,
                ), timeoutMs = 30_000)
                if (!receipt.complete) return false
                offset += filled
                progress(if (size == 0L) 1.0 else offset.toDouble() / size)
            } while (!final)
        }
        return true
    }
}
