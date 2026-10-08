package org.projectluma.connect.clipboard

import android.app.Activity
import android.content.ClipDescription
import android.content.ClipboardManager
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import org.projectluma.connect.R
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Capabilities
import org.projectluma.connect.service.ClipboardEcho

/**
 * Sends the phone's clipboard to the computer.
 *
 * Android only lets the focused app read the clipboard, so this invisible
 * activity waits until it actually holds window focus, reads once, and
 * closes. It is started by a person: the Quick Settings tile, the link
 * notification's button, or the app itself.
 */
class ClipboardSendActivity : Activity() {
    private var sent = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!Connect.paired) {
            Toast.makeText(this, R.string.not_paired, Toast.LENGTH_SHORT).show()
            finish()
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (!hasFocus || sent) return
        sent = true
        val name = Connect.desktop()?.name ?: ""
        val clipboard = getSystemService(ClipboardManager::class.java)
        val clip = clipboard.primaryClip
        val text = clip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString()
        if (text.isNullOrEmpty()) {
            Toast.makeText(this, R.string.clipboard_empty, Toast.LENGTH_SHORT).show()
            finish()
            return
        }
        val sensitive = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            clip.description.extras?.getBoolean(ClipDescription.EXTRA_IS_SENSITIVE) == true
        ClipboardEcho.remember(text)
        Connect.send(Capabilities.CLIPBOARD_WRITE, mapOf("text" to text.take(256 * 1024), "sensitive" to sensitive)) { result ->
            val ok = result.getOrNull()?.complete == true
            Toast.makeText(
                applicationContext,
                getString(if (ok) R.string.clipboard_sent else R.string.clipboard_failed, name),
                Toast.LENGTH_SHORT,
            ).show()
        }
        finish()
    }
}
