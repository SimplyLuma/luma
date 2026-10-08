package org.projectluma.connect.protocol

import java.net.URI
import java.net.URLDecoder
import java.security.MessageDigest
import java.security.cert.X509Certificate

/**
 * Companion pairing, schema `org.projectluma.companion-pairing/v1` (ADR-021).
 *
 * The desktop shows a QR code carrying its full certificate pin and a
 * single-use secret. Scanning it is the out-of-band full-fingerprint
 * comparison the daemon requires: the phone refuses any other certificate,
 * and only that desktop can read the secret the phone returns inside TLS.
 * Both screens then show the same six-digit code as a visible confirmation.
 */
object Pairing {
    const val SCHEMA = "org.projectluma.companion-pairing/v1"
    const val SCHEME = "luma-connect"

    data class Invitation(val hosts: List<String>, val port: Int, val pin: String, val token: String, val name: String)

    data class Result(
        val peer: Peer,
        val sas: String,
        val desktopCertificate: X509Certificate,
    )

    fun parse(uri: String): Invitation {
        val parsed = try { URI(uri) } catch (_: Exception) { throw ProtocolException("not a Luma Connect code") }
        if (parsed.scheme != SCHEME || parsed.host != "pair" || parsed.rawQuery == null || uri.length > 1024) {
            throw ProtocolException("not a Luma Connect code")
        }
        val values = parsed.rawQuery.split('&').map {
            val (key, value) = it.split('=', limit = 2).let { parts -> parts[0] to parts.getOrElse(1) { "" } }
            key to URLDecoder.decode(value, "UTF-8")
        }
        fun one(key: String) = values.filter { it.first == key }.map { it.second }.singleOrNull()
            ?: throw ProtocolException("invalid Luma Connect code")
        if (one("v") != "1") throw ProtocolException("this code needs a newer Luma Connect")
        // A desktop's IPv6 scope ID means nothing on the phone; link-local addresses without one are unusable.
        val hosts = values.filter { it.first == "host" }.map { it.second.substringBefore('%') }
            .filterNot { it.lowercase().startsWith("fe80:") }
        val port = one("port").toIntOrNull()
        val pin = one("pin")
        val token = one("token")
        val name = one("name")
        if (hosts.isEmpty() || hosts.size > 4 || hosts.any { !isNumericAddress(it) } || port == null || port !in 1024..65535 ||
            !Ids.DIGEST.matches(pin) || !Ids.IDENTIFIER.matches(token) || name.isEmpty() || name.length > 128
        ) {
            throw ProtocolException("invalid Luma Connect code")
        }
        return Invitation(hosts, port, pin, token, name)
    }

    /** Six digits both devices compute independently from both pins, the epoch and the secret. */
    fun sas(desktopPin: String, phonePin: String, epoch: String, token: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest("luma-companion-sas/1|$desktopPin|$phonePin|$epoch|$token".toByteArray())
        val value = ((digest[0].toLong() and 0xff) shl 24) or ((digest[1].toLong() and 0xff) shl 16) or
            ((digest[2].toLong() and 0xff) shl 8) or (digest[3].toLong() and 0xff)
        return "%06d".format(value % 1_000_000)
    }

    /**
     * Runs the phone side. [requested] is what the phone asks to be allowed to
     * do on the desktop; [offered] is what the phone lets the desktop do.
     * The desktop may narrow both; it can never widen them.
     */
    fun pair(
        invitation: Invitation,
        identity: DeviceIdentity,
        journal: Journal,
        deviceName: String,
        model: String,
        listenPort: Int,
        requestedFeatures: Set<String>,
        offeredFeatures: Set<String>,
    ): Result {
        // Every pairing can rotate certificates in both directions; without it a pairing dies at expiry.
        val requested = requestedFeatures + Capabilities.DEVICE_ROTATE
        val offered = offeredFeatures + Capabilities.DEVICE_ROTATE
        require(Capabilities.ALL.containsAll(requested) && Capabilities.ALL.containsAll(offered))
        var lastError: Exception? = null
        for (host in invitation.hosts) {
            try {
                val context = Transport.pairingClientContext(invitation.pin)
                Transport.connect(context, host, invitation.port, invitation.pin, alpn = Transport.PAIRING_ALPN).use { socket ->
                    val desktopCertificate = socket.session.peerCertificates.first() as X509Certificate
                    Transport.send(
                        socket.outputStream,
                        mapOf(
                            "schema" to SCHEMA,
                            "kind" to "request",
                            "token" to invitation.token,
                            "certificate" to identity.pem,
                            "pin" to identity.pin,
                            "name" to deviceName.take(128),
                            "model" to model.take(128),
                            "platform" to "android",
                            "listen_port" to listenPort.toLong(),
                            "phone_to_desktop" to requested.sorted(),
                            "desktop_to_phone" to offered.sorted(),
                        ),
                    )
                    val response = Transport.receive(socket.inputStream, socket)
                    return accept(response, invitation, identity, journal, host, desktopCertificate, requested, offered)
                }
            } catch (error: DeniedException) {
                throw error
            } catch (error: Exception) {
                lastError = error
            }
        }
        throw ProtocolException("could not reach ${invitation.name}: ${lastError?.message ?: "no address"}")
    }

    @Suppress("UNCHECKED_CAST")
    private fun accept(
        response: Map<String, Any?>,
        invitation: Invitation,
        identity: DeviceIdentity,
        journal: Journal,
        host: String,
        desktopCertificate: X509Certificate,
        requested: Set<String>,
        offered: Set<String>,
    ): Result {
        if (response["kind"] == "rejected") throw DeniedException("${invitation.name} declined the pairing")
        val fields = setOf("schema", "kind", "epoch", "name", "listen_port", "phone_to_desktop", "desktop_to_phone", "sas")
        val epoch = response["epoch"]
        val port = response["listen_port"]
        val phoneToDesktop = (response["phone_to_desktop"] as? List<*>)?.map { it as? String }
        val desktopToPhone = (response["desktop_to_phone"] as? List<*>)?.map { it as? String }
        if (response.keys != fields || response["schema"] != SCHEMA || response["kind"] != "accepted" ||
            epoch !is String || !Ids.IDENTIFIER.matches(epoch) || port !is Long || port !in 1024L..65535L ||
            phoneToDesktop == null || phoneToDesktop.any { it == null } || desktopToPhone == null || desktopToPhone.any { it == null }
        ) {
            throw ProtocolException("invalid pairing response")
        }
        val outgoing = (phoneToDesktop as List<String>).toSet()
        val incoming = (desktopToPhone as List<String>).toSet()
        if (!requested.containsAll(outgoing) || !offered.containsAll(incoming)) {
            throw DeniedException("pairing response expands permissions")
        }
        val expectedSas = sas(invitation.pin, identity.pin, epoch, invitation.token)
        if (!MessageDigest.isEqual(expectedSas.toByteArray(), (response["sas"] as? String ?: "").toByteArray())) {
            throw DeniedException("pairing confirmation code mismatch")
        }
        val peer = Peer(
            pin = invitation.pin,
            epoch = epoch,
            account = null,
            incoming = incoming,
            outgoing = outgoing,
            revoked = false,
            name = (response["name"] as? String)?.take(128) ?: invitation.name,
            certificatePem = Certificates.toPem(desktopCertificate),
            host = host,
            port = port.toInt(),
        )
        journal.approve(peer)
        return Result(peer, expectedSas, desktopCertificate)
    }

    private fun isNumericAddress(value: String): Boolean =
        Regex("""\d{1,3}(\.\d{1,3}){3}""").matches(value) && value.split('.').all { it.toInt() <= 255 } ||
            (value.contains(':') && Regex("""[0-9a-fA-F:%.a-zA-Z0-9]+""").matches(value) && value.length <= 64)
}
