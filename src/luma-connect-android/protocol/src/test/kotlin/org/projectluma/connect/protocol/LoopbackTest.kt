package org.projectluma.connect.protocol

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class LoopbackTest {
    @Test
    fun kotlinPeersExchangeOverPinnedMutualTls() {
        val phone = TestIdentities.create()
        val desktop = TestIdentities.create()
        val epoch = Ids.token()
        val phoneJournal = Journal(InMemoryJournalStore())
        val desktopJournal = Journal(InMemoryJournalStore())
        val received = mutableListOf<Map<String, Any?>>()
        desktopJournal.approve(Peer(phone.pin, epoch, null, setOf("clipboard.write"), emptySet(), false, "Phone", phone.pem, null, null))
        val adapter: Adapter = { _, payload -> received += payload; mapOf("accepted" to true) }
        val listener = Listener(desktop, desktopJournal, Receiver(desktopJournal, mapOf("clipboard.write" to adapter)), 0)
        listener.start()
        try {
            phoneJournal.approve(Peer(desktop.pin, epoch, null, emptySet(), setOf("clipboard.write"), false, "Desk", desktop.pem, "127.0.0.1", listener.boundPort))
            val receipt = Exchange(phone, phoneJournal).call(desktop.pin, "clipboard.write", mapOf("text" to "hi"))
            assertTrue(receipt.complete)
            assertEquals(listOf(mapOf<String, Any?>("text" to "hi")), received)

            // A stranger with a well-formed certificate is refused during the handshake.
            val stranger = TestIdentities.create()
            val strangerJournal = Journal(InMemoryJournalStore())
            strangerJournal.approve(Peer(desktop.pin, Ids.token(), null, emptySet(), setOf("clipboard.write"), false, "Desk", desktop.pem, "127.0.0.1", listener.boundPort))
            assertThrows(Exception::class.java) { Exchange(stranger, strangerJournal).call(desktop.pin, "clipboard.write", mapOf("text" to "evil")) }
            assertEquals(1, received.size)

            // Revocation applies to the next connection.
            desktopJournal.revoke(phone.pin)
            assertThrows(Exception::class.java) { Exchange(phone, phoneJournal).call(desktop.pin, "clipboard.write", mapOf("text" to "late")) }
            assertEquals(1, received.size)
        } finally {
            listener.stop()
        }
    }
}
