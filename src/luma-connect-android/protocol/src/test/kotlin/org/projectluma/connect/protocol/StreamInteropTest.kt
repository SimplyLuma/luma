package org.projectluma.connect.protocol

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.File
import java.nio.file.Files
import java.security.MessageDigest
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * `luma-input/1` and `luma-files/1` between this Kotlin implementation and the real
 * Python desktop modules (`tests/companion_streams_peer.py`), including resumed
 * transfers in both directions. Opt-in like [DaemonInteropTest].
 */
class StreamInteropTest {
    private val python = System.getProperty("luma.interop.python").orEmpty()
    private val source = System.getProperty("luma.interop.source").orEmpty()

    @Suppress("UNCHECKED_CAST")
    @Test
    fun streamsInteroperateWithPythonDesktop() {
        assumeTrue("set LUMA_INTEROP_PYTHON and LUMA_INTEROP_SOURCE", python.isNotEmpty() && source.isNotEmpty())
        val root = Files.createTempDirectory("luma-stream-interop").toFile()
        val process = ProcessBuilder(python, File(source, "tests/companion_streams_peer.py").path, root.path)
            .apply { environment()["PYTHONPATH"] = source }
            .redirectError(ProcessBuilder.Redirect.INHERIT)
            .start()
        val lines = LinkedBlockingQueue<Map<String, Any?>>()
        Thread {
            process.inputStream.bufferedReader().useLines { sequence -> sequence.forEach { lines.put(Json.parse(it) as Map<String, Any?>) } }
        }.apply { isDaemon = true; start() }
        fun next(key: String, filter: (Map<String, Any?>) -> Boolean = { true }): Map<String, Any?> {
            while (true) {
                val line = lines.poll(30, TimeUnit.SECONDS) ?: error("daemon did not report $key")
                if (key in line && filter(line)) return line
            }
        }
        fun command(value: Map<String, Any?>) {
            process.outputStream.write(Json.encode(value) + '\n'.code.toByte())
            process.outputStream.flush()
        }
        fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

        val phone = TestIdentities.create()
        val journal = Journal(InMemoryJournalStore())
        val desktopPin = AtomicReference<String>()
        val receiving = FileStreamReceiving(phone, DirectoryPartialStore(File(root, "phone-partial"), File(root, "phone-downloads")))
        val receiver = Receiver(journal, mapOf(
            Capabilities.FILES_WRITE to { _, payload -> receiving.offer(desktopPin.get(), payload) },
        ))
        val listener = Listener(phone, journal, receiver, 0)
        listener.start()
        try {
            val invitation = Pairing.parse(next("uri")["uri"] as String)
            val result = Pairing.pair(invitation, phone, journal, "Pixel Test", "Google Pixel 9", listener.boundPort,
                setOf("device.status", "input.control", "files.write"), setOf("files.write"))
            next("paired")
            val desk = result.peer.pin
            desktopPin.set(desk)
            val exchange = Exchange(phone, journal)

            // luma-input/1: one journaled request, then frames on a long-lived stream.
            val stream = Streams.inputSession(exchange.call(desk, "input.control", mapOf("stream" to emptyMap<String, Any?>())))
            val states = LinkedBlockingQueue<String>()
            InputStreamClient.open(phone, desk, "127.0.0.1", stream, onState = { states.put(it) }).use { client ->
                assertTrue(client.send(listOf(mapOf("type" to "move", "dx" to 12L, "dy" to -4L))))
                assertEquals(listOf(mapOf("type" to "move", "dx" to 12L, "dy" to -4L)), next("event")["value"])
                assertTrue(client.send(listOf(
                    mapOf("type" to "button", "button" to "left", "pressed" to true),
                    mapOf("type" to "button", "button" to "left", "pressed" to false),
                    mapOf("type" to "text", "text" to "héllo"),
                )))
                assertEquals(3, (next("event")["value"] as List<*>).size)
                repeat(200) { client.send(listOf(mapOf("type" to "move", "dx" to 1L, "dy" to 0L))) }
                var moved = 0L
                while (moved < 200) {
                    (next("event")["value"] as List<Map<String, Any?>>).forEach { moved += it["dx"] as Long }
                }
                // Informational latency: each sample waits until the desktop's injector saw the event.
                val streamed = (1..20).map {
                    val started = System.nanoTime()
                    client.send(listOf(mapOf("type" to "move", "dx" to 1L, "dy" to 1L)))
                    next("event")
                    (System.nanoTime() - started) / 1_000
                }
                val requested = (1..20).map {
                    val started = System.nanoTime()
                    exchange.call(desk, "input.control", mapOf("events" to listOf(mapOf("type" to "move", "dx" to 1L, "dy" to 1L))), lifetimeSeconds = 10)
                    next("event")
                    (System.nanoTime() - started) / 1_000
                }
                System.err.println("input latency, median of 20 on loopback: stream ${streamed.sorted()[10]} us, per-request TLS ${requested.sorted()[10]} us")
            }
            // The batched request path still works beside the stream.
            val batched = exchange.call(desk, "input.control", mapOf("events" to listOf(mapOf("type" to "scroll", "dx" to 0L, "dy" to 10L))))
            assertEquals(mapOf("accepted" to true), batched.result)
            next("event")

            // luma-files/1 phone to desktop, interrupted and resumed.
            val bytes = ByteArray(6 * 1024 * 1024 + 3) { (it * 7 % 251).toByte() }
            val offer = FileOffer(Ids.token(), "trip.mov", bytes.size.toLong(), sha(bytes))
            val first = Streams.fileSession(exchange.call(desk, "files.write", offer.payload()), offer.size)
            assertEquals(0L, first.offset)
            val sender = FileStreamSender(phone, desk, "127.0.0.1")
            assertThrows(StreamCancelledException::class.java) {
                sender.send(first, offer.size, ByteArrayInputStream(bytes)) { sent, _ -> if (sent >= 2_500_000) sender.cancel() }
            }
            val second = Streams.fileSession(exchange.call(desk, "files.write", offer.payload()), offer.size)
            assertTrue("resumed at ${second.offset}", second.offset in 1 until offer.size)
            assertEquals("trip.mov", FileStreamSender(phone, desk, "127.0.0.1").send(second, offer.size, ByteArrayInputStream(bytes)))
            assertEquals("trip.mov", (next("event") { (it["value"] as? Map<*, *>)?.get("name") == "trip.mov" }["value"] as Map<*, *>)["name"])
            assertArrayEquals(bytes, File(root, "Downloads/trip.mov").readBytes())

            // Informational throughput for the same 6 MiB: one stream versus 512 KiB journaled chunks.
            val streamOffer = FileOffer(Ids.token(), "speed-stream.bin", bytes.size.toLong(), offer.sha256)
            var started = System.nanoTime()
            FileStreamSender(phone, desk, "127.0.0.1").send(Streams.fileSession(exchange.call(desk, "files.write", streamOffer.payload()), offer.size), offer.size, ByteArrayInputStream(bytes))
            val streamMs = (System.nanoTime() - started) / 1_000_000
            started = System.nanoTime()
            val chunkedTransfer = Ids.token()
            var position = 0
            while (position < bytes.size) {
                val end = minOf(bytes.size, position + 512 * 1024)
                val final = end == bytes.size
                assertTrue(exchange.call(desk, "files.write", mapOf(
                    "transfer" to chunkedTransfer, "name" to "speed-chunked.bin", "size" to bytes.size.toLong(), "offset" to position.toLong(),
                    "data" to java.util.Base64.getEncoder().encodeToString(bytes.copyOfRange(position, end)), "final" to final,
                    "sha256" to if (final) offer.sha256 else null,
                ), timeoutMs = 30_000).complete)
                position = end
            }
            val chunkedMs = (System.nanoTime() - started) / 1_000_000
            System.err.println("6 MiB phone->desktop on loopback: luma-files/1 $streamMs ms (includes offer), chunked files.write $chunkedMs ms")

            // luma-files/1 desktop to phone, interrupted and resumed.
            val outgoing = File(root, "desk-report.pdf").apply { writeBytes(ByteArray(5 * 1024 * 1024) { (it % 239).toByte() }) }
            val transfer = Ids.token()
            command(mapOf("send_stream" to mapOf("path" to outgoing.path, "transfer" to transfer, "interrupt_after" to 1_500_000L)))
            assertEquals("Cancelled", next("sent")["error"])
            command(mapOf("send_stream" to mapOf("path" to outgoing.path, "transfer" to transfer, "interrupt_after" to null)))
            val sent = next("sent")["sent"] as Map<String, Any?>?
            assertNotNull(sent)
            assertEquals("desk-report.pdf", sent!!["name"])
            assertTrue("desktop resumed at ${sent["offset"]}", (sent["offset"] as Long) > 0)
            assertArrayEquals(outgoing.readBytes(), File(root, "phone-downloads/desk-report.pdf").readBytes())
        } finally {
            receiving.closeAll()
            listener.stop()
            runCatching { command(mapOf("quit" to true)) }
            process.waitFor(10, TimeUnit.SECONDS)
            process.destroy()
            root.deleteRecursively()
        }
    }
}
