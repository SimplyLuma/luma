package org.projectluma.connect.protocol

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.io.RandomAccessFile
import java.nio.file.Files
import java.security.MessageDigest
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** A partial store on the JVM filesystem, used by the loopback and interoperability tests. */
class DirectoryPartialStore(private val directory: File, private val downloads: File) : PartialStore {
    override fun open(peerPin: String, offer: FileOffer): PartialFile {
        directory.mkdirs()
        val base = "${peerPin.take(16)}-${offer.transfer}"
        val part = File(directory, "$base.part")
        val record = File(directory, "$base.json")
        val expected = Json.encodeToString(mapOf("peer" to peerPin) + offer.fields())
        if (!(record.exists() && record.readText() == expected)) {
            part.delete()
            record.writeText(expected)
        }
        return object : PartialFile {
            override fun length() = if (part.exists()) part.length() else 0L
            // Like a fresh MediaStore row, a partial that was never written has no file to open.
            override fun openRead(): InputStream = part.inputStream()
            override fun openWrite(offset: Long): OutputStream {
                val file = RandomAccessFile(part, "rw")
                file.setLength(offset)
                file.seek(offset)
                return object : OutputStream() {
                    override fun write(b: Int) = file.write(b)
                    override fun write(b: ByteArray, off: Int, len: Int) = file.write(b, off, len)
                    override fun close() { file.fd.sync(); file.close() }
                }
            }
            override fun commit(): String {
                downloads.mkdirs()
                var target = File(downloads, offer.name)
                var index = 2
                while (target.exists()) target = File(downloads, offer.name.substringBeforeLast('.') + " (${index++})." + offer.name.substringAfterLast('.'))
                Files.move(part.toPath(), target.toPath())
                record.delete()
                return target.name
            }
            override fun discard() { part.delete(); record.delete() }
        }
    }
}

class StreamsTest {
    private val phone = TestIdentities.create()
    private val desktop = TestIdentities.create()

    private fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    @Test
    fun inputClientSendsHeaderAndOrderedFramesWithNoDelay() {
        val frames = LinkedBlockingQueue<Map<String, Any?>>()
        val listener = OneShotStreamListener(desktop, phone.pin, Streams.INPUT_ALPN)
        listener.start { socket ->
            assertTrue(socket.tcpNoDelay)
            var count = 0
            while (true) {
                val frame = runCatching { Transport.receive(socket.inputStream) }.getOrNull() ?: break
                frames.put(frame)
                if (++count == 2) Transport.send(socket.outputStream, mapOf("state" to "needs-user"))
            }
        }
        val states = LinkedBlockingQueue<String>()
        val client = InputStreamClient.open(phone, desktop.pin, "127.0.0.1", StreamSession(listener.session, listener.port, 0), onState = { states.put(it) })
        client.use {
            assertTrue(it.send(listOf(mapOf("type" to "button", "button" to "left", "pressed" to true))))
            assertEquals(mapOf("session" to listener.session), frames.poll(5, TimeUnit.SECONDS))
            assertEquals(listOf(mapOf("type" to "button", "button" to "left", "pressed" to true)), frames.poll(5, TimeUnit.SECONDS)!!["events"])
            assertEquals("needs-user", states.poll(5, TimeUnit.SECONDS))
            assertTrue(it.send(listOf(mapOf("type" to "text", "text" to "hi"))))
            assertEquals(listOf(mapOf("type" to "text", "text" to "hi")), frames.poll(5, TimeUnit.SECONDS)!!["events"])
        }
        assertFalse(client.send(listOf(mapOf("type" to "move", "dx" to 1L, "dy" to 1L))))
        assertTrue(listener.join(5_000))
        assertEquals("closed", listener.state)
    }

    @Test
    fun backpressuredMovesCoalesceWithoutReorderingOtherEvents() {
        val pending = ArrayDeque<MutableMap<String, Any?>>()
        fun move(dx: Long, dy: Long) = mapOf("type" to "move", "dx" to dx, "dy" to dy)
        assertTrue(InputStreamClient.enqueue(pending, move(3, 4)))
        assertTrue(InputStreamClient.enqueue(pending, move(-1, 2)))
        assertTrue(InputStreamClient.enqueue(pending, mapOf("type" to "button", "button" to "left", "pressed" to true)))
        assertTrue(InputStreamClient.enqueue(pending, move(1000, 0)))
        assertTrue(InputStreamClient.enqueue(pending, move(100, 0))) // would exceed ±1024, so it starts a new event
        assertTrue(InputStreamClient.enqueue(pending, mapOf("type" to "scroll", "dx" to 0L, "dy" to 10L)))
        assertTrue(InputStreamClient.enqueue(pending, mapOf("type" to "scroll", "dx" to 0L, "dy" to 10L)))
        assertEquals(
            listOf(move(2, 6), mapOf("type" to "button", "button" to "left", "pressed" to true), move(1000, 0), move(100, 0),
                mapOf("type" to "scroll", "dx" to 0L, "dy" to 20L)),
            pending.toList(),
        )
        // Frames respect the input.control batch bounds.
        pending.clear()
        repeat(70) { InputStreamClient.enqueue(pending, mapOf("type" to "key", "key" to "a", "pressed" to (it % 2 == 0))) }
        assertEquals(64, InputStreamClient.takeFrame(pending).size)
        assertEquals(6, InputStreamClient.takeFrame(pending).size)
        repeat(5) { InputStreamClient.enqueue(pending, mapOf("type" to "text", "text" to "x".repeat(256))) }
        assertEquals(4, InputStreamClient.takeFrame(pending).size)
        assertEquals(1, InputStreamClient.takeFrame(pending).size)
    }

    @Test
    fun fileStreamIsVerifiedCommittedAndResumed() {
        val root = Files.createTempDirectory("luma-streams").toFile()
        try {
            val bytes = ByteArray(3 * 1024 * 1024 + 11) { (it * 31 % 253).toByte() }
            val offer = FileOffer(Ids.token(), "clip.mp4", bytes.size.toLong(), sha(bytes))
            val finished = CopyOnWriteArrayList<String?>()
            val receiving = FileStreamReceiving(phone, DirectoryPartialStore(File(root, "partial"), File(root, "Downloads")),
                onFinished = { _, name -> finished += name })

            // First attempt: the sender is cancelled part way through.
            val first = receiving.offer(desktop.pin, offer.payload())
            assertEquals(0L, first["offset"])
            val sender = FileStreamSender(desktop, phone.pin, "127.0.0.1")
            val session = StreamSession(first["session"] as String, (first["port"] as Long).toInt(), 0)
            assertThrows(StreamCancelledException::class.java) {
                sender.send(session, offer.size, ByteArrayInputStream(bytes)) { sent, _ -> if (sent >= 1_000_000) sender.cancel() }
            }

            // The re-offer stops the old session and reports where the partial file ends.
            val second = receiving.offer(desktop.pin, offer.payload())
            val offset = second["offset"] as Long
            // Bytes still in flight when the socket closed are lost; everything the receiver stored is kept.
            assertTrue("resumed at $offset", offset in 500_000L until offer.size)
            val progress = CopyOnWriteArrayList<Long>()
            val name = FileStreamSender(desktop, phone.pin, "127.0.0.1").send(
                StreamSession(second["session"] as String, (second["port"] as Long).toInt(), offset), offer.size, ByteArrayInputStream(bytes),
            ) { sent, _ -> progress += sent }
            assertEquals("clip.mp4", name)
            assertEquals(offset, progress.first())
            assertEquals(offer.size, progress.last())
            assertArrayEquals(bytes, File(root, "Downloads/clip.mp4").readBytes())
            assertTrue(File(root, "partial").listFiles()!!.isEmpty())
            waitUntil { finished.contains("clip.mp4") }
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun digestMismatchIsReportedAndDiscarded() {
        val root = Files.createTempDirectory("luma-streams").toFile()
        try {
            val bytes = ByteArray(10_000) { it.toByte() }
            val offer = FileOffer(Ids.token(), "x.bin", bytes.size.toLong(), "0".repeat(64))
            val receiving = FileStreamReceiving(phone, DirectoryPartialStore(File(root, "partial"), File(root, "Downloads")))
            val result = receiving.offer(desktop.pin, offer.payload())
            val error = assertThrows(StreamFailedException::class.java) {
                FileStreamSender(desktop, phone.pin, "127.0.0.1").send(StreamSession(result["session"] as String, (result["port"] as Long).toInt(), 0), offer.size, ByteArrayInputStream(bytes))
            }
            assertEquals("digest-mismatch", error.code)
            assertFalse(File(root, "Downloads/x.bin").exists())
            assertTrue(File(root, "partial").listFiles()!!.isEmpty())
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun offersAreStrict() {
        val receiving = FileStreamReceiving(phone, { _, _ -> error("must not open storage") })
        val good = FileOffer(Ids.token(), "a.txt", 1, "a".repeat(64)).fields()
        listOf(
            good + ("name" to "../evil"), good + ("name" to ".."), good + ("name" to "a b"), good + ("name" to "x".repeat(129)),
            good + ("size" to (4L shl 30) + 1), good + ("size" to -1L), good + ("transfer" to "nope"), good + ("sha256" to "A".repeat(64)),
            good + ("offset" to 0L), good - "sha256", good + ("size" to 1),
        ).forEach { stream ->
            assertThrows(stream.toString(), IllegalArgumentException::class.java) { receiving.offer(desktop.pin, mapOf("stream" to stream)) }
        }
        assertThrows(IllegalArgumentException::class.java) { receiving.offer(desktop.pin, mapOf("stream" to good, "data" to "")) }
        assertTrue(FileOffer.validName("résumé 😀.pdf"))
    }

    @Test
    fun listenerAcceptsOnlyThePinnedPeerOnceAndExpires() {
        val stranger = TestIdentities.create()
        val listener = OneShotStreamListener(phone, desktop.pin, Streams.FILES_ALPN)
        val served = CopyOnWriteArrayList<String>()
        listener.start { served += "served" }
        // Wrong certificate and wrong ALPN fail the handshake without consuming the session.
        assertThrows(Exception::class.java) { Streams.connect(stranger, phone.pin, "127.0.0.1", listener.port, Streams.FILES_ALPN).use { it.inputStream.read() } }
        assertThrows(Exception::class.java) { Streams.connect(desktop, phone.pin, "127.0.0.1", listener.port, Streams.INPUT_ALPN).use { it.inputStream.read() } }
        assertEquals("waiting", listener.state)
        Streams.connect(desktop, phone.pin, "127.0.0.1", listener.port, Streams.FILES_ALPN).use { it.soTimeout = 5_000; it.inputStream.read() }
        assertTrue(listener.join(5_000))
        assertEquals(listOf("served"), served)
        assertEquals("closed", listener.state)
        // Consumed: a second connection is refused.
        assertThrows(Exception::class.java) { Streams.connect(desktop, phone.pin, "127.0.0.1", listener.port, Streams.FILES_ALPN).use { it.inputStream.read() } }

        var now = 1_000L
        val expiring = OneShotStreamListener(phone, desktop.pin, Streams.FILES_ALPN, clock = { now }).start { served += "late" }
        now += 31_000
        assertTrue(expiring.join(5_000))
        assertEquals("expired", expiring.state)

        val cancelled = OneShotStreamListener(phone, desktop.pin, Streams.FILES_ALPN).start { }
        cancelled.close()
        assertTrue(cancelled.join(5_000))
        assertEquals("cancelled", cancelled.state)
        assertEquals(listOf("served"), served)
    }

    @Test
    fun fallbackIsLimitedToReceiversWithoutStreams() {
        assertTrue(Streams.shouldFallBack(Receipt("complete", mapOf("error" to "invalid-request"))))
        assertTrue(Streams.shouldFallBack(Receipt("complete", mapOf("error" to "unavailable"))))
        assertFalse(Streams.shouldFallBack(Receipt("complete", mapOf("error" to "revoked"))))
        assertFalse(Streams.shouldFallBack(Receipt("denied", null)))
        assertThrows(ProtocolException::class.java) { Streams.fileSession(Receipt("complete", mapOf("session" to Ids.token(), "port" to 5L, "offset" to 11L)), 10) }
        assertEquals(StreamSession("a".repeat(32), 4000, 0), Streams.inputSession(Receipt("complete", mapOf("session" to "a".repeat(32), "port" to 4000L))))
    }

    private fun waitUntil(condition: () -> Boolean) {
        val deadline = System.currentTimeMillis() + 5_000
        while (!condition()) {
            check(System.currentTimeMillis() < deadline) { "condition not met" }
            Thread.sleep(20)
        }
    }
}
