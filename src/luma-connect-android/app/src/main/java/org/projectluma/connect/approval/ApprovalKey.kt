package org.projectluma.connect.approval

import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.security.keystore.KeyProperties
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/**
 * The approval key, separate from the device identity used for TLS.
 *
 * P-256 in Android Keystore, usable only right after the person authenticates:
 * - Android 11+ (API 30): `setUserAuthenticationParameters(0, AUTH_BIOMETRIC_STRONG or AUTH_DEVICE_CREDENTIAL)`,
 *   a timeout of 0 meaning every signature needs its own authentication through a `CryptoObject`.
 * - Android 10 (API 29): the deprecated `setUserAuthenticationValidityDurationSeconds(-1)`, which is
 *   the same per-use rule for biometrics only. androidx.biometric cannot combine a `CryptoObject` with
 *   the device credential before API 30 (BiometricPrompt.PromptInfo.Builder#setAllowedAuthenticators).
 * `setInvalidatedByBiometricEnrollment(true)` makes Keystore destroy the key's usability when a new
 * biometric is enrolled; the key is then replaced and the computer must trust the new public key.
 */
object ApprovalKey {
    const val ALIAS = "luma-connect-approval"

    class Unavailable(cause: Throwable) : Exception(cause)

    private fun keyStore() = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    /** SubjectPublicKeyInfo DER of the current key, creating it if needed. */
    fun publicKey(): ByteArray {
        val store = keyStore()
        if (!store.containsAlias(ALIAS)) create()
        return keyStore().getCertificate(ALIAS).publicKey.encoded
    }

    /**
     * A `SHA256withECDSA` signature object ready for [androidx.biometric.BiometricPrompt.CryptoObject].
     * Returns `replaced = true` when the old key had been invalidated and a new one was made.
     */
    fun signer(): Pair<Signature, Boolean> {
        val store = keyStore()
        var replaced = false
        if (!store.containsAlias(ALIAS)) create()
        val signature = Signature.getInstance("SHA256withECDSA")
        try {
            signature.initSign(keyStore().getKey(ALIAS, null) as PrivateKey)
        } catch (_: KeyPermanentlyInvalidatedException) {
            keyStore().deleteEntry(ALIAS)
            create()
            replaced = true
            signature.initSign(keyStore().getKey(ALIAS, null) as PrivateKey)
        }
        return signature to replaced
    }

    private fun create() {
        try {
            val builder = KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setUserAuthenticationRequired(true)
                .setInvalidatedByBiometricEnrollment(true)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                builder.setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG or KeyProperties.AUTH_DEVICE_CREDENTIAL)
            } else {
                @Suppress("DEPRECATION")
                builder.setUserAuthenticationValidityDurationSeconds(-1)
            }
            KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore").apply { initialize(builder.build()) }.generateKeyPair()
        } catch (error: Exception) {
            // Typically no secure lock screen (or, on Android 10, no enrolled biometric).
            throw Unavailable(error)
        }
    }

    /** A throwaway software key, only so an unsigned denial carries a well-formed `public_key`. The desktop never pins it. */
    fun ephemeralPublicKey(): ByteArray =
        KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair().public.encoded
}
