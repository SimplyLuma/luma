package org.projectluma.connect.core

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import org.projectluma.connect.protocol.Certificates
import org.projectluma.connect.protocol.DeviceIdentity
import org.projectluma.connect.protocol.Ids
import java.io.File
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.spec.ECGenParameterSpec

/**
 * This phone's device identity: a non-exportable P-256 key in Android
 * Keystore and a self-signed certificate that the key signs itself.
 * The key never leaves secure hardware (where the device has it) and is not
 * included in backups, so a restored or replaced phone must pair again.
 *
 * Certificates are replaced before they expire through `device.rotate`
 * ([prepareNext], then [commitNext] once the computer accepted), so a
 * pairing survives renewal without scanning a new code.
 */
object Identity {
    private const val BASE_ALIAS = "luma-connect-device"
    private const val VALID_DAYS = 398
    /** Start rotating this long before expiry; the computer may be unreachable for weeks. */
    const val ROTATE_BEFORE_DAYS = 60L

    private fun preferences(context: Context) = context.getSharedPreferences("luma-connect-identity", Context.MODE_PRIVATE)
    private fun keyStore() = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    fun load(context: Context): DeviceIdentity {
        val alias = preferences(context).getString("alias", BASE_ALIAS)!!
        val file = File(context.noBackupFilesDir, "device.pem")
        val store = keyStore()
        val key = store.getKey(alias, null) as? PrivateKey
        if (key != null && file.exists()) {
            val certificate = Certificates.fromPem(file.readText())
            val publicMatches = store.getCertificate(alias)?.publicKey?.encoded?.contentEquals(certificate.publicKey.encoded) == true
            if (certificate.notAfter.time > System.currentTimeMillis() && publicMatches) return DeviceIdentity(key, certificate)
        }
        // No usable identity (first run, or a certificate that expired before it could rotate): create one.
        // Existing pairings cannot survive this and are shown as needing a new pairing.
        return create(context, alias, file)
    }

    fun daysRemaining(identity: DeviceIdentity): Long =
        (identity.certificate.notAfter.time - System.currentTimeMillis()) / 86_400_000L

    /** Creates (or reloads) the replacement identity without touching the active one. */
    fun prepareNext(context: Context): DeviceIdentity {
        val preferences = preferences(context)
        val file = File(context.noBackupFilesDir, "device-next.pem")
        val alias = preferences.getString("next", null)
        if (alias != null && file.exists()) {
            val key = keyStore().getKey(alias, null) as? PrivateKey
            if (key != null) return DeviceIdentity(key, Certificates.fromPem(file.readText()))
        }
        val nextAlias = "$BASE_ALIAS-${Ids.token().take(12)}"
        preferences.edit().putString("next", nextAlias).commit()
        return create(context, nextAlias, file)
    }

    /** Makes the prepared identity active after the computer accepted it. */
    fun commitNext(context: Context) {
        val preferences = preferences(context)
        val next = preferences.getString("next", null) ?: return
        val old = preferences.getString("alias", BASE_ALIAS)!!
        val nextFile = File(context.noBackupFilesDir, "device-next.pem")
        val activeFile = File(context.noBackupFilesDir, "device.pem")
        if (!nextFile.renameTo(activeFile)) throw IllegalStateException("could not activate new certificate")
        preferences.edit().putString("alias", next).remove("next").commit()
        runCatching { keyStore().deleteEntry(old) }
    }

    /** Replaces the identity. Every existing pairing stops working and must be repeated. */
    fun reset(context: Context) {
        val preferences = preferences(context)
        val store = keyStore()
        listOfNotNull(preferences.getString("alias", BASE_ALIAS), preferences.getString("next", null)).forEach { runCatching { store.deleteEntry(it) } }
        preferences.edit().clear().commit()
        File(context.noBackupFilesDir, "device.pem").delete()
        File(context.noBackupFilesDir, "device-next.pem").delete()
    }

    private fun create(context: Context, alias: String, file: File): DeviceIdentity {
        val store = keyStore()
        store.deleteEntry(alias)
        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
        generator.initialize(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                // TLS 1.3 signs with SHA-256; some providers pre-hash and ask for NONE.
                .setDigests(KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_NONE)
                .build(),
        )
        val pair = generator.generateKeyPair()
        val certificate = Certificates.selfSigned(pair.public, pair.private, VALID_DAYS)
        val temporary = File(file.parentFile, file.name + ".tmp")
        temporary.writeText(Certificates.toPem(certificate))
        if (!temporary.renameTo(file)) throw IllegalStateException("could not store device certificate")
        return DeviceIdentity(pair.private, certificate)
    }
}
