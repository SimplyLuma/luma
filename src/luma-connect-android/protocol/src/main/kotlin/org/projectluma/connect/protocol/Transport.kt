package org.projectluma.connect.protocol

import java.io.DataInputStream
import java.io.EOFException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.security.MessageDigest
import java.security.Principal
import java.security.PrivateKey
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.KeyManager
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManager
import javax.net.ssl.X509ExtendedKeyManager
import javax.net.ssl.X509TrustManager

/** Framing and TLS rules shared with `luma_continuity/transport.py`. */
object Transport {
    const val ALPN = "luma-continuity/1"
    const val PAIRING_ALPN = "luma-companion-pairing/1"
    const val MAX_FRAME = 1024 * 1024
    const val FRAME_DEADLINE_MS = 20_000L

    fun fingerprint(certificate: X509Certificate): String = sha256Hex(certificate.encoded)

    fun sha256Hex(data: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }

    fun send(output: OutputStream, value: Map<String, Any?>) {
        val data = Json.encode(value)
        if (data.isEmpty() || data.size > MAX_FRAME) throw ProtocolException("frame size")
        val header = byteArrayOf(
            (data.size ushr 24).toByte(), (data.size ushr 16).toByte(),
            (data.size ushr 8).toByte(), data.size.toByte(),
        )
        output.write(header + data)
        output.flush()
    }

    /** Reads one frame. The caller's socket timeout bounds each read; the deadline bounds the frame. */
    fun receive(input: InputStream, socket: Socket? = null): Map<String, Any?> {
        val deadline = System.currentTimeMillis() + FRAME_DEADLINE_MS
        val header = readFully(input, 4, deadline, socket)
        val size = ((header[0].toInt() and 0xff) shl 24) or ((header[1].toInt() and 0xff) shl 16) or
            ((header[2].toInt() and 0xff) shl 8) or (header[3].toInt() and 0xff)
        if (size <= 0 || size > MAX_FRAME) throw ProtocolException("frame size")
        return Json.parseObject(readFully(input, size, deadline, socket))
    }

    private fun readFully(input: InputStream, count: Int, deadline: Long, socket: Socket?): ByteArray {
        val data = ByteArray(count)
        var offset = 0
        val stream = DataInputStream(input)
        while (offset < count) {
            val remaining = deadline - System.currentTimeMillis()
            if (remaining <= 0) throw SocketTimeoutException("frame deadline")
            socket?.soTimeout = remaining.coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
            val read = stream.read(data, offset, count - offset)
            if (read < 0) throw EOFException("truncated frame")
            offset += read
        }
        return data
    }

    /**
     * A TLS 1.3 context whose only trust decision is an exact leaf fingerprint.
     *
     * [approved] is consulted during every handshake so revocation applies to
     * the next connection without rebuilding the context.
     */
    fun context(identity: DeviceIdentity, approved: () -> Set<String>): SSLContext =
        SSLContext.getInstance("TLSv1.3").apply {
            init(arrayOf<KeyManager>(IdentityKeyManager(identity)), arrayOf<TrustManager>(PinnedTrustManager(approved)), SecureRandom())
        }

    /** Pairing server side of the desktop presents a certificate; the phone presents none. */
    fun pairingClientContext(expectedPin: String): SSLContext =
        SSLContext.getInstance("TLSv1.3").apply {
            init(null, arrayOf<TrustManager>(PinnedTrustManager { setOf(expectedPin) }), SecureRandom())
        }

    fun connect(context: SSLContext, host: String, port: Int, expectedPin: String, timeoutMs: Int = 10_000, alpn: String = ALPN): SSLSocket {
        val raw = Socket()
        try {
            raw.connect(InetSocketAddress(host, port), timeoutMs)
            raw.soTimeout = timeoutMs
            val socket = context.socketFactory.createSocket(raw, host, port, true) as SSLSocket
            configure(socket, alpn)
            socket.useClientMode = true
            socket.startHandshake()
            authenticate(socket, expectedPin, alpn)
            return socket
        } catch (error: Throwable) {
            runCatching { raw.close() }
            throw error
        }
    }

    fun listen(context: SSLContext, port: Int, backlog: Int = 4): SSLServerSocket =
        (context.serverSocketFactory.createServerSocket() as SSLServerSocket).apply {
            reuseAddress = true
            bind(InetSocketAddress(port), backlog)
            needClientAuth = true
            val parameters = sslParameters
            parameters.protocols = arrayOf("TLSv1.3")
            parameters.applicationProtocols = arrayOf(ALPN)
            parameters.needClientAuth = true
            sslParameters = parameters
        }

    fun accept(server: ServerSocket, timeoutMs: Int = 5_000, allowedSource: (java.net.InetAddress) -> Boolean = { true }): Pair<SSLSocket, String> {
        val socket = server.accept() as SSLSocket
        try {
            if (!allowedSource(socket.inetAddress)) throw DeniedException("connection from outside the local network")
            socket.soTimeout = timeoutMs
            // Conscrypt (Android) does not copy ALPN from the server socket to accepted sockets; set it per connection.
            val parameters = socket.sslParameters
            parameters.protocols = arrayOf("TLSv1.3")
            parameters.applicationProtocols = arrayOf(ALPN)
            parameters.needClientAuth = true
            socket.sslParameters = parameters
            socket.useClientMode = false
            socket.startHandshake()
            val session = socket.session
            val peer = session.peerCertificates.firstOrNull() as? X509Certificate
                ?: throw DeniedException("client certificate required")
            if (session.protocol != "TLSv1.3" || socket.applicationProtocol != ALPN) {
                throw DeniedException("unapproved device or protocol")
            }
            return socket to fingerprint(peer)
        } catch (error: Throwable) {
            runCatching { socket.close() }
            throw error
        }
    }

    private fun configure(socket: SSLSocket, alpn: String) {
        val parameters = socket.sslParameters
        parameters.protocols = arrayOf("TLSv1.3")
        parameters.applicationProtocols = arrayOf(alpn)
        // Identity is the approved leaf fingerprint, never a hostname.
        parameters.endpointIdentificationAlgorithm = null
        socket.sslParameters = parameters
    }

    fun authenticate(socket: SSLSocket, expectedPin: String, alpn: String = ALPN): X509Certificate {
        val session = socket.session
        val peer = session.peerCertificates.firstOrNull() as? X509Certificate
            ?: throw DeniedException("peer certificate required")
        val actual = fingerprint(peer)
        if (session.protocol != "TLSv1.3" || socket.applicationProtocol != alpn ||
            !MessageDigest.isEqual(actual.toByteArray(), expectedPin.toByteArray())
        ) {
            throw DeniedException("unapproved device or protocol")
        }
        return peer
    }
}

/** A private key (possibly hardware-backed and non-exportable) and its self-signed device certificate. */
class DeviceIdentity(val privateKey: PrivateKey, val certificate: X509Certificate) {
    val pin: String get() = Transport.fingerprint(certificate)
    val pem: String get() = Certificates.toPem(certificate)
}

private class IdentityKeyManager(private val identity: DeviceIdentity) : X509ExtendedKeyManager() {
    private val alias = "device"
    override fun getClientAliases(keyType: String?, issuers: Array<out Principal>?) = arrayOf(alias)
    override fun chooseClientAlias(keyType: Array<out String>?, issuers: Array<out Principal>?, socket: Socket?) = alias
    override fun getServerAliases(keyType: String?, issuers: Array<out Principal>?) = arrayOf(alias)
    override fun chooseServerAlias(keyType: String?, issuers: Array<out Principal>?, socket: Socket?) = alias
    override fun chooseEngineClientAlias(keyType: Array<out String>?, issuers: Array<out Principal>?, engine: javax.net.ssl.SSLEngine?) = alias
    override fun chooseEngineServerAlias(keyType: String?, issuers: Array<out Principal>?, engine: javax.net.ssl.SSLEngine?) = alias
    override fun getCertificateChain(alias: String?) = arrayOf(identity.certificate)
    override fun getPrivateKey(alias: String?) = identity.privateKey
}

/**
 * Accepts exactly one approved, currently valid leaf. System CAs are never consulted.
 * Chains longer than the leaf are ignored: the leaf itself is the trust anchor.
 */
class PinnedTrustManager(private val approved: () -> Set<String>) : X509TrustManager {
    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = check(chain)
    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) = check(chain)
    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()

    private fun check(chain: Array<out X509Certificate>?) {
        val leaf = chain?.firstOrNull() ?: throw CertificateException("certificate required")
        leaf.checkValidity()
        val pin = Transport.fingerprint(leaf)
        if (approved().none { MessageDigest.isEqual(it.toByteArray(), pin.toByteArray()) }) {
            throw CertificateException("unapproved device")
        }
    }
}
