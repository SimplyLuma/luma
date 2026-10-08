package org.projectluma.connect.ime

import android.content.ClipDescription
import android.content.ClipboardManager
import android.inputmethodservice.InputMethodService
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.KeyCharacterMap
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import org.projectluma.connect.R
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Capabilities
import org.projectluma.connect.protocol.ProtocolException
import org.projectluma.connect.service.ClipboardEcho
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Luma Keyboard: an optional input method whose value is the link, not typing on glass.
 *
 * 1. Clipboard to the computer. Android 10+ only lets the focused app or the default input
 *    method read the clipboard (developer.android.com/about/versions/10/privacy/changes#clipboard-data),
 *    so while Luma Keyboard is the selected keyboard it sends newly copied text to the paired
 *    computer, if the computer granted `clipboard.write` and the person left "Send what you copy"
 *    on. Text marked `ClipDescription.EXTRA_IS_SENSITIVE` (passwords, codes) is never sent
 *    automatically, and text that just arrived from the computer is not sent back ([ClipboardEcho]).
 * 2. Typing from the computer. `input.control` from the computer commits text and key events
 *    through the current `InputConnection`. When Luma Keyboard is not the active keyboard, or no
 *    text field is focused, the adapter answers `needs-user`.
 *
 * Google Play: an input method is an allowed app type, but its clipboard access and the fact that
 * the computer can type must be disclosed in the listing, the privacy policy and the Data safety
 * form. It never logs or stores keystrokes; text typed on the phone's screen is not sent anywhere.
 */
class LumaKeyboardService : InputMethodService() {
    private val main = Handler(Looper.getMainLooper())
    private var clipboard: ClipboardManager? = null
    private var label: TextView? = null
    @Volatile private var editing = false

    private val clipListener = ClipboardManager.OnPrimaryClipChangedListener { sendClipboard() }

    override fun onCreate() {
        super.onCreate()
        instance = this
        clipboard = getSystemService(ClipboardManager::class.java).also { it.addPrimaryClipChangedListener(clipListener) }
    }

    override fun onDestroy() {
        clipboard?.removePrimaryClipChangedListener(clipListener)
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun onCreateInputView(): View {
        val density = resources.displayMetrics.density
        fun dp(value: Int) = (value * density).toInt()
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), dp(6), dp(8), dp(6))
            minimumHeight = dp(52)
            setBackgroundColor(resolveColor(android.R.attr.colorBackground))
        }
        label = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setTextColor(resolveColor(android.R.attr.textColorPrimary))
            maxLines = 1
        }
        row.addView(label, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        fun button(icon: Int, description: Int, action: () -> Unit) = ImageButton(this).apply {
            setImageResource(icon)
            contentDescription = getString(description)
            setColorFilter(resolveColor(android.R.attr.textColorPrimary))
            background = null
            minimumWidth = dp(48)
            minimumHeight = dp(48)
            setOnClickListener { action() }
        }
        row.addView(button(R.drawable.ic_keyboard_switch, R.string.keyboard_choose) {
            getSystemService(InputMethodManager::class.java).showInputMethodPicker()
        })
        row.addView(button(R.drawable.ic_keyboard_globe, R.string.keyboard_next) {
            if (!switchToNextInputMethod(false)) getSystemService(InputMethodManager::class.java).showInputMethodPicker()
        })
        updateLabel()
        return row
    }

    private fun resolveColor(attribute: Int): Int {
        val value = TypedValue()
        theme.resolveAttribute(attribute, value, true)
        return if (value.resourceId != 0) getColor(value.resourceId) else value.data
    }

    private fun updateLabel() {
        val name = Connect.desktop()?.name
        label?.text = if (name != null) getString(R.string.keyboard_typing_from, name) else getString(R.string.not_paired)
    }

    override fun onStartInput(attribute: EditorInfo?, restarting: Boolean) {
        super.onStartInput(attribute, restarting)
        editing = attribute != null && attribute.inputType != InputType.TYPE_NULL
    }

    override fun onFinishInput() {
        editing = false
        super.onFinishInput()
    }

    override fun onStartInputView(info: EditorInfo?, restarting: Boolean) {
        super.onStartInputView(info, restarting)
        updateLabel()
    }

    private fun sendClipboard() {
        if (!Connect.canSend(Capabilities.CLIPBOARD_WRITE) || !Connect.featureEnabled(PREFERENCE_CLIPBOARD)) return
        val clip = runCatching { clipboard?.primaryClip }.getOrNull() ?: return
        if (clip.itemCount == 0) return
        val sensitive = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            clip.description.extras?.getBoolean(ClipDescription.EXTRA_IS_SENSITIVE) == true
        if (sensitive) return
        val text = clip.getItemAt(0).coerceToText(this)?.toString()
        if (text.isNullOrEmpty() || text.length > 256 * 1024 || ClipboardEcho.isEcho(text)) return
        ClipboardEcho.remember(text)
        Connect.send(Capabilities.CLIPBOARD_WRITE, mapOf("text" to text, "sensitive" to false))
    }

    /**
     * Main thread only. Returns false when there is nowhere to type.
     *
     * `InputConnection.commitText` and friends reach the app in order, but `sendKeyEvent` is queued
     * behind them as input, so mixing the two would reorder a batch (text typed after Backspace
     * would be deleted). A batch that needs real key events (arrows, shortcuts, function keys)
     * therefore types its text as key events too; any other batch uses editor calls only.
     */
    private fun apply(events: List<Event>): Boolean {
        val connection = currentInputConnection
        if (!editing || connection == null) return false
        keyMode = heldMeta != 0 || events.any { it is Event.Key && it.key !in EDITING && it.key.length != 1 }
        if (!keyMode) connection.beginBatchEdit()
        try {
            for (event in events) when (event) {
                is Event.Text -> if (keyMode) event.text.forEach { typeCharacter(it) } else connection.commitText(event.text, 1)
                is Event.Key -> key(event.key, event.pressed)
            }
        } finally {
            if (!keyMode) connection.endBatchEdit()
        }
        return true
    }

    private var heldMeta = 0
    private var keyMode = false

    private fun key(key: String, pressed: Boolean) {
        val connection = currentInputConnection ?: return
        MODIFIERS[key]?.let { (code, meta) ->
            heldMeta = if (pressed) heldMeta or meta else heldMeta and meta.inv()
            sendEvent(code, pressed)
            return
        }
        if (!keyMode) {
            // Editing keys through the editor, so they stay in order with committed text.
            if (!pressed) return
            when (key) {
                "backspace" -> if (connection.getSelectedText(0).isNullOrEmpty()) connection.deleteSurroundingTextInCodePoints(1, 0) else connection.commitText("", 1)
                "delete" -> if (connection.getSelectedText(0).isNullOrEmpty()) connection.deleteSurroundingTextInCodePoints(0, 1) else connection.commitText("", 1)
                "enter" -> enter()
                "space" -> connection.commitText(" ", 1)
                else -> connection.commitText(key, 1)
            }
            return
        }
        val code = KEYS[key]
        when {
            code != null -> sendEvent(code, pressed)
            pressed -> typeCharacter(key.single())
        }
    }

    private fun enter() {
        val connection = currentInputConnection ?: return
        val info = currentInputEditorInfo
        val action = info.imeOptions and EditorInfo.IME_MASK_ACTION
        val multiLine = info.inputType and InputType.TYPE_TEXT_FLAG_MULTI_LINE != 0
        if (!multiLine && (info.imeOptions and EditorInfo.IME_FLAG_NO_ENTER_ACTION) == 0 &&
            action != EditorInfo.IME_ACTION_NONE && action != EditorInfo.IME_ACTION_UNSPECIFIED
        ) {
            connection.performEditorAction(action)
        } else {
            connection.commitText("\n", 1)
        }
    }

    private fun typeCharacter(character: Char) {
        // Real key events, so shortcuts such as Ctrl+A reach the app and order is kept with other keys.
        val events = KeyCharacterMap.load(KeyCharacterMap.VIRTUAL_KEYBOARD).getEvents(charArrayOf(if (heldMeta != 0) character.lowercaseChar() else character))
        if (events == null) {
            currentInputConnection?.commitText(character.toString(), 1)
            return
        }
        if (heldMeta != 0) {
            val code = events.firstOrNull { it.action == KeyEvent.ACTION_DOWN && !KeyEvent.isModifierKey(it.keyCode) }?.keyCode ?: return
            sendEvent(code, true)
            sendEvent(code, false)
        } else {
            // The character map already includes any Shift presses the character needs.
            events.forEach { currentInputConnection?.sendKeyEvent(KeyEvent.changeTimeRepeat(it, SystemClock.uptimeMillis(), 0)) }
        }
    }

    private fun sendEvent(code: Int, pressed: Boolean) {
        val now = SystemClock.uptimeMillis()
        currentInputConnection?.sendKeyEvent(
            KeyEvent(now, now, if (pressed) KeyEvent.ACTION_DOWN else KeyEvent.ACTION_UP, code, 0, heldMeta),
        )
    }

    sealed interface Event {
        data class Text(val text: String) : Event
        data class Key(val key: String, val pressed: Boolean) : Event
    }

    companion object {
        /** Feature switch for automatic clipboard sending; shown as its own row in the app. */
        const val PREFERENCE_CLIPBOARD = "keyboard.clipboard"
        /** Feature switch for typing from the computer, separate from the phone-as-trackpad switch. */
        const val PREFERENCE_TYPING = "keyboard.typing"
        const val MAX_EVENTS = 64
        const val MAX_TEXT = 256
        const val MAX_BATCH_TEXT = 1024

        @Volatile var instance: LumaKeyboardService? = null
            private set

        private val MODIFIERS = mapOf(
            "shift" to (KeyEvent.KEYCODE_SHIFT_LEFT to (KeyEvent.META_SHIFT_ON or KeyEvent.META_SHIFT_LEFT_ON)),
            "control" to (KeyEvent.KEYCODE_CTRL_LEFT to (KeyEvent.META_CTRL_ON or KeyEvent.META_CTRL_LEFT_ON)),
            "alt" to (KeyEvent.KEYCODE_ALT_LEFT to (KeyEvent.META_ALT_ON or KeyEvent.META_ALT_LEFT_ON)),
            "super" to (KeyEvent.KEYCODE_META_LEFT to (KeyEvent.META_META_ON or KeyEvent.META_META_LEFT_ON)),
        )

        private val KEYS = mapOf(
            "enter" to KeyEvent.KEYCODE_ENTER, "backspace" to KeyEvent.KEYCODE_DEL, "tab" to KeyEvent.KEYCODE_TAB,
            "escape" to KeyEvent.KEYCODE_ESCAPE, "delete" to KeyEvent.KEYCODE_FORWARD_DEL, "insert" to KeyEvent.KEYCODE_INSERT,
            "space" to KeyEvent.KEYCODE_SPACE, "left" to KeyEvent.KEYCODE_DPAD_LEFT, "right" to KeyEvent.KEYCODE_DPAD_RIGHT,
            "up" to KeyEvent.KEYCODE_DPAD_UP, "down" to KeyEvent.KEYCODE_DPAD_DOWN, "home" to KeyEvent.KEYCODE_MOVE_HOME,
            "end" to KeyEvent.KEYCODE_MOVE_END, "page_up" to KeyEvent.KEYCODE_PAGE_UP, "page_down" to KeyEvent.KEYCODE_PAGE_DOWN,
            "caps_lock" to KeyEvent.KEYCODE_CAPS_LOCK, "menu" to KeyEvent.KEYCODE_MENU,
        ) + (1..12).associate { "f$it" to (KeyEvent.KEYCODE_F1 + it - 1) }

        private val NAMED = KEYS.keys + MODIFIERS.keys
        /** Keys that can be applied through the editor without a key event. */
        private val EDITING = setOf("backspace", "delete", "enter", "space")

        private fun printable(codePoint: Int) = when (Character.getType(codePoint).toByte()) {
            Character.CONTROL, Character.FORMAT, Character.SURROGATE, Character.PRIVATE_USE, Character.UNASSIGNED,
            Character.LINE_SEPARATOR, Character.PARAGRAPH_SEPARATOR -> false
            Character.SPACE_SEPARATOR -> codePoint == ' '.code
            else -> true
        }

        /** Validates `input.control` exactly as strictly as the desktop does; pointer events are not accepted by a phone. */
        fun parse(payload: Map<String, Any?>): List<Event> {
            if (payload.keys != setOf("events")) throw ProtocolException("invalid input")
            val raw = payload["events"] as? List<*> ?: throw ProtocolException("invalid input")
            if (raw.isEmpty() || raw.size > MAX_EVENTS) throw ProtocolException("invalid input")
            var total = 0
            return raw.map { item ->
                val event = item as? Map<*, *> ?: throw ProtocolException("invalid input event")
                when (event["type"]) {
                    "text" -> {
                        val text = event["text"] as? String
                        if (event.keys != setOf("type", "text") || text == null || text.isEmpty() || text.length > MAX_TEXT) {
                            throw ProtocolException("invalid text event")
                        }
                        if (text.codePoints().anyMatch { !printable(it) && it != '\n'.code && it != '\t'.code }) throw ProtocolException("invalid text event")
                        total += text.length
                        if (total > MAX_BATCH_TEXT) throw ProtocolException("invalid text event")
                        Event.Text(text)
                    }
                    "key" -> {
                        val key = event["key"] as? String
                        val pressed = event["pressed"] as? Boolean
                        if (event.keys != setOf("type", "key", "pressed") || key == null || pressed == null) throw ProtocolException("invalid key event")
                        val single = key.length == 1 && printable(key[0].code) && key != " "
                        if (key !in NAMED && !single) throw ProtocolException("invalid key event")
                        Event.Key(key, pressed)
                    }
                    else -> throw ProtocolException("invalid input event")
                }
            }
        }

        /** `input.control` receiver adapter, called on a listener thread. */
        fun control(payload: Map<String, Any?>): Map<String, Any?> {
            val events = parse(payload)
            val service = instance ?: return mapOf("error" to "needs-user")
            val done = CountDownLatch(1)
            var applied = false
            service.main.post {
                applied = runCatching { service.apply(events) }.getOrDefault(false)
                done.countDown()
            }
            if (!done.await(2, TimeUnit.SECONDS) || !applied) return mapOf("error" to "needs-user")
            return mapOf("accepted" to true)
        }
    }
}
