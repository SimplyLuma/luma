package org.projectluma.connect.protocol

import java.security.KeyPairGenerator
import java.security.spec.ECGenParameterSpec

object TestIdentities {
    fun create(days: Int = 30): DeviceIdentity {
        val pair = KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair()
        return DeviceIdentity(pair.private, Certificates.selfSigned(pair.public, pair.private, days))
    }
}
