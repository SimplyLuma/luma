package org.projectluma.connect.protocol

import java.io.EOFException
import java.net.ServerSocket
import java.net.SocketException
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.SSLException

data class Receipt(val state: String, val result: Map<String, Any?>?) {
    val complete: Boolean get() = state == "complete" && result?.containsKey("error") != true
    val error: String? get() = result?.get("error") as? String
}

/**
 * Caller side of `local.ScopedExchange`: one mutually authenticated TLS
 * connection per request, grants checked before sending and again on receipt.
 * It never retries; the owner decides whether a new operation is appropriate.
 */
class Exchange(private val identity: DeviceIdentity, private val journal: Journal) {
    @Suppress("UNCHECKED_CAST")
    fun call(peerPin: String, capability: String, payload: Map<String, Any?>, lifetimeSeconds: Long = 120, timeoutMs: Int = 10_000): Receipt {
        val peer = admitted(peerPin, capability)
        val host = peer.host ?: throw ProtocolException("no route to device")
        val port = peer.port ?: throw ProtocolException("no route to device")
        val request = Requests.create(peer, capability, payload, lifetimeSeconds)
        val context = Transport.context(identity) { setOf(peerPin) }
        Transport.connect(context, host, port, peerPin, timeoutMs).use { socket ->
            admitted(peerPin, capability)
            Transport.send(socket.outputStream, request)
            val response = Transport.receive(socket.inputStream, socket)
            // Results revoked while in flight are not admitted.
            admitted(peerPin, capability)
            val state = response["state"]
            val result = response["result"]
            if (response.keys != setOf("state", "result") || state !in setOf("complete", "unknown", "denied", "invalid") ||
                (result != null && result !is Map<*, *>)
            ) {
                throw ProtocolException("invalid paired-device receipt")
            }
            return Receipt(state as String, result as Map<String, Any?>?)
        }
    }

    private fun admitted(peerPin: String, capability: String): Peer {
        val peer = journal.peer(peerPin)
        if (peer == null || peer.revoked || capability !in peer.outgoing) {
            throw DeniedException("selected device or capability is no longer approved")
        }
        return peer
    }
}

typealias Adapter = (capability: String, payload: Map<String, Any?>) -> Map<String, Any?>

/**
 * Receiver side of `session.Receiver`. Adapter failures map to the same
 * receipts the daemon returns; error text never crosses the wire.
 */
class Receiver(private val journal: Journal, private val adapters: Map<String, Adapter>) {
    fun handle(peerPin: String, request: Map<String, Any?>): Map<String, Any?> {
        RotationContext.caller.set(peerPin)
        try {
            return dispatch(peerPin, request)
        } finally {
            RotationContext.caller.remove()
        }
    }

    private fun dispatch(peerPin: String, request: Map<String, Any?>): Map<String, Any?> =
        journal.dispatch(peerPin, request) { capability, payload ->
            val adapter = adapters[capability] ?: return@dispatch mapOf("error" to "unavailable")
            var effective = payload
            if (capability in Capabilities.OPERATION_BOUND) {
                if ("operation" in payload) return@dispatch mapOf("error" to "invalid-request")
                effective = payload + ("operation" to request["id"])
            }
            try {
                adapter(capability, effective)
            } catch (_: IllegalArgumentException) {
                mapOf("error" to "invalid-request")
            } catch (_: ProtocolException) {
                mapOf("error" to "invalid-request")
            } catch (_: SecurityException) {
                mapOf("error" to "revoked")
            }
        }

    fun serveOne(socket: javax.net.ssl.SSLSocket, peerPin: String) {
        val response = try {
            handle(peerPin, Transport.receive(socket.inputStream, socket))
        } catch (_: DeniedException) {
            mapOf("state" to "denied", "result" to null)
        } catch (_: ProtocolException) {
            mapOf("state" to "invalid", "result" to null)
        } catch (_: EOFException) {
            mapOf("state" to "invalid", "result" to null)
        } catch (_: Exception) {
            mapOf("state" to "unknown", "result" to null)
        }
        Transport.send(socket.outputStream, response)
    }
}

/** A bounded accept loop for paired devices only. The TLS trust manager rejects everyone else. */
class Listener(
    private val identity: DeviceIdentity,
    private val journal: Journal,
    private val receiver: Receiver,
    private val port: Int,
) {
    private val stopped = AtomicBoolean(false)
    // A slow or stalled peer must not block the others; four concurrent requests is ample for one computer.
    private val workers = java.util.concurrent.ThreadPoolExecutor(
        1, 4, 30, java.util.concurrent.TimeUnit.SECONDS, java.util.concurrent.ArrayBlockingQueue(16),
        java.util.concurrent.ThreadPoolExecutor.DiscardPolicy(),
    )
    @Volatile private var server: ServerSocket? = null
    @Volatile var boundPort: Int = 0
        private set

    fun start(): Thread {
        val context = Transport.context(identity) { journal.approvedPins() }
        val socket = Transport.listen(context, port)
        server = socket
        boundPort = socket.localPort
        return Thread({
            while (!stopped.get()) {
                val accepted = try {
                    Transport.accept(socket, allowedSource = ::isLocalAddress)
                } catch (_: SocketException) {
                    if (stopped.get()) break else continue
                } catch (_: SSLException) {
                    continue
                } catch (_: ProtocolException) {
                    continue
                } catch (_: java.io.IOException) {
                    continue
                }
                val (stream, pin) = accepted
                workers.execute {
                    stream.use {
                        runCatching { if (!stopped.get()) receiver.serveOne(it, pin) }
                    }
                }
            }
            workers.shutdown()
        }, "luma-connect-listener").apply { isDaemon = true; start() }
    }

    fun stop() {
        stopped.set(true)
        runCatching { server?.close() }
    }
}

/**
 * Paired devices only ever reach each other over a local network or a VPN
 * overlay. Anything else (for example a public address on mobile data) is
 * dropped before the TLS handshake.
 */
fun isLocalAddress(address: java.net.InetAddress): Boolean {
    if (address.isLoopbackAddress || address.isLinkLocalAddress || address.isSiteLocalAddress) return true
    val bytes = address.address
    if (bytes.size == 4) {
        // 100.64.0.0/10 carrier-grade NAT, used by Tailscale and similar overlays.
        return (bytes[0].toInt() and 0xff) == 100 && (bytes[1].toInt() and 0xc0) == 64
    }
    // fc00::/7 unique local IPv6.
    return (bytes[0].toInt() and 0xfe) == 0xfc
}
