package org.projectluma.connect.service

import android.app.PendingIntent
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.MediaStore
import org.projectluma.connect.R
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Adapter
import org.projectluma.connect.protocol.Capabilities
import org.projectluma.connect.protocol.FileOffer
import org.projectluma.connect.protocol.FileStreamReceiving
import org.projectluma.connect.protocol.Json
import org.projectluma.connect.protocol.PartialFile
import org.projectluma.connect.protocol.PartialStore
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream

/**
 * The phone's `luma-files/1` receiver (contract section 5b).
 *
 * Merged over [PhoneAdapters] in `LinkService.startListener()`: `files.write` payloads
 * with a `stream` key open a one-shot listener; every other payload goes to this
 * instance's own [PhoneAdapters] chunked adapter unchanged, so its in-memory chunk
 * state stays in one place.
 *
 * Partial files are MediaStore Downloads rows with `IS_PENDING=1` plus a small record in
 * no-backup storage, so a transfer resumes after the process or the phone restarts.
 * Android deletes pending rows after seven days; records older than that are removed too.
 */
class StreamAdapters(private val context: Context) {
    private val chunked: Adapter = PhoneAdapters(context).all().getValue(Capabilities.FILES_WRITE)
    private val receiving by lazy {
        FileStreamReceiving(
            Connect.identity(),
            MediaStorePartials(context),
            authorized = { pin -> Connect.desktop()?.pin == pin && Connect.canReceive(Capabilities.FILES_WRITE) },
        )
    }

    fun all(): Map<String, Adapter> = mapOf(
        Capabilities.FILES_WRITE to { capability, payload ->
            if ("stream" !in payload) {
                chunked(capability, payload)
            } else {
                if (!Connect.featureEnabled(Capabilities.FILES_WRITE) || Connect.linkState == Connect.LinkState.Paused) {
                    throw SecurityException("paused on this phone")
                }
                // The app keeps one active computer, and the listener admitted only approved pins.
                val desktop = Connect.desktop() ?: throw SecurityException("not paired")
                receiving.offer(desktop.pin, payload)
            }
        },
    )
}

private class MediaStorePartials(private val context: Context) : PartialStore {
    private val records = File(context.noBackupFilesDir, "transfers")

    @Synchronized
    override fun open(peerPin: String, offer: FileOffer): PartialFile {
        records.mkdirs()
        removeStale()
        val resolver = context.contentResolver
        val file = File(records, "${offer.transfer}.json")
        val expected = mapOf("peer" to peerPin) + offer.fields()
        val stored = runCatching { Json.parseObject(file.readBytes()) }.getOrNull()
        var uri = (stored?.get("uri") as? String)?.let(Uri::parse)
        val matches = stored != null && stored - setOf("uri", "created") == expected && uri != null &&
            runCatching { resolver.openFileDescriptor(uri!!, "r")?.use { true } ?: false }.getOrDefault(false)
        if (!matches) {
            uri?.let { runCatching { resolver.delete(it, null, null) } }
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, offer.name)
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: throw IOException("storage unavailable")
            write(file, expected + mapOf("uri" to uri.toString(), "created" to System.currentTimeMillis()))
        }
        return Partial(uri!!, file, offer)
    }

    private fun write(file: File, record: Map<String, Any?>) {
        val temporary = File(records, file.name + ".tmp")
        FileOutputStream(temporary).use { stream ->
            stream.write(Json.encode(record))
            stream.fd.sync()
        }
        if (!temporary.renameTo(file)) throw IOException("could not store transfer record")
    }

    private fun removeStale() {
        val limit = System.currentTimeMillis() - 7L * 24 * 60 * 60 * 1000
        records.listFiles()?.forEach { file ->
            if (file.lastModified() >= limit) return@forEach
            runCatching {
                (Json.parseObject(file.readBytes())["uri"] as? String)?.let { context.contentResolver.delete(Uri.parse(it), null, null) }
            }
            file.delete()
        }
    }

    private inner class Partial(private val uri: Uri, private val record: File, private val offer: FileOffer) : PartialFile {
        private val resolver get() = context.contentResolver

        override fun length(): Long =
            runCatching { resolver.openFileDescriptor(uri, "r")?.use { it.statSize.coerceAtLeast(0) } }.getOrNull() ?: 0L

        override fun openRead(): InputStream = resolver.openInputStream(uri) ?: throw IOException("storage unavailable")

        override fun openWrite(offset: Long): OutputStream {
            val descriptor: ParcelFileDescriptor = resolver.openFileDescriptor(uri, "rw") ?: throw IOException("storage unavailable")
            val stream = FileOutputStream(descriptor.fileDescriptor)
            try {
                stream.channel.truncate(offset)
                stream.channel.position(offset)
            } catch (error: IOException) {
                runCatching { descriptor.close() }
                throw error
            }
            record.setLastModified(System.currentTimeMillis())
            return object : OutputStream() {
                override fun write(b: Int) = stream.write(b)
                override fun write(b: ByteArray, off: Int, len: Int) = stream.write(b, off, len)
                override fun flush() = stream.flush()
                override fun close() {
                    try {
                        stream.flush()
                        stream.fd.sync()
                    } finally {
                        descriptor.close()
                    }
                }
            }
        }

        override fun commit(): String {
            resolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
            record.delete()
            // MediaStore may rename to avoid a collision ("photo (1).jpg"); report the name actually stored.
            val stored = resolver.query(uri, arrayOf(MediaStore.Downloads.DISPLAY_NAME), null, null, null)?.use {
                if (it.moveToFirst()) it.getString(0) else null
            } ?: offer.name
            val type = resolver.getType(uri) ?: "*/*"
            val open = PendingIntent.getActivity(
                context, offer.transfer.hashCode(),
                Intent(Intent.ACTION_VIEW).setDataAndType(uri, type)
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE,
            )
            val desktop = Connect.desktop()?.name ?: "Your computer"
            Notifications.manager(context).notify(
                offer.transfer.hashCode(),
                Notifications.builder(context, Notifications.INCOMING)
                    .setContentTitle(context.getString(R.string.file_from, desktop, stored))
                    .setContentIntent(open)
                    .setAutoCancel(true)
                    .build(),
            )
            return stored
        }

        override fun discard() {
            runCatching { resolver.delete(uri, null, null) }
            record.delete()
        }
    }
}
