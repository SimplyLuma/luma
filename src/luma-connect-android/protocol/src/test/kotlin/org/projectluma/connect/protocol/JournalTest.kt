package org.projectluma.connect.protocol

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class JournalTest {
    private val now = 2_000_000_000L
    private val pin = "a".repeat(64)
    private val epoch = "b".repeat(32)

    private fun journal(): Pair<Journal, InMemoryJournalStore> {
        val store = InMemoryJournalStore()
        val journal = Journal(store) { now }
        journal.approve(Peer(pin, epoch, null, setOf("device.ring"), setOf("clipboard.write"), false, "Desk", "", "127.0.0.1", 1234))
        return journal to store
    }

    private fun request(id: String = Ids.token(), capability: String = "device.ring", expires: Long = now + 60) = mapOf(
        "version" to 1L, "epoch" to epoch, "id" to id, "account" to null,
        "capability" to capability, "expires" to expires, "payload" to mapOf<String, Any?>(),
    )

    @Test
    fun dispatchesOnceAndReplaysReceipt() {
        val (journal, _) = journal()
        var effects = 0
        val request = request()
        val first = journal.dispatch(pin, request) { _, _ -> effects++; mapOf("rang" to true) }
        val second = journal.dispatch(pin, request) { _, _ -> effects++; mapOf("rang" to true) }
        assertEquals(1, effects)
        assertEquals(first, second)
    }

    @Test
    fun reusedIdentityWithDifferentContentIsDenied() {
        val (journal, _) = journal()
        val id = Ids.token()
        journal.dispatch(pin, request(id)) { _, _ -> mapOf() }
        assertThrows(DeniedException::class.java) { journal.dispatch(pin, request(id, expires = now + 61)) { _, _ -> mapOf() } }
    }

    @Test
    fun ungrantedExpiredRevokedAndDisabledAreDenied() {
        val (journal, store) = journal()
        assertThrows(DeniedException::class.java) { journal.dispatch(pin, request(capability = "clipboard.write")) { _, _ -> mapOf() } }
        assertThrows(DeniedException::class.java) { journal.dispatch(pin, request(expires = now)) { _, _ -> mapOf() } }
        assertThrows(DeniedException::class.java) { journal.dispatch(pin, request(expires = now + 86_401)) { _, _ -> mapOf() } }
        store.setEnabled(false)
        assertThrows(DeniedException::class.java) { journal.dispatch(pin, request()) { _, _ -> mapOf() } }
        store.setEnabled(true)
        journal.revoke(pin)
        assertThrows(DeniedException::class.java) { journal.dispatch(pin, request()) { _, _ -> mapOf() } }
    }

    @Test
    fun crashDuringEffectLeavesUnknownReceipt() {
        val (journal, _) = journal()
        val request = request()
        assertThrows(IllegalStateException::class.java) { journal.dispatch(pin, request) { _, _ -> error("power loss") } }
        val replay = journal.dispatch(pin, request) { _, _ -> mapOf("rang" to true) }
        assertEquals("unknown", replay["state"])
    }

    @Test
    fun rePairingRequiresFreshEpoch() {
        val (journal, _) = journal()
        assertThrows(ProtocolException::class.java) {
            journal.approve(Peer(pin, epoch, null, setOf("device.ring"), emptySet(), false, "Desk", "", null, null))
        }
    }

    @Test
    fun localAddressFilterAcceptsLanAndOverlaysOnly() {
        fun ip(value: String) = java.net.InetAddress.getByName(value)
        listOf("192.168.1.2", "10.0.0.5", "172.20.1.1", "127.0.0.1", "169.254.3.3", "100.101.102.103", "fd12::1", "fe80::1", "::1")
            .forEach { assertEquals(it, true, isLocalAddress(ip(it))) }
        listOf("8.8.8.8", "100.128.0.1", "2001:4860:4860::8888", "203.0.113.9")
            .forEach { assertEquals(it, false, isLocalAddress(ip(it))) }
    }

    @Test
    fun certificatesAreLocaleSafeAndBackdated() {
        val previous = java.util.Locale.getDefault()
        try {
            for (tag in listOf("ar", "fa", "th-TH-u-ca-buddhist")) {
                java.util.Locale.setDefault(java.util.Locale.forLanguageTag(tag))
                val identity = TestIdentities.create()
                identity.certificate.checkValidity(java.util.Date(System.currentTimeMillis() - 86_400_000L))
                assertEquals(64, identity.pin.length)
            }
        } finally {
            java.util.Locale.setDefault(previous)
        }
    }

    @Test
    fun rotationAdapterMovesPeerOnlyWithValidProof() {
        val receiver = TestIdentities.create()
        val caller = TestIdentities.create()
        val replacement = TestIdentities.create()
        val store = InMemoryJournalStore()
        val journal = Journal(store)
        journal.approve(Peer(caller.pin, epoch, null, setOf("device.rotate", "device.ring"), emptySet(), false, "Desk", caller.pem, null, null))
        val adapter = Rotation.adapter(journal) { receiver.pin }
        RotationContext.caller.set(caller.pin)
        try {
            val badProof = Rotation.sign(caller.privateKey, Rotation.message(caller.pin, replacement.pin, receiver.pin, epoch))
            assertThrows(ProtocolException::class.java) { adapter("device.rotate", mapOf("certificate" to replacement.pem, "pin" to replacement.pin, "proof" to badProof)) }
            val proof = Rotation.sign(replacement.privateKey, Rotation.message(caller.pin, replacement.pin, receiver.pin, epoch))
            assertThrows(ProtocolException::class.java) { adapter("device.rotate", mapOf("certificate" to replacement.pem, "pin" to "0".repeat(64), "proof" to proof)) }
            assertEquals(mapOf("accepted" to true, "pin" to replacement.pin), adapter("device.rotate", mapOf("certificate" to replacement.pem, "pin" to replacement.pin, "proof" to proof)))
        } finally {
            RotationContext.caller.remove()
        }
        assertEquals(null, journal.peer(caller.pin))
        assertEquals(setOf("device.rotate", "device.ring"), journal.peer(replacement.pin)!!.incoming)
        assertEquals(replacement.pem, journal.peer(replacement.pin)!!.certificatePem)
    }

    @Test
    fun parsesInvitationAndRejectsTampering() {
        val uri = "luma-connect://pair?v=1&host=192.168.1.20&host=fe80%3A%3A1&port=40123&pin=$pin&token=$epoch&name=Nick%E2%80%99s%20Desk"
        val invitation = Pairing.parse(uri)
        assertEquals(listOf("192.168.1.20"), invitation.hosts)
        assertEquals("Nick" + 0x2019.toChar() + "s Desk", invitation.name)
        assertThrows(ProtocolException::class.java) { Pairing.parse(uri.replace("v=1", "v=2")) }
        assertThrows(ProtocolException::class.java) { Pairing.parse(uri.replace("host=192.168.1.20", "host=evil.example")) }
        assertThrows(ProtocolException::class.java) { Pairing.parse(uri.replace("pin=$pin", "pin=abc")) }
        assertThrows(ProtocolException::class.java) { Pairing.parse("https://pair?v=1") }
    }
}
