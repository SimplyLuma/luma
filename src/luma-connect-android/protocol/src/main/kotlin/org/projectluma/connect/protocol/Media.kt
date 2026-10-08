package org.projectluma.connect.protocol

import java.io.DataOutputStream
import java.io.InputStream
import java.io.OutputStream
import javax.net.ssl.SSLSocket

/**
 * Sender side of `luma-media/1` (contract section 5): one JSON header frame,
 * then typed binary packets. Control frames from the desktop are read on a
 * separate thread with the ordinary JSON framing.
 */
class MediaStream private constructor(private val socket: SSLSocket) : AutoCloseable {
    private val output = DataOutputStream(socket.outputStream.buffered(64 * 1024))
    private val lock = Any()

    fun codecConfig(data: ByteArray) = packet(TYPE_CONFIG, 0, data)
    fun video(ptsUs: Long, data: ByteArray, key: Boolean) = packet(if (key) TYPE_KEY_FRAME else TYPE_FRAME, ptsUs, data)
    fun audioConfig(data: ByteArray) = packet(TYPE_AUDIO_CONFIG, 0, data)
    fun audio(ptsUs: Long, data: ByteArray) = packet(TYPE_AUDIO, ptsUs, data)

    fun packet(type: Int, ptsUs: Long, data: ByteArray) {
        require(data.size <= MAX_PACKET) { "packet too large" }
        synchronized(lock) {
            output.writeByte(type)
            output.writeLong(ptsUs)
            output.writeInt(data.size)
            output.write(data)
            output.flush()
        }
    }

    /** Blocks reading validated control events until the stream closes. */
    fun readControl(onEvent: (Map<String, Any?>) -> Unit) {
        val input: InputStream = socket.inputStream
        socket.soTimeout = 0
        while (true) {
            val event = try {
                Transport.receive(input)
            } catch (_: java.net.SocketTimeoutException) {
                continue
            }
            validateControl(event)
            onEvent(event)
        }
    }

    override fun close() {
        runCatching { synchronized(lock) { output.writeByte(TYPE_END); output.writeLong(0); output.writeInt(0); output.flush() } }
        runCatching { socket.close() }
    }

    companion object {
        const val ALPN = "luma-media/1"
        const val TYPE_CONFIG = 0x01
        const val TYPE_FRAME = 0x02
        const val TYPE_KEY_FRAME = 0x03
        const val TYPE_AUDIO_CONFIG = 0x04
        const val TYPE_AUDIO = 0x05
        const val TYPE_END = 0x7f
        const val MAX_PACKET = 8 * 1024 * 1024

        fun open(identity: DeviceIdentity, desktop: Peer, port: Int, header: Map<String, Any?>): MediaStream {
            val host = desktop.host ?: throw ProtocolException("no route to device")
            val context = Transport.context(identity) { setOf(desktop.pin) }
            val socket = Transport.connect(context, host, port, desktop.pin, 5_000, ALPN)
            val stream = MediaStream(socket)
            val out: OutputStream = stream.output
            Transport.send(out, header)
            return stream
        }

        val CODECS = setOf("av1", "vp9", "vp8", "h264", "h265")

        fun header(session: String, kind: String, width: Int, height: Int, fps: Int, rotation: Int, audio: Boolean, codec: String = "h264"): Map<String, Any?> {
            require(Ids.IDENTIFIER.matches(session) && kind in setOf("camera", "screen") && codec in CODECS)
            require(width in 1..8192 && height in 1..8192 && fps in 1..120 && rotation in setOf(0, 90, 180, 270))
            return mapOf(
                "session" to session, "kind" to kind, "codec" to codec, "width" to width.toLong(), "height" to height.toLong(),
                "fps" to fps.toLong(), "rotation" to rotation.toLong(), "audio" to if (audio) "opus" else null,
            )
        }

        fun validateControl(event: Map<String, Any?>) {
            when (event["type"]) {
                "touch" -> {
                    val x = event["x"] as? Long
                    val y = event["y"] as? Long
                    if (event.keys != setOf("type", "action", "x", "y") || event["action"] !in setOf("down", "move", "up") ||
                        x == null || y == null || x !in 0..10_000 || y !in 0..10_000
                    ) throw ProtocolException("invalid touch event")
                }
                "text" -> if (event.keys != setOf("type", "text") || (event["text"] as? String)?.let { it.length <= 4096 } != true) {
                    throw ProtocolException("invalid text event")
                }
                "key" -> if (event.keys != setOf("type", "key") || event["key"] !in setOf("back", "home", "recents")) {
                    throw ProtocolException("invalid key event")
                }
                else -> throw ProtocolException("unknown control event")
            }
        }
    }
}
