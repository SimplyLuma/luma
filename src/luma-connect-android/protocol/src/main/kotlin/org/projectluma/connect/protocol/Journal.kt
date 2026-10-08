package org.projectluma.connect.protocol

import java.security.SecureRandom

/** One approved peer, mirroring the daemon's `peers` row. */
data class Peer(
    val pin: String,
    val epoch: String,
    val account: String?,
    /** Capabilities the peer may invoke on this device. */
    val incoming: Set<String>,
    /** Capabilities this device may invoke on the peer. */
    val outgoing: Set<String>,
    val revoked: Boolean,
    val name: String,
    val certificatePem: String,
    val host: String?,
    val port: Int?,
)

data class RequestRecord(val digest: String, val state: String, val response: String?, val expires: Long)

/**
 * Durable storage. [transaction] must be serializable and atomic: the Android
 * implementation uses one SQLite `BEGIN IMMEDIATE` transaction.
 */
interface JournalStore {
    fun <T> transaction(block: () -> T): T
    fun enabled(): Boolean
    fun peer(pin: String): Peer?
    fun peers(): List<Peer>
    fun putPeer(peer: Peer)
    fun deletePeer(pin: String)
    fun epochUsed(pin: String, epoch: String): Boolean
    fun recordEpoch(pin: String, epoch: String)
    fun request(pin: String, epoch: String, id: String): RequestRecord?
    fun insertRequest(pin: String, epoch: String, id: String, record: RequestRecord)
    fun completeRequest(pin: String, epoch: String, id: String, response: String)
    fun requestCount(pin: String): Int
    /** Deletes tombstones whose request already expired; replay of those is rejected by the expiry check. */
    fun pruneRequests(now: Long): Int
}

object Ids {
    val IDENTIFIER = Regex("[0-9a-f]{32}")
    val DIGEST = Regex("[0-9a-f]{64}")
    private val random = SecureRandom()

    fun token(): String = ByteArray(16).also(random::nextBytes).joinToString("") { "%02x".format(it) }
}

/**
 * Receiver authorization and at-most-once dispatch, the Kotlin twin of
 * `policy.Journal.dispatch`. A crash between an effect and its receipt stays
 * UNKNOWN; nothing is retried automatically.
 */
class Journal(private val store: JournalStore, private val clock: () -> Long = { System.currentTimeMillis() / 1000 }) {
    fun approve(peer: Peer) = store.transaction {
        if (!Ids.DIGEST.matches(peer.pin) || !Ids.IDENTIFIER.matches(peer.epoch) ||
            !Capabilities.ALL.containsAll(peer.incoming) || !Capabilities.ALL.containsAll(peer.outgoing) ||
            (peer.incoming.isEmpty() && peer.outgoing.isEmpty())
        ) {
            throw ProtocolException("invalid approval")
        }
        // Re-pairing must create a fresh epoch; old requests cannot become valid again.
        if (store.epochUsed(peer.pin, peer.epoch)) throw ProtocolException("pairing requires a fresh epoch")
        store.recordEpoch(peer.pin, peer.epoch)
        store.putPeer(peer.copy(revoked = false))
    }

    fun revoke(pin: String) = store.transaction {
        store.peer(pin)?.let { store.putPeer(it.copy(revoked = true)) }
    }

    fun setGrants(pin: String, incoming: Set<String>) = store.transaction {
        if (!Capabilities.ALL.containsAll(incoming)) throw ProtocolException("invalid capability")
        val current = store.peer(pin) ?: return@transaction
        if (!current.revoked) store.putPeer(current.copy(incoming = incoming))
    }

    /**
     * Moves a peer to its replacement certificate, keeping epoch, account and grants
     * (`device.rotate`). The old pin stops being trusted in the same transaction.
     */
    fun rotatePeer(oldPin: String, newPin: String, certificatePem: String) = store.transaction {
        if (!Ids.DIGEST.matches(oldPin) || !Ids.DIGEST.matches(newPin) || oldPin == newPin) throw ProtocolException("invalid rotation")
        val current = store.peer(oldPin)
        if (current == null || current.revoked) throw DeniedException("device is not approved")
        if (store.peer(newPin) != null) throw DeniedException("replacement certificate is already paired")
        if (!store.epochUsed(newPin, current.epoch)) store.recordEpoch(newPin, current.epoch)
        store.deletePeer(oldPin)
        store.putPeer(current.copy(pin = newPin, certificatePem = certificatePem))
    }

    fun updateRoute(pin: String, host: String?, port: Int?) = store.transaction {
        store.peer(pin)?.let { store.putPeer(it.copy(host = host, port = port)) }
    }

    fun peer(pin: String): Peer? = store.peer(pin)

    fun activePeers(): List<Peer> = store.peers().filter { !it.revoked }

    fun approvedPins(): Set<String> = activePeers().map { it.pin }.toSet()

    /** Returns `{state, result}`; throws [DeniedException] for authorization failures. */
    fun dispatch(peerPin: String, request: Map<String, Any?>, handler: (String, Map<String, Any?>) -> Map<String, Any?>): Map<String, Any?> {
        val now = clock()
        val payload = validate(request, now)
        val epoch = request["epoch"] as String
        val id = request["id"] as String
        val capability = request["capability"] as String
        val digest = Transport.sha256Hex(Json.encode(request))

        val previous = store.transaction {
            if (!store.enabled()) throw DeniedException("continuity disabled")
            authorize(peerPin, request)
            val existing = store.request(peerPin, epoch, id)
            if (existing != null) {
                if (existing.digest != digest) throw DeniedException("request identity reused with different content")
                return@transaction existing
            }
            if (store.requestCount(peerPin) >= 10_000) {
                store.pruneRequests(now)
                if (store.requestCount(peerPin) >= 10_000) throw DeniedException("request quota exhausted; local maintenance required")
            }
            // Persist uncertainty before invoking any side effect.
            store.insertRequest(peerPin, epoch, id, RequestRecord(digest, "unknown", null, request["expires"] as Long))
            null
        }
        if (previous != null) {
            return mapOf("state" to previous.state, "result" to previous.response?.let { Json.parse(it) })
        }
        return store.transaction {
            if (!store.enabled()) throw DeniedException("continuity disabled")
            authorize(peerPin, request)
            val result = handler(capability, payload)
            store.completeRequest(peerPin, epoch, id, Json.encodeToString(result))
            mapOf("state" to "complete", "result" to result)
        }
    }

    private fun authorize(peerPin: String, request: Map<String, Any?>) {
        val peer = store.peer(peerPin)
        if (peer == null || peer.revoked || peer.epoch != request["epoch"] || peer.account != request["account"] ||
            request["capability"] !in peer.incoming
        ) {
            throw DeniedException("device, account or capability not approved")
        }
    }

    @Suppress("UNCHECKED_CAST")
    private fun validate(request: Map<String, Any?>, now: Long): Map<String, Any?> {
        val fields = setOf("version", "epoch", "id", "account", "capability", "expires", "payload")
        val epoch = request["epoch"]
        val id = request["id"]
        val capability = request["capability"]
        val expires = request["expires"]
        val payload = request["payload"]
        if (request.keys != fields || request["version"] != 1L ||
            epoch !is String || !Ids.IDENTIFIER.matches(epoch) ||
            id !is String || !Ids.IDENTIFIER.matches(id) ||
            capability !is String || capability !in Capabilities.ALL ||
            expires !is Long || expires <= now || expires > now + 86_400 ||
            payload !is Map<*, *> || (request["account"] != null && request["account"] !is String)
        ) {
            throw DeniedException("invalid or expired request")
        }
        return payload as Map<String, Any?>
    }
}

/** Builds requests in the exact shape `policy.Journal.dispatch` accepts. */
object Requests {
    fun create(peer: Peer, capability: String, payload: Map<String, Any?>, lifetimeSeconds: Long = 120, now: Long = System.currentTimeMillis() / 1000): Map<String, Any?> {
        require(capability in peer.outgoing) { "capability not granted by peer" }
        require(lifetimeSeconds in 1..86_400)
        return mapOf(
            "version" to 1L,
            "epoch" to peer.epoch,
            "id" to Ids.token(),
            "account" to peer.account,
            "capability" to capability,
            "expires" to now + lifetimeSeconds,
            "payload" to payload,
        )
    }
}

class InMemoryJournalStore : JournalStore {
    private val lock = Any()
    private var enabled = true
    private val peers = HashMap<String, Peer>()
    private val epochs = HashSet<Pair<String, String>>()
    private val requests = HashMap<Triple<String, String, String>, RequestRecord>()

    fun setEnabled(value: Boolean) = synchronized(lock) { enabled = value }

    override fun <T> transaction(block: () -> T): T = synchronized(lock) { block() }
    override fun enabled() = enabled
    override fun peer(pin: String) = peers[pin]
    override fun peers() = peers.values.toList()
    override fun putPeer(peer: Peer) { peers[peer.pin] = peer }
    override fun deletePeer(pin: String) { peers.remove(pin) }
    override fun epochUsed(pin: String, epoch: String) = (pin to epoch) in epochs
    override fun recordEpoch(pin: String, epoch: String) { epochs += pin to epoch }
    override fun request(pin: String, epoch: String, id: String) = requests[Triple(pin, epoch, id)]
    override fun insertRequest(pin: String, epoch: String, id: String, record: RequestRecord) { requests[Triple(pin, epoch, id)] = record }
    override fun completeRequest(pin: String, epoch: String, id: String, response: String) {
        val key = Triple(pin, epoch, id)
        requests[key] = requests.getValue(key).copy(state = "complete", response = response)
    }
    override fun requestCount(pin: String) = requests.keys.count { it.first == pin }
    override fun pruneRequests(now: Long): Int {
        val expired = requests.filterValues { it.expires < now }.keys
        expired.forEach(requests::remove)
        return expired.size
    }
}
