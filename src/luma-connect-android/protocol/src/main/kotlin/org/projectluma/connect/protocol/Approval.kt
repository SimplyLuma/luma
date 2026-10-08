package org.projectluma.connect.protocol

import java.security.KeyFactory
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.X509EncodedKeySpec
import java.util.Base64

/**
 * "Approve with your phone": the pure part shared by the Android app and its
 * tests, matching `luma_continuity.companion_approval` byte for byte.
 *
 * The computer sends `auth.request`:
 * `{request: 32 hex, reason: str ≤200, app: str ≤64, challenge: base64 of 32 bytes, expires: int}`.
 * The phone answers later with `auth.response`:
 * `{request, approved: bool, signature: base64 DER ECDSA | null, public_key: base64 SPKI}`.
 *
 * The signature is ECDSA P-256 with SHA-256 (Java `SHA256withECDSA`, Python
 * `ec.ECDSA(hashes.SHA256())`) over the UTF-8 bytes of [message]. The digest is
 * computed once, by the signature algorithm; the message is not pre-hashed.
 */
object Approval {
    const val DOMAIN = "luma-approval/1"
    const val MAX_REASON = 200
    const val MAX_APP = 64
    const val CHALLENGE_BYTES = 32
    /** The computer never asks for longer than this. */
    const val MAX_LIFETIME_SECONDS = 60L
    /** Tolerated disagreement between the two clocks when checking `expires`. */
    const val CLOCK_SKEW_SECONDS = 30L

    data class Request(val request: String, val reason: String, val app: String, val challenge: String, val expires: Long)

    /** Validates an `auth.request` payload. Throws [ProtocolException] for anything unexpected. */
    fun parseRequest(payload: Map<String, Any?>, nowSeconds: Long): Request {
        if (payload.keys != setOf("request", "reason", "app", "challenge", "expires")) throw ProtocolException("invalid approval request")
        val request = payload["request"] as? String ?: throw ProtocolException("request")
        val reason = payload["reason"] as? String ?: throw ProtocolException("reason")
        val app = payload["app"] as? String ?: throw ProtocolException("app")
        val challenge = payload["challenge"] as? String ?: throw ProtocolException("challenge")
        val expires = payload["expires"] as? Long ?: throw ProtocolException("expires")
        if (!Ids.IDENTIFIER.matches(request)) throw ProtocolException("request")
        if (!displayable(reason, MAX_REASON) || !displayable(app, MAX_APP)) throw ProtocolException("text")
        if (!canonicalChallenge(challenge)) throw ProtocolException("challenge")
        if (expires < nowSeconds - CLOCK_SKEW_SECONDS || expires > nowSeconds + MAX_LIFETIME_SECONDS + CLOCK_SKEW_SECONDS) {
            throw ProtocolException("expires")
        }
        return Request(request, reason, app, challenge, expires)
    }

    /** `luma-approval/1|<desktop pin>|<request>|<challenge as sent>|true|false`, UTF-8 (always ASCII). */
    fun message(desktopPin: String, request: String, challenge: String, approved: Boolean): ByteArray {
        if (!Ids.DIGEST.matches(desktopPin) || !Ids.IDENTIFIER.matches(request) || !canonicalChallenge(challenge)) {
            throw ProtocolException("invalid approval binding")
        }
        return "$DOMAIN|$desktopPin|$request|$challenge|$approved".toByteArray(Charsets.UTF_8)
    }

    fun response(request: String, approved: Boolean, signature: ByteArray?, publicKeySpki: ByteArray): Map<String, Any?> = mapOf(
        "request" to request,
        "approved" to approved,
        "signature" to signature?.let { Base64.getEncoder().encodeToString(it) },
        "public_key" to Base64.getEncoder().encodeToString(publicKeySpki),
    )

    /** Verification twin of the desktop's check; used by tests and for a local self-check after signing. */
    fun verify(publicKeySpki: ByteArray, message: ByteArray, signature: ByteArray): Boolean = runCatching {
        val key = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(publicKeySpki)) as ECPublicKey
        if (key.params.curve.field.fieldSize != 256) return false
        Signature.getInstance("SHA256withECDSA").run {
            initVerify(key)
            update(message)
            verify(signature)
        }
    }.getOrDefault(false)

    /** Standard padded base64 of exactly 32 bytes, in its one canonical spelling. */
    fun canonicalChallenge(value: String): Boolean {
        val decoded = runCatching { Base64.getDecoder().decode(value) }.getOrNull() ?: return false
        return decoded.size == CHALLENGE_BYTES && Base64.getEncoder().encodeToString(decoded) == value
    }

    /**
     * One line of text a person can read without being misled: non-empty, at most [limit]
     * code points, no control, format (including bidirectional overrides), private-use,
     * unassigned or unpaired surrogate code points. Python applies the same rule with
     * `unicodedata.category(c)[0] != "C"`.
     */
    fun displayable(value: String, limit: Int): Boolean {
        if (value.isEmpty() || value.isBlank()) return false
        var count = 0
        var index = 0
        while (index < value.length) {
            val codePoint = value.codePointAt(index)
            if (Character.isSurrogate(value[index]) && Character.charCount(codePoint) == 1) return false
            when (Character.getType(codePoint).toByte()) {
                Character.CONTROL, Character.FORMAT, Character.PRIVATE_USE, Character.UNASSIGNED, Character.SURROGATE -> return false
            }
            count++
            index += Character.charCount(codePoint)
        }
        return count <= limit
    }
}
