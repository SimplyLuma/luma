package org.projectluma.connect.control

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.os.Bundle
import android.util.DisplayMetrics
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Applies the computer's taps, swipes, navigation keys and text during an
 * active screen-sharing session. It reads no window content except to find
 * the focused text field when the computer types. It is off unless the
 * person turns it on in Android's accessibility settings, and it does
 * nothing while no session is running.
 */
class RemoteControlService : AccessibilityService() {
    private var downX = 0f
    private var downY = 0f
    private var downAt = 0L
    private var path: Path? = null

    override fun onServiceConnected() {
        instance = this
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        if (instance === this) instance = null
        return super.onUnbind(intent)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit
    override fun onInterrupt() = Unit

    /** [event] was validated by `MediaStream.validateControl`; coordinates are normalized to 0..10000. */
    fun apply(event: Map<String, Any?>, streamWidth: Int, streamHeight: Int) {
        when (event["type"]) {
            "touch" -> touch(event["action"] as String, (event["x"] as Long).toFloat() / 10_000f, (event["y"] as Long).toFloat() / 10_000f)
            "key" -> performGlobalAction(
                when (event["key"]) {
                    "back" -> GLOBAL_ACTION_BACK
                    "home" -> GLOBAL_ACTION_HOME
                    else -> GLOBAL_ACTION_RECENTS
                },
            )
            "text" -> type(event["text"] as String)
        }
    }

    private fun touch(action: String, fx: Float, fy: Float) {
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        getSystemService(WindowManager::class.java).defaultDisplay.getRealMetrics(metrics)
        val x = (fx * metrics.widthPixels).coerceIn(0f, metrics.widthPixels - 1f)
        val y = (fy * metrics.heightPixels).coerceIn(0f, metrics.heightPixels - 1f)
        val now = android.os.SystemClock.uptimeMillis()
        when (action) {
            "down" -> {
                downX = x; downY = y; downAt = now
                path = Path().apply { moveTo(x, y) }
            }
            "move" -> path?.lineTo(x, y)
            "up" -> {
                val stroke = path ?: return
                stroke.lineTo(x, y)
                path = null
                val duration = (now - downAt).coerceIn(1, 10_000)
                dispatchGesture(GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(stroke, 0, duration)).build(), null, null)
            }
        }
    }

    private fun type(text: String) {
        val focused = rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: return
        val existing = focused.text?.toString().orEmpty()
        focused.performAction(
            AccessibilityNodeInfo.ACTION_SET_TEXT,
            Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, existing + text) },
        )
    }

    companion object {
        @Volatile var instance: RemoteControlService? = null
            private set
    }
}
