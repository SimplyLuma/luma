package org.projectluma.connect.protocol

/**
 * Capability names, always read from the receiver's side: a grant of
 * `clipboard.write` lets the caller write the receiver's clipboard.
 *
 * The first block matches `policy.CAPABILITIES` in the daemon. The companion
 * block is added by ADR-021 and must stay identical in `policy.py`.
 */
object Capabilities {
    val NATIVE = setOf(
        "messages.read", "messages.send", "notifications.read", "notifications.act",
        "contacts.read", "calls.read", "calls.control", "calls.audio",
        "screen.view", "screen.control",
    )

    /** Caller publishes its own battery, charging, signal and name to the receiver. */
    const val DEVICE_STATUS = "device.status"
    /** Caller asks the receiver to ring loudly until stopped. */
    const val DEVICE_RING = "device.ring"
    /** Caller writes the receiver's clipboard. */
    const val CLIPBOARD_WRITE = "clipboard.write"
    /** Caller reads the receiver's current clipboard (desktop only; Android forbids background reads). */
    const val CLIPBOARD_READ = "clipboard.read"
    /** Caller offers a link that the receiver shows as a tap-to-open notification. */
    const val LINKS_OPEN = "links.open"
    /** Caller delivers a file to the receiver's Downloads in bounded chunks. */
    const val FILES_WRITE = "files.write"
    /** Caller mirrors its notifications (post, update, remove) onto the receiver. */
    const val NOTIFICATIONS_MIRROR = "notifications.mirror"
    /** Caller mirrors its now-playing media state onto the receiver. */
    const val MEDIA_MIRROR = "media.mirror"
    /** Caller controls the receiver's active media session. */
    const val MEDIA_CONTROL = "media.control"
    /** Caller injects pointer and keyboard input into the receiver (phone as trackpad). */
    const val INPUT_CONTROL = "input.control"
    /** Caller asks the receiver to offer its camera and microphone as a stream. */
    const val CAMERA_STREAM = "camera.stream"

    /** Caller replaces its own device certificate over the existing mutual TLS link, keeping epoch and grants. */
    const val DEVICE_ROTATE = "device.rotate"
    /** Caller turns the receiver's Do Not Disturb (on Android 15+, a Luma Connect mode) on or off. */
    const val DND_SET = "dnd.set"
    /** Caller asks the receiver to offer its mobile hotspot with one tap. */
    const val HOTSPOT_REQUEST = "hotspot.request"
    /** Caller asks the receiver's person to approve something with the phone's biometric prompt. */
    const val AUTH_REQUEST = "auth.request"
    /** Caller delivers the signed answer to an earlier `auth.request`. */
    const val AUTH_RESPONSE = "auth.response"
    /** Caller gives its Bluetooth address and asks the receiver's person to bond for hands-free calls. */
    const val BLUETOOTH_BOND = "bluetooth.bond"

    val COMPANION = setOf(
        DEVICE_STATUS, DEVICE_RING, CLIPBOARD_WRITE, CLIPBOARD_READ, LINKS_OPEN, FILES_WRITE,
        NOTIFICATIONS_MIRROR, MEDIA_MIRROR, MEDIA_CONTROL, INPUT_CONTROL, CAMERA_STREAM,
        DEVICE_ROTATE, DND_SET, HOTSPOT_REQUEST, AUTH_REQUEST, AUTH_RESPONSE, BLUETOOTH_BOND,
    )

    val ALL = NATIVE + COMPANION

    /** Capabilities whose payload carries the request ID as `operation`, as in `session.Receiver`. */
    val OPERATION_BOUND = setOf("messages.send", "notifications.act")
}
