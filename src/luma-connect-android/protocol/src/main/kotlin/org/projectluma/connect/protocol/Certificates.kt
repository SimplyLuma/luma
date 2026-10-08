package org.projectluma.connect.protocol

import org.bouncycastle.asn1.x500.X500Name
import org.bouncycastle.asn1.x509.AlgorithmIdentifier
import org.bouncycastle.asn1.x509.BasicConstraints
import org.bouncycastle.asn1.x509.ExtendedKeyUsage
import org.bouncycastle.asn1.x509.Extension
import org.bouncycastle.asn1.x509.KeyPurposeId
import org.bouncycastle.asn1.x509.KeyUsage
import org.bouncycastle.asn1.x509.SubjectPublicKeyInfo
import org.bouncycastle.cert.X509v3CertificateBuilder
import org.bouncycastle.cert.jcajce.JcaX509ExtensionUtils
import org.bouncycastle.operator.ContentSigner
import org.bouncycastle.operator.DefaultSignatureAlgorithmIdentifierFinder
import java.io.ByteArrayOutputStream
import java.math.BigInteger
import java.security.PrivateKey
import java.security.PublicKey
import java.security.SecureRandom
import java.security.Signature
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.util.Base64
import java.util.Date
import java.util.Locale

/**
 * Device certificates that satisfy `pairing._certificate` and OpenSSL's
 * `VERIFY_X509_STRICT` when used as their own partial-chain trust anchor:
 * a P-256 leaf, `CA:FALSE`, digitalSignature and serverAuth+clientAuth.
 */
object Certificates {
    const val SUBJECT = "CN=Luma Connect device"

    /**
     * Signs through [signature] so a hardware-backed key that never leaves the
     * Android Keystore can issue its own certificate.
     */
    fun selfSigned(publicKey: PublicKey, privateKey: PrivateKey, validDays: Int, now: Long = System.currentTimeMillis()): X509Certificate {
        require(validDays in 1..398) { "validity must be between 1 and 398 days" }
        val subject = X500Name(SUBJECT)
        val serial = BigInteger(159, SecureRandom()).add(BigInteger.ONE)
        // Phones with a wrong clock would otherwise reject or be rejected by a certificate that "starts in the future".
        val notBefore = Date(now - 2 * 86_400_000L)
        val notAfter = Date(now + validDays * 86_400_000L)
        val info = SubjectPublicKeyInfo.getInstance(publicKey.encoded)
        val extensions = JcaX509ExtensionUtils()
        // Encode times with an explicit locale: some default locales (Arabic, Persian, Thai Buddhist) break ASN.1 time strings.
        val builder = X509v3CertificateBuilder(subject, serial, notBefore, notAfter, Locale.ROOT, subject, info)
            .addExtension(Extension.basicConstraints, true, BasicConstraints(false))
            .addExtension(Extension.keyUsage, true, KeyUsage(KeyUsage.digitalSignature))
            .addExtension(
                Extension.extendedKeyUsage, false,
                ExtendedKeyUsage(arrayOf(KeyPurposeId.id_kp_serverAuth, KeyPurposeId.id_kp_clientAuth)),
            )
            .addExtension(Extension.subjectKeyIdentifier, false, extensions.createSubjectKeyIdentifier(info))
        val holder = builder.build(JcaSigner(privateKey))
        return CertificateFactory.getInstance("X.509")
            .generateCertificate(holder.encoded.inputStream()) as X509Certificate
    }

    fun toPem(certificate: X509Certificate): String {
        val body = Base64.getMimeEncoder(64, "\n".toByteArray()).encodeToString(certificate.encoded)
        return "-----BEGIN CERTIFICATE-----\n$body\n-----END CERTIFICATE-----\n"
    }

    fun fromPem(pem: String): X509Certificate {
        if (pem.length > 16_384 || Regex("-----BEGIN CERTIFICATE-----").findAll(pem).count() != 1) {
            throw ProtocolException("one device certificate required")
        }
        return CertificateFactory.getInstance("X.509").generateCertificate(pem.toByteArray().inputStream()) as X509Certificate
    }

    private class JcaSigner(private val key: PrivateKey) : ContentSigner {
        private val buffer = ByteArrayOutputStream()
        override fun getAlgorithmIdentifier(): AlgorithmIdentifier =
            DefaultSignatureAlgorithmIdentifierFinder().find("SHA256withECDSA")
        override fun getOutputStream() = buffer
        override fun getSignature(): ByteArray = Signature.getInstance("SHA256withECDSA").run {
            initSign(key)
            update(buffer.toByteArray())
            sign()
        }
    }
}
