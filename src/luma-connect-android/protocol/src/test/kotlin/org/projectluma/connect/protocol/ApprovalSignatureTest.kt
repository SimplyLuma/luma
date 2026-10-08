package org.projectluma.connect.protocol

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.KeyPairGenerator
import java.security.MessageDigest
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.util.Base64

class ApprovalSignatureTest {
    // Cross-check vector. The same constants are in
    // src/luma-continuity/tests/test_companion_features.py (ApprovalVectorTest); the signature
    // was produced once by Python `cryptography` and must verify in both languages.
    private val pin = "18050ffd0924e288b03139d645375e5330287ae97fbba1303fdbbb8bacfc034b"
    private val request = "94af58a870ae07ed7108a9eff76ab174"
    private val challenge = "2LaWHh/UL48Ix7CppvcW4DDtOksYtyVkq7NaXcXz9/0="
    private val expectedMessage = "luma-approval/1|$pin|$request|$challenge|true"
    private val expectedDigest = "74444e9c69f8fc64c99af955fd4736366a143cad7f9d763edcc12f7898ad2b23"
    private val vectorKey = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEIFrO4EGO7an1NCMFW5nciXOKCyc0dZd5EuULyPT2cOKc6yNdZOoch9iQpZkDl5MIvEs08ARDIPr7lK+RtlBC3g=="
    private val vectorSignature = "MEQCIAeDod3k5gU2Q6SJ9OcyladZd1ixBsog9Ol5ZPDhjhgaAiA5CWJge6SEYng0UYhABvcVphNKHGZGi8GTYg15I2DwHA=="

    private fun hex(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it) }

    @Test
    fun messageMatchesTheDesktopByteForByte() {
        val message = Approval.message(pin, request, challenge, approved = true)
        assertArrayEquals(expectedMessage.toByteArray(Charsets.US_ASCII), message)
        assertEquals(expectedDigest, hex(MessageDigest.getInstance("SHA-256").digest(message)))
        assertEquals("luma-approval/1|$pin|$request|$challenge|false", String(Approval.message(pin, request, challenge, approved = false)))
    }

    @Test
    fun pythonSignatureVerifiesAndIsBoundToEveryField() {
        val key = Base64.getDecoder().decode(vectorKey)
        val signature = Base64.getDecoder().decode(vectorSignature)
        assertTrue(Approval.verify(key, Approval.message(pin, request, challenge, true), signature))
        assertFalse(Approval.verify(key, Approval.message(pin, request, challenge, false), signature))
        assertFalse(Approval.verify(key, Approval.message("00".repeat(32), request, challenge, true), signature))
        assertFalse(Approval.verify(key, Approval.message(pin, "0".repeat(32), challenge, true), signature))
        val otherChallenge = Base64.getEncoder().encodeToString(ByteArray(32))
        assertFalse(Approval.verify(key, Approval.message(pin, request, otherChallenge, true), signature))
    }

    @Test
    fun jvmSignatureRoundTripsWithSpkiPublicKey() {
        // Android Keystore produces the same DER signature and X.509 SubjectPublicKeyInfo encoding.
        val pair = KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair()
        val message = Approval.message(pin, request, challenge, true)
        val signature = Signature.getInstance("SHA256withECDSA").run { initSign(pair.private); update(message); sign() }
        assertTrue(Approval.verify(pair.public.encoded, message, signature))
        val response = Approval.response(request, true, signature, pair.public.encoded)
        assertEquals(setOf("request", "approved", "signature", "public_key"), response.keys)
        assertEquals(null, Approval.response(request, false, null, pair.public.encoded)["signature"])
        // A P-384 key is refused even with a valid signature.
        val large = KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp384r1")) }.generateKeyPair()
        val largeSignature = Signature.getInstance("SHA256withECDSA").run { initSign(large.private); update(message); sign() }
        assertFalse(Approval.verify(large.public.encoded, message, largeSignature))
    }

    @Test
    fun requestValidationIsStrict() {
        val now = 1_757_790_000L
        val good = mapOf("request" to request, "reason" to "Install updates", "app" to "Software", "challenge" to challenge, "expires" to now + 60)
        assertEquals("Install updates", Approval.parseRequest(good, now).reason)
        val bad = listOf(
            good + ("extra" to true),
            good - "app",
            good + ("request" to request.uppercase()),
            good + ("reason" to ""),
            good + ("reason" to "x".repeat(201)),
            good + ("app" to "y".repeat(65)),
            good + ("reason" to "Pay ‮yrtne"),
            good + ("reason" to "two\nlines"),
            good + ("challenge" to Base64.getEncoder().encodeToString(ByteArray(31))),
            good + ("challenge" to challenge.dropLast(1)),
            good + ("expires" to now + 60 + Approval.CLOCK_SKEW_SECONDS + 1),
            good + ("expires" to now - Approval.CLOCK_SKEW_SECONDS - 1),
            good + ("expires" to "soon"),
        )
        bad.forEach { payload -> assertThrows(ProtocolException::class.java) { Approval.parseRequest(payload, now) } }
        assertEquals(200, Approval.parseRequest(good + ("reason" to "😀".repeat(200)), now).reason.codePointCount(0, 400))
    }
}
