package org.projectluma.connect.share

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.Patterns
import android.widget.Toast
import org.projectluma.connect.R
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Capabilities

/** "Send to computer" in the share sheet: links open as a notification there, text goes to its clipboard, files to Downloads. */
class ShareActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val desktop = Connect.desktop()
        if (desktop == null) {
            Toast.makeText(this, R.string.not_paired, Toast.LENGTH_SHORT).show()
            finish()
            return
        }
        val streams = streams(intent)
        val text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString()?.trim()
        when {
            streams.isNotEmpty() -> FileSender.send(applicationContext, streams)
            text != null && Patterns.WEB_URL.matcher(text).matches() && (text.startsWith("http://") || text.startsWith("https://")) ->
                Connect.send(Capabilities.LINKS_OPEN, mapOf("url" to text.take(4096), "title" to intent.getStringExtra(Intent.EXTRA_SUBJECT).orEmpty().take(256))) { done(it.getOrNull()?.complete == true, desktop.name) }
            !text.isNullOrEmpty() ->
                Connect.send(Capabilities.CLIPBOARD_WRITE, mapOf("text" to text.take(256 * 1024), "sensitive" to false)) { done(it.getOrNull()?.complete == true, desktop.name) }
        }
        finish()
    }

    private fun done(ok: Boolean, name: String) {
        Toast.makeText(applicationContext, applicationContext.getString(if (ok) R.string.sent_files else R.string.send_failed, name), Toast.LENGTH_SHORT).show()
    }

    @Suppress("DEPRECATION")
    private fun streams(intent: Intent): List<Uri> = when (intent.action) {
        Intent.ACTION_SEND -> listOfNotNull(
            if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java) else intent.getParcelableExtra(Intent.EXTRA_STREAM),
        )
        Intent.ACTION_SEND_MULTIPLE ->
            (if (Build.VERSION.SDK_INT >= 33) intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java) else intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM)).orEmpty()
        else -> emptyList()
    }
}
