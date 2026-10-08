package org.projectluma.connect.protocol

import java.security.KeyFactory
import java.security.PrivateKey
import java.security.PublicKey
import java.security.Signature
import java.security.cert.X509Certificate
import java.security.interfaces.ECPublicKey
import java.util.Base64

/**
 * Certificate rotation (`device.rotate`), the Kotlin twin of `companion_rotation.py`.
 *
 * The rotating device calls `device.rotate` over the existing link, still authenticated
 * by its old certificate, with the new certificate and a proof signed by the NEW key
 * over `luma-rotate/1|<old pin>|<new pin>|<receiver pin>|<epoch>`.
 */
object Rotation {
    private const val PREFIX = "luma-rotate/1"

    fun message(oldPin: String, newPin: String, receiverPin: String, epoch: String): ByteArray {
        require(listOf(oldPin, newPin, receiverPin).all { Ids.DIGEST.matches(it) } && Ids.IDENTIFIER.matches(epoch))
        return "$PREFIX|$oldPin|$newPin|$receiverPin|$epoch".toByteArray(Charsets.US_ASCII)
    }

    fun sign(newKey: PrivateKey, message: ByteArray): String =
        Base64.getEncoder().encodeToString(Signature.getInstance("SHA256withECDSA").run { initSign(newKey); update(message); sign() })

    fun verify(certificate: X509Certificate, message: ByteArray, proof: String): Boolean {
        if (proof.length > 512) return false
        val signature = runCatching { Base64.getDecoder().decode(proof) }.getOrNull() ?: return false
        return runCatching {
            Signature.getInstance("SHA256withECDSA").run { initVerify(publicKeyOf(certificate)); update(message); verify(signature) }
        }.getOrDefault(false)
    }

    // Re-encode through the default provider so a Keystore-backed certificate verifies anywhere.
    private fun publicKeyOf(certificate: X509Certificate): PublicKey =
        KeyFactory.getInstance("EC").generatePublic(java.security.spec.X509EncodedKeySpec(certificate.publicKey.encoded))

    /** The same rules as `pairing._certificate`: currently valid P-256 leaf, CA:FALSE, digitalSignature, server+client auth. */
    fun checkCertificate(pem: String, pin: String): X509Certificate {
        val certificate = Certificates.fromPem(pem)
        certificate.checkValidity()
        val key = certificate.publicKey
        if (Transport.fingerprint(certificate) != pin) throw ProtocolException("pin does not match certificate")
        if (certificate.basicConstraints != -1) throw ProtocolException("device leaf certificate required")
        if (certificate.keyUsage?.getOrNull(0) != true) throw ProtocolException("device signing key required")
        val usages = certificate.extendedKeyUsage.orEmpty()
        if (!usages.containsAll(listOf("1.3.6.1.5.5.7.3.1", "1.3.6.1.5.5.7.3.2"))) throw ProtocolException("mutual TLS device certificate required")
        if (key !is ECPublicKey || key.params.curve.field.fieldSize < 256) throw ProtocolException("unsupported device key")
        return certificate
    }

    /**
     * Caller side: asks [peerPin] to trust [next] instead of [current]. Only after a
     * complete receipt carrying the new pin may the caller switch identities; until then
     * the old identity keeps working, so a failed attempt can simply be retried.
     */
    fun rotateSelf(current: DeviceIdentity, next: DeviceIdentity, journal: Journal, peerPin: String): Boolean {
        val peer = journal.peer(peerPin) ?: throw DeniedException("device is not paired")
        val proof = sign(next.privateKey, message(current.pin, next.pin, peerPin, peer.epoch))
        val receipt = Exchange(current, journal).call(peerPin, Capabilities.DEVICE_ROTATE, mapOf(
            "certificate" to next.pem, "pin" to next.pin, "proof" to proof,
        ))
        return receipt.complete && receipt.result?.get("pin") == next.pin
    }

    /** Receiver adapter: the paired device at [peerPin] replaces its certificate. */
    fun adapter(journal: Journal, receiverPin: () -> String): Adapter = { _, payload ->
        require(payload.keys == setOf("certificate", "pin", "proof"))
        val certificate = payload["certificate"] as? String ?: throw ProtocolException("certificate")
        val pin = payload["pin"] as? String ?: throw ProtocolException("pin")
        val proof = payload["proof"] as? String ?: throw ProtocolException("proof")
        val parsed = checkCertificate(certificate, pin)
        val caller = RotationContext.caller.get() ?: throw ProtocolException("no authenticated caller")
        val peer = journal.peer(caller) ?: throw SecurityException("device removed")
        if (!verify(parsed, message(caller, pin, receiverPin(), peer.epoch), proof)) throw ProtocolException("rotation proof does not match")
        journal.rotatePeer(caller, pin, certificate)
        mapOf("accepted" to true, "pin" to pin)
    }
}

/** The authenticated caller of the request being dispatched, set by [Receiver] for adapters that need it. */
object RotationContext {
    val caller = ThreadLocal<String?>()
}
