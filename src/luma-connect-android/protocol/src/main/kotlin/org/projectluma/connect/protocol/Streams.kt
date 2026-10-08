package org.projectluma.connect.protocol

import java.io.BufferedInputStream
import java.io.EOFException
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.SocketTimeoutException
import java.security.MessageDigest
import java.security.cert.X509Certificate
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocket
import kotlin.concurrent.withLock

/**
 * Long-lived streams authorized once by a journaled request (contract section 5b),
 * the Kotlin twin of `luma_continuity/companion_streams.py`.
 *
 * - `luma-input/1`: `input.control` `{"stream": {}}` returns `{session, port}`; the phone
 *   connects, sends `{session}`, then ordinary `{events: [...]}` batches.
 * - `luma-files/1`: `files.write` `{"stream": {transfer, name, size, sha256}}` returns
 *   `{session, port, offset}`; the sender connects to the receiver, sends `{session, offset}`,
 *   then exactly `size - offset` raw bytes, and reads one `{state: "complete", name}` or
 *   `{state: "failed", error}` frame.
 */
object Streams {
    const val INPUT_ALPN = "luma-input/1"
    const val FILES_ALPN = "luma-files/1"
    const val SESSION_TTL_MS = 30_000L
    const val IDLE_TIMEOUT_MS = 120_000
    const val HANDSHAKE_TIMEOUT_MS = 20_000
    const val REPLY_TIMEOUT_MS = 120_000
    const val MAX_FAILED_HANDSHAKES = 8
    const val MAX_FILE = 4L shl 30
    const val MAX_EVENTS = 64
    const val MAX_BATCH_TEXT = 1024
    const val MAX_MOVE = 1024L
    const val MAX_SCROLL = 600L
    const val MAX_ACTIVE_FILE_STREAMS = 4
    internal const val IO_CHUNK = 256 * 1024

    /** Receipts from a receiver that predates streams: use the batched or chunked request instead. */
    val FALLBACK_ERRORS = setOf("invalid-request", "unavailable")

    fun shouldFallBack(receipt: Receipt): Boolean = receipt.state == "complete" && receipt.error in FALLBACK_ERRORS

    internal val watchdog: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor { runnable ->
        Thread(runnable, "luma-stream-watchdog").apply { isDaemon = true }
    }

    /** Mutual TLS 1.3 to a one-shot listener, pinned to [peerPin], with Nagle disabled. */
    fun connect(identity: DeviceIdentity, peerPin: String, host: String, port: Int, alpn: String, timeoutMs: Int = 10_000): SSLSocket {
        val socket = Transport.connect(Transport.context(identity) { setOf(peerPin) }, host, port, peerPin, timeoutMs, alpn)
        runCatching { socket.tcpNoDelay = true }
        return socket
    }

    /** Validates an `input.control` stream result. */
    fun inputSession(receipt: Receipt): StreamSession {
        val result = receipt.result
        if (!receipt.complete || result == null || result.keys != setOf("session", "port")) throw ProtocolException("invalid input stream result")
        return StreamSession(session(result["session"]), port(result["port"]), 0)
    }

    /** Validates a `files.write` stream result for a file of [size] bytes. */
    fun fileSession(receipt: Receipt, size: Long): StreamSession {
        val result = receipt.result
        if (!receipt.complete || result == null || result.keys != setOf("session", "port", "offset")) throw ProtocolException("invalid file stream result")
        val offset = result["offset"] as? Long ?: throw ProtocolException("invalid offset")
        if (offset !in 0..size) throw ProtocolException("invalid offset")
        return StreamSession(session(result["session"]), port(result["port"]), offset)
    }

    private fun session(value: Any?): String = (value as? String)?.takeIf { Ids.IDENTIFIER.matches(it) } ?: throw ProtocolException("invalid session")
    private fun port(value: Any?): Int = (value as? Long)?.takeIf { it in 1..65535 }?.toInt() ?: throw ProtocolException("invalid port")

    fun sha256(input: InputStream, cancelled: () -> Boolean = { false }): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val buffer = ByteArray(IO_CHUNK)
        while (true) {
            if (cancelled()) throw StreamCancelledException()
            val read = input.read(buffer)
            if (read < 0) break
            digest.update(buffer, 0, read)
        }
        return hex(digest.digest())
    }

    internal fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it) }

    /** Discards exactly [count] bytes. */
    internal fun skipFully(input: InputStream, count: Long) {
        var remaining = count
        val scratch = ByteArray(64 * 1024)
        while (remaining > 0) {
            val skipped = input.skip(remaining)
            if (skipped > 0) {
                remaining -= skipped
                continue
            }
            val read = input.read(scratch, 0, minOf(scratch.size.toLong(), remaining).toInt())
            if (read < 0) throw EOFException("source is shorter than the offset")
            remaining -= read
        }
    }

    /**
     * Waits up to [timeoutMs] (0: indefinitely) for the first byte of a frame, then reads it
     * under the ordinary 20 s frame deadline.
     */
    internal fun receiveAfter(socket: SSLSocket, input: BufferedInputStream, timeoutMs: Int): Map<String, Any?> {
        socket.soTimeout = timeoutMs
        input.mark(1)
        if (input.read() < 0) throw EOFException("stream closed")
        input.reset()
        socket.soTimeout = Transport.FRAME_DEADLINE_MS.toInt()
        return Transport.receive(input)
    }
}

data class StreamSession(val session: String, val port: Int, val offset: Long)

class StreamCancelledException : IOException("stream cancelled")

/** The receiver answered `{"state": "failed", "error": code}`, or the header was refused. */
class StreamFailedException(val code: String) : IOException("file stream failed: $code")

/** A `files.write` stream offer. Validation matches the desktop's chunked `files.write` name and size rules. */
data class FileOffer(val transfer: String, val name: String, val size: Long, val sha256: String) {
    init {
        require(Ids.IDENTIFIER.matches(transfer)) { "invalid transfer" }
        require(validName(name)) { "invalid name" }
        require(size in 0..Streams.MAX_FILE) { "invalid size" }
        require(Ids.DIGEST.matches(sha256)) { "invalid digest" }
    }

    fun payload(): Map<String, Any?> = mapOf("stream" to fields())

    fun fields(): Map<String, Any?> = mapOf("transfer" to transfer, "name" to name, "size" to size, "sha256" to sha256)

    companion object {
        /** `companion.NAME` plus the chunked adapter's rules: 1-128 code points, no controls, no "/", not "." or "..". */
        fun validName(name: String): Boolean {
            val points = name.codePointCount(0, name.length)
            return points in 1..128 && name != "." && name != ".." && '/' !in name &&
                name.codePoints().noneMatch { it < 0x20 || it == 0x7f }
        }

        /** Parses the value of the `stream` key. Throws [IllegalArgumentException] for anything unexpected. */
        fun parse(stream: Any?): FileOffer {
            require(stream is Map<*, *> && stream.keys == setOf("transfer", "name", "size", "sha256")) { "invalid file stream" }
            return FileOffer(
                stream["transfer"] as? String ?: throw IllegalArgumentException("transfer"),
                stream["name"] as? String ?: throw IllegalArgumentException("name"),
                stream["size"] as? Long ?: throw IllegalArgumentException("size"),
                stream["sha256"] as? String ?: throw IllegalArgumentException("sha256"),
            )
        }
    }
}

// ----------------------------------------------------------------------------------- input

/**
 * Phone side of `luma-input/1`. [send] is thread-safe and never blocks on the network:
 * events queue for a writer thread, and while a write is in progress consecutive
 * `move` (and `scroll`) events are merged, so a backpressured socket carries fewer,
 * larger deltas instead of an ever-growing backlog. Order across types is preserved.
 */
class InputStreamClient private constructor(
    private val socket: SSLSocket,
    private val onState: (String) -> Unit,
    private val onClosed: () -> Unit,
) : AutoCloseable {
    private val lock = java.util.concurrent.locks.ReentrantLock()
    private val ready = lock.newCondition()
    private val pending = ArrayDeque<MutableMap<String, Any?>>()
    private val closing = AtomicBoolean(false)
    @Volatile var closed = false
        private set
    /** Frames written so far; for tests and diagnostics. */
    @Volatile var framesSent = 0
        private set

    private fun start() {
        Thread(::writeLoop, "luma-input-writer").apply { isDaemon = true; start() }
        Thread(::readLoop, "luma-input-reader").apply { isDaemon = true; start() }
    }

    /** Queues events. Returns false if the stream is closed or the backlog is full; the caller may fall back. */
    fun send(events: List<Map<String, Any?>>): Boolean {
        if (events.isEmpty()) return !closed
        lock.withLock {
            if (closed) return false
            for (event in events) {
                if (!enqueue(pending, event)) return false
            }
            ready.signalAll()
        }
        return true
    }

    private fun writeLoop() {
        val output = socket.outputStream
        try {
            while (true) {
                val frame = lock.withLock {
                    while (pending.isEmpty() && !closed) ready.await()
                    if (closed) return
                    takeFrame(pending)
                }
                Transport.send(output, mapOf("events" to frame))
                framesSent++
            }
        } catch (_: InterruptedException) {
        } catch (_: Exception) {
        } finally {
            close()
        }
    }

    private fun readLoop() {
        try {
            val input = BufferedInputStream(socket.inputStream, 4096)
            while (!closed) {
                // State frames can arrive minutes apart; only a started frame is bound by the 20 s deadline.
                val frame = Streams.receiveAfter(socket, input, 0)
                val state = frame["state"] as? String
                if (frame.keys == setOf("state") && state in setOf("needs-user", "accepted")) {
                    runCatching { onState(state!!) }
                }
            }
        } catch (_: Exception) {
        } finally {
            close()
        }
    }

    override fun close() {
        if (!closing.compareAndSet(false, true)) return
        lock.withLock {
            closed = true
            pending.clear()
            ready.signalAll()
        }
        runCatching { socket.close() }
        runCatching { onClosed() }
    }

    companion object {
        const val MAX_PENDING = 4096

        /** Connects to the desktop's one-shot listener and sends the header. */
        fun open(
            identity: DeviceIdentity,
            desktopPin: String,
            host: String,
            stream: StreamSession,
            onState: (String) -> Unit = {},
            onClosed: () -> Unit = {},
        ): InputStreamClient {
            val socket = Streams.connect(identity, desktopPin, host, stream.port, Streams.INPUT_ALPN, 5_000)
            try {
                Transport.send(socket.outputStream, mapOf("session" to stream.session))
            } catch (error: Throwable) {
                runCatching { socket.close() }
                throw error
            }
            return InputStreamClient(socket, onState, onClosed).also { it.start() }
        }

        /** Appends [event], merging a relative motion into the previous queued motion of the same type. */
        internal fun enqueue(pending: ArrayDeque<MutableMap<String, Any?>>, event: Map<String, Any?>): Boolean {
            val type = event["type"]
            val last = pending.lastOrNull()
            if ((type == "move" || type == "scroll") && last != null && last["type"] == type) {
                val bound = if (type == "move") Streams.MAX_MOVE else Streams.MAX_SCROLL
                val dx = (last["dx"] as Long) + (event["dx"] as Long)
                val dy = (last["dy"] as Long) + (event["dy"] as Long)
                if (kotlin.math.abs(dx) <= bound && kotlin.math.abs(dy) <= bound) {
                    last["dx"] = dx
                    last["dy"] = dy
                    return true
                }
            }
            if (pending.size >= MAX_PENDING) return false
            pending.addLast(LinkedHashMap(event))
            return true
        }

        /** Removes one frame's worth of events, within the `input.control` batch bounds. */
        internal fun takeFrame(pending: ArrayDeque<MutableMap<String, Any?>>): List<Map<String, Any?>> {
            val frame = ArrayList<Map<String, Any?>>()
            var text = 0
            while (pending.isNotEmpty() && frame.size < Streams.MAX_EVENTS) {
                val next = pending.first()
                val length = (next["text"] as? String)?.length ?: 0
                if (frame.isNotEmpty() && text + length > Streams.MAX_BATCH_TEXT) break
                text += length
                frame += pending.removeFirst()
            }
            return frame
        }
    }
}

// ----------------------------------------------------------------------------------- files: sending

/** Sender side of `luma-files/1`. One instance per attempt; [cancel] may be called from any thread. */
class FileStreamSender(private val identity: DeviceIdentity, private val peerPin: String, private val host: String) {
    @Volatile private var socket: SSLSocket? = null
    @Volatile private var cancelled = false

    fun cancel() {
        cancelled = true
        runCatching { socket?.close() }
    }

    /**
     * Streams bytes `stream.offset until size` of [source] (positioned at byte 0) and
     * returns the name the receiver stored. [progress] receives `(bytesAtReceiver, size)`.
     */
    fun send(stream: StreamSession, size: Long, source: InputStream, progress: (Long, Long) -> Unit = { _, _ -> }): String {
        if (cancelled) throw StreamCancelledException()
        try {
            Streams.skipFully(source, stream.offset)
            val connected = Streams.connect(identity, peerPin, host, stream.port, Streams.FILES_ALPN)
            socket = connected
            connected.use {
                if (cancelled) throw StreamCancelledException()
                val output: OutputStream = it.outputStream
                Transport.send(output, mapOf("session" to stream.session, "offset" to stream.offset))
                var position = stream.offset
                val buffer = ByteArray(Streams.IO_CHUNK)
                progress(position, size)
                while (position < size) {
                    if (cancelled) throw StreamCancelledException()
                    val read = source.read(buffer, 0, minOf(buffer.size.toLong(), size - position).toInt())
                    if (read < 0) throw EOFException("source ended early")
                    output.write(buffer, 0, read)
                    position += read
                    progress(position, size)
                }
                output.flush()
                if (source.read() >= 0) throw IOException("source changed while sending")
                val reply = Streams.receiveAfter(it, BufferedInputStream(it.inputStream, 64 * 1024), Streams.REPLY_TIMEOUT_MS)
                when {
                    reply.keys == setOf("state", "name") && reply["state"] == "complete" && reply["name"] is String ->
                        return reply["name"] as String
                    reply.keys == setOf("state", "error") && reply["state"] == "failed" && reply["error"] is String ->
                        throw StreamFailedException(reply["error"] as String)
                    else -> throw ProtocolException("invalid file stream reply")
                }
            }
        } catch (error: IOException) {
            if (cancelled && error !is StreamFailedException) throw StreamCancelledException()
            throw error
        } finally {
            socket = null
        }
    }
}

// ----------------------------------------------------------------------------------- listening

/**
 * A single-use listener for one stream, as `companion_media.MediaSession` on the desktop:
 * only [peerPin] over TLS 1.3 with [alpn], 30 seconds to connect, eight failed handshakes
 * at most, and the first authenticated connection consumes it. [handler] runs on the
 * listener thread and owns the socket until it returns.
 *
 * States: `waiting`, `streaming`, then `closed`, `failed`, `expired` or `cancelled`.
 */
class OneShotStreamListener(
    identity: DeviceIdentity,
    val peerPin: String,
    private val alpn: String,
    private val ttlMs: Long = Streams.SESSION_TTL_MS,
    private val allowedSource: (InetAddress) -> Boolean = ::isLocalAddress,
    private val clock: () -> Long = System::currentTimeMillis,
) : AutoCloseable {
    val session: String = Ids.token()
    private val server: SSLServerSocket
    private val created = clock()
    private val stopped = AtomicBoolean(false)
    private val done = CountDownLatch(1)
    @Volatile private var active: SSLSocket? = null
    @Volatile var state: String = "waiting"
        private set
    @Volatile var error: String? = null
        private set
    val port: Int

    init {
        val context = Transport.context(identity) { setOf(peerPin) }
        server = (context.serverSocketFactory.createServerSocket() as SSLServerSocket).apply {
            bind(InetSocketAddress(0), 2)
            soTimeout = 250
        }
        port = server.localPort
    }

    fun start(onFinished: (OneShotStreamListener) -> Unit = {}, handler: (SSLSocket) -> Unit): OneShotStreamListener {
        Thread({
            try {
                val socket = accept()
                if (socket != null) serve(socket, handler)
            } catch (_: Throwable) {
                finish("failed", "internal")
            } finally {
                runCatching { server.close() }
                done.countDown()
                runCatching { onFinished(this) }
            }
        }, "luma-stream-$alpn").apply { isDaemon = true; start() }
        return this
    }

    private fun accept(): SSLSocket? {
        var failures = 0
        while (true) {
            if (stopped.get()) return null.also { finish("cancelled", "cancelled") }
            if (clock() - created >= ttlMs) return null.also { finish("expired", "expired") }
            val socket = try {
                server.accept() as SSLSocket
            } catch (_: SocketTimeoutException) {
                continue
            } catch (_: IOException) {
                if (stopped.get()) continue
                failures++
                if (failures >= Streams.MAX_FAILED_HANDSHAKES) return null.also { finish("failed", "too-many-attempts") }
                continue
            }
            if (handshake(socket)) {
                runCatching { server.close() }
                return socket
            }
            failures++
            if (failures >= Streams.MAX_FAILED_HANDSHAKES) return null.also { finish("failed", "too-many-attempts") }
        }
    }

    private fun handshake(socket: SSLSocket): Boolean {
        val timer = Streams.watchdog.schedule({ runCatching { socket.close() } }, Streams.HANDSHAKE_TIMEOUT_MS.toLong(), TimeUnit.MILLISECONDS)
        try {
            if (!allowedSource(socket.inetAddress)) throw DeniedException("connection from outside the local network")
            socket.soTimeout = Streams.HANDSHAKE_TIMEOUT_MS
            runCatching { socket.tcpNoDelay = true }
            val parameters = socket.sslParameters
            parameters.protocols = arrayOf("TLSv1.3")
            parameters.applicationProtocols = arrayOf(alpn)
            parameters.needClientAuth = true
            socket.sslParameters = parameters
            socket.useClientMode = false
            socket.startHandshake()
            val peer = socket.session.peerCertificates.firstOrNull() as? X509Certificate ?: throw DeniedException("certificate required")
            if (socket.session.protocol != "TLSv1.3" || socket.applicationProtocol != alpn ||
                !MessageDigest.isEqual(Transport.fingerprint(peer).toByteArray(), peerPin.toByteArray())
            ) throw DeniedException("unapproved device or protocol")
            return true
        } catch (_: Exception) {
            runCatching { socket.close() }
            return false
        } finally {
            timer.cancel(false)
        }
    }

    private fun serve(socket: SSLSocket, handler: (SSLSocket) -> Unit) {
        active = socket
        socket.use {
            synchronized(this) { if (state == "waiting" && !stopped.get()) state = "streaming" }
            try {
                if (stopped.get()) throw StreamCancelledException()
                handler(it)
                finish("closed", null)
            } catch (failure: StreamFailedException) {
                if (stopped.get()) finish("cancelled", "cancelled") else finish("failed", failure.code)
            } catch (_: Exception) {
                if (stopped.get()) finish("cancelled", "cancelled") else finish("failed", "truncated")
            } finally {
                active = null
            }
        }
    }

    @Synchronized
    private fun finish(next: String, code: String?) {
        if (state == "waiting" || state == "streaming") {
            state = next
            error = code
        }
    }

    val finished: Boolean get() = done.count == 0L

    fun join(timeoutMs: Long): Boolean = done.await(timeoutMs, TimeUnit.MILLISECONDS)

    override fun close() {
        stopped.set(true)
        runCatching { server.close() }
        runCatching { active?.close() }
    }
}

// ----------------------------------------------------------------------------------- files: receiving

/** Stored bytes of one incoming transfer. Implementations persist enough to find it again after a restart. */
interface PartialFile {
    /** Bytes stored so far. */
    fun length(): Long
    /** Reads the stored bytes from the start. */
    fun openRead(): InputStream
    /** Truncates to [offset] and returns a stream that appends from there; closing it must flush durably. */
    fun openWrite(offset: Long): OutputStream
    /** Publishes the verified file (for example `IS_PENDING=0`) and returns the name actually stored. */
    fun commit(): String
    /** Deletes the stored bytes and the persisted record. */
    fun discard()
}

fun interface PartialStore {
    /** Returns the partial file for [offer] from [peerPin]: a matching persisted one, or a new empty one. */
    fun open(peerPin: String, offer: FileOffer): PartialFile
}

/**
 * Receiver side of `luma-files/1`: validates offers, opens one-shot listeners and
 * verifies, commits and answers each stream. [authorized] is re-checked when the
 * sender connects. [onFinished] runs on the listener thread with the stored name
 * (on success) or null.
 */
class FileStreamReceiving(
    private val identity: DeviceIdentity,
    private val store: PartialStore,
    private val authorized: (peerPin: String) -> Boolean = { true },
    private val allowedSource: (InetAddress) -> Boolean = ::isLocalAddress,
    private val onFinished: (FileOffer, String?) -> Unit = { _, _ -> },
) {
    private val active = HashMap<String, OneShotStreamListener>()

    /** Handles a `files.write` payload of exactly `{"stream": {...}}`. */
    fun offer(peerPin: String, payload: Map<String, Any?>): Map<String, Any?> {
        require(payload.keys == setOf("stream")) { "invalid file stream request" }
        val offer = FileOffer.parse(payload["stream"])
        val key = peerPin + "/" + offer.transfer
        val previous = synchronized(active) { active.remove(key) }
        if (previous != null) {
            previous.close()
            if (!previous.join(5_000)) return mapOf("error" to "busy")
        }
        synchronized(active) {
            active.values.removeAll { it.finished }
            if (active.size >= Streams.MAX_ACTIVE_FILE_STREAMS) return mapOf("error" to "busy")
        }
        val partial = try {
            store.open(peerPin, offer)
        } catch (_: java.io.IOException) {
            return mapOf("error" to "storage")
        }
        val offset = minOf(partial.length(), offer.size)
        val listener = OneShotStreamListener(identity, peerPin, Streams.FILES_ALPN, allowedSource = allowedSource)
        synchronized(active) { active[key] = listener }
        var stored: String? = null
        listener.start(onFinished = { finished ->
            synchronized(active) { if (active[key] === finished) active.remove(key) }
            runCatching { onFinished(offer, stored) }
        }) { socket ->
            stored = receive(socket, listener, peerPin, offer, offset, partial)
        }
        return mapOf("session" to listener.session, "port" to listener.port.toLong(), "offset" to offset)
    }

    private fun receive(socket: SSLSocket, listener: OneShotStreamListener, peerPin: String, offer: FileOffer, offset: Long, partial: PartialFile): String {
        val output = socket.outputStream
        fun fail(code: String): Nothing {
            runCatching { Transport.send(output, mapOf("state" to "failed", "error" to code)) }
            throw StreamFailedException(code)
        }
        if (!authorized(peerPin)) fail("revoked")
        val input = BufferedInputStream(socket.inputStream, Streams.IO_CHUNK)
        val header = try {
            socket.soTimeout = Streams.HANDSHAKE_TIMEOUT_MS
            Transport.receive(input)
        } catch (_: Exception) {
            fail("invalid-header")
        }
        if (header.keys != setOf("session", "offset")) fail("invalid-header")
        val session = header["session"] as? String
        if (session == null || !MessageDigest.isEqual(session.toByteArray(), listener.session.toByteArray())) fail("wrong-session")
        if (header["offset"] != offset) fail("wrong-offset")

        val digest = MessageDigest.getInstance("SHA-256")
        val buffer = ByteArray(Streams.IO_CHUNK)
        // A new partial may not exist on disk yet (MediaStore creates the file on first write).
        if (offset > 0) try {
            partial.openRead().use { prefix ->
                var remaining = offset
                while (remaining > 0) {
                    val read = prefix.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
                    if (read < 0) fail("partial-changed")
                    digest.update(buffer, 0, read)
                    remaining -= read
                }
            }
        } catch (failure: StreamFailedException) {
            throw failure
        } catch (_: IOException) {
            fail("storage")
        }
        socket.soTimeout = Streams.IDLE_TIMEOUT_MS
        val sink = try { partial.openWrite(offset) } catch (_: IOException) { fail("storage") }
        sink.use {
            var remaining = offer.size - offset
            while (remaining > 0) {
                val read = try {
                    input.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
                } catch (_: SocketTimeoutException) {
                    fail("idle-timeout")
                } catch (_: IOException) {
                    throw StreamFailedException("truncated")
                }
                if (read < 0) throw StreamFailedException("truncated")
                try { it.write(buffer, 0, read) } catch (_: IOException) { fail("storage") }
                digest.update(buffer, 0, read)
                remaining -= read
            }
        }
        if (!MessageDigest.isEqual(Streams.hex(digest.digest()).toByteArray(), offer.sha256.toByteArray())) {
            runCatching { partial.discard() }
            fail("digest-mismatch")
        }
        val name = try { partial.commit() } catch (_: Exception) { fail("storage") }
        runCatching { Transport.send(output, mapOf("state" to "complete", "name" to name)) }
        return name
    }

    /** Stops every open listener, for example when the computer is unpaired. */
    fun closeAll() {
        val listeners = synchronized(active) { active.values.toList().also { active.clear() } }
        listeners.forEach { it.close() }
    }
}
