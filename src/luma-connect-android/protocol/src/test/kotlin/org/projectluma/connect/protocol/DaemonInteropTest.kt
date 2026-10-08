package org.projectluma.connect.protocol

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.security.MessageDigest
import java.util.Base64
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * Pairs this Kotlin implementation with the real Python daemon modules.
 *
 * Opt-in: set LUMA_INTEROP_PYTHON to an interpreter with `cryptography` and
 * LUMA_INTEROP_SOURCE to `src/luma-continuity`. All state is synthetic and
 * lives in a temporary directory on loopback.
 */
class DaemonInteropTest {
    private val python = System.getProperty("luma.interop.python").orEmpty()
    private val source = System.getProperty("luma.interop.source").orEmpty()

    @Suppress("UNCHECKED_CAST")
    @Test
    fun pairsAndExchangesWithPythonDaemon() {
        assumeTrue("set LUMA_INTEROP_PYTHON and LUMA_INTEROP_SOURCE", python.isNotEmpty() && source.isNotEmpty())
        val root = Files.createTempDirectory("luma-interop").toFile()
        val process = ProcessBuilder(python, File(source, "tests/companion_peer.py").path, root.path)
            .apply { environment()["PYTHONPATH"] = source }
            .redirectError(ProcessBuilder.Redirect.INHERIT)
            .start()
        val lines = LinkedBlockingQueue<Map<String, Any?>>()
        Thread {
            process.inputStream.bufferedReader().useLines { sequence -> sequence.forEach { lines.put(Json.parse(it) as Map<String, Any?>) } }
        }.apply { isDaemon = true; start() }
        fun next(key: String): Map<String, Any?> {
            while (true) {
                val line = lines.poll(20, TimeUnit.SECONDS) ?: error("daemon did not report $key")
                if (key in line) return line
            }
        }
        fun command(value: Map<String, Any?>) {
            process.outputStream.write(Json.encode(value) + '\n'.code.toByte())
            process.outputStream.flush()
        }

        val phone = TestIdentities.create()
        val journal = Journal(InMemoryJournalStore())
        val ringing = mutableListOf<Map<String, Any?>>()
        val clipboard = mutableListOf<String>()
        val receiver = Receiver(journal, mapOf(
            Capabilities.DEVICE_ROTATE to Rotation.adapter(journal) { phone.pin },
            Capabilities.DEVICE_RING to { _, payload -> ringing += payload; mapOf("ringing" to true) },
            Capabilities.CLIPBOARD_WRITE to { _, payload -> clipboard += payload["text"] as String; mapOf("accepted" to true) },
        ))
        val listener = Listener(phone, journal, receiver, 0)
        listener.start()
        try {
            val invitation = Pairing.parse(next("uri")["uri"] as String)
            val requested = setOf("device.status", "clipboard.write", "notifications.mirror", "files.write", "links.open", "input.control", "device.rotate")
            val offered = setOf("device.ring", "clipboard.write", "screen.control", "device.rotate")
            val result = Pairing.pair(invitation, phone, journal, "Pixel Test", "Google Pixel 9", listener.boundPort, requested, offered)
            val paired = next("paired")["paired"] as Map<String, Any?>
            assertEquals(result.sas, paired["sas"])
            assertEquals(phone.pin, paired["fingerprint"])
            // The desktop narrowed both directions to what its owner selected.
            assertEquals(setOf("device.status", "clipboard.write", "notifications.mirror", "files.write", "links.open", "device.rotate"), result.peer.outgoing)
            assertEquals(setOf("device.ring", "clipboard.write", "device.rotate"), result.peer.incoming)

            val exchange = Exchange(phone, journal)
            val desk = result.peer.pin
            assertTrue(exchange.call(desk, "device.status", mapOf("battery" to 81L, "charging" to true, "network" to "wifi", "listen_port" to listener.boundPort.toLong())).complete)
            assertEquals("status", next("event")["value"])

            assertTrue(exchange.call(desk, "clipboard.write", mapOf("text" to "from phone " + 0x2713.toChar(), "sensitive" to false)).complete)
            assertEquals("from phone " + 0x2713.toChar(), (next("event")["value"] as Map<*, *>)["text"])

            val key = Ids.token()
            val notification = mapOf(
                "op" to "post", "key" to key, "app" to "Messages", "package" to "com.google.android.apps.messaging",
                "title" to "Sam", "text" to "Running late", "when" to 1_757_000_000_000L, "silent" to false,
                "actions" to listOf(mapOf("id" to Ids.token(), "label" to "Reply", "reply" to true)), "conversation" to null,
            )
            assertTrue(exchange.call(desk, "notifications.mirror", notification).complete)
            assertEquals(key, (next("event")["value"] as Map<*, *>)["key"])

            val bytes = ByteArray(900_000) { (it % 251).toByte() }
            val transfer = Ids.token()
            val digest = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
            var offset = 0
            while (offset < bytes.size) {
                val end = minOf(bytes.size, offset + 512 * 1024)
                val final = end == bytes.size
                val receipt = exchange.call(desk, "files.write", mapOf(
                    "transfer" to transfer, "name" to "report.pdf", "size" to bytes.size.toLong(), "offset" to offset.toLong(),
                    "data" to Base64.getEncoder().encodeToString(bytes.copyOfRange(offset, end)), "final" to final,
                    "sha256" to if (final) digest else null,
                ))
                assertTrue(receipt.error ?: "", receipt.complete)
                offset = end
            }
            assertEquals("report.pdf", (next("event")["value"] as Map<*, *>)["name"])
            assertTrue(File(root, "Downloads/report.pdf").readBytes().contentEquals(bytes))

            // Desktop to phone, through the phone's own listener.
            command(mapOf("call" to "device.ring", "payload" to mapOf("ring" to true)))
            assertEquals("complete", (next("receipt")["receipt"] as Map<*, *>)["state"])
            assertEquals(listOf(mapOf<String, Any?>("ring" to true)), ringing)
            command(mapOf("call" to "clipboard.write", "payload" to mapOf("text" to "from desk")))
            assertEquals("complete", (next("receipt")["receipt"] as Map<*, *>)["state"])
            assertEquals(listOf("from desk"), clipboard)

            // A capability the phone never offered is refused by the desktop before sending.
            command(mapOf("call" to "screen.control", "payload" to mapOf<String, Any?>()))
            assertTrue((next("receipt")["error"] as String).startsWith("Denied"))

            // The desktop rotates its certificate; the phone moves the pairing to the new pin.
            command(mapOf("rotate" to true))
            val rotated = next("rotated")["rotated"] as Map<String, Any?>
            assertEquals(true, rotated["rotated"])
            val newDesk = rotated["pin"] as String
            assertEquals(null, journal.peer(desk))
            assertEquals(setOf("device.status", "clipboard.write", "notifications.mirror", "files.write", "links.open", "device.rotate"), journal.peer(newDesk)!!.outgoing)
            command(mapOf("call" to "device.ring", "payload" to mapOf("ring" to false)))
            assertEquals("complete", (next("receipt")["receipt"] as Map<*, *>)["state"])
            assertTrue(Exchange(phone, journal).call(newDesk, "clipboard.write", mapOf("text" to "after desktop rotation")).complete)
            assertEquals("after desktop rotation", (next("event")["value"] as Map<*, *>)["text"])

            // The phone rotates its own certificate; the old one is refused afterwards.
            val nextPhone = TestIdentities.create(days = 398)
            assertTrue(Rotation.rotateSelf(phone, nextPhone, journal, newDesk))
            assertEquals(nextPhone.pin, next("event")["value"])
            assertTrue(Exchange(nextPhone, journal).call(newDesk, "clipboard.write", mapOf("text" to "after phone rotation")).complete)
            assertEquals("after phone rotation", (next("event")["value"] as Map<*, *>)["text"])
            assertTrue(runCatching { Exchange(phone, journal).call(newDesk, "clipboard.write", mapOf("text" to "stale")) }.isFailure)
        } finally {
            listener.stop()
            runCatching { command(mapOf("quit" to true)) }
            process.waitFor(10, TimeUnit.SECONDS)
            process.destroy()
            root.deleteRecursively()
        }
    }
}
