package org.projectluma.connect.approval

import android.os.Build
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.biometric.BiometricManager.Authenticators
import androidx.biometric.BiometricPrompt
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import kotlinx.coroutines.delay
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Approval
import org.projectluma.connect.protocol.Capabilities
import org.projectluma.connect.service.Notifications
import org.projectluma.connect.ui.LumaTheme

/**
 * Shows exactly what the computer asks to approve, then asks for a strong biometric or
 * the screen lock before signing. Deny needs no authentication and sends no signature.
 * androidx.biometric 1.1.0 requires a FragmentActivity host for BiometricPrompt.
 */
class ApprovalActivity : FragmentActivity() {
    private var pending: ApprovalRequests.Pending? = null
    private var state by mutableStateOf<State>(State.Asking)
    private var remaining by mutableLongStateOf(0L)

    private sealed interface State {
        data object Asking : State
        data object Working : State
        data class Done(val message: String) : State
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // Another app must not be able to draw over, or tap through, an approval.
        window.decorView.filterTouchesWhenObscured = true
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) window.setHideOverlayWindows(true)
        Notifications.manager(this).cancel(ApprovalRequests.ID_NOTIFICATION)
        pending = ApprovalRequests.current(intent.getStringExtra(EXTRA_REQUEST))
        if (pending == null) state = State.Done("This request has expired. Try again from your computer.")
        setContent { LumaTheme { Screen() } }
    }

    @Composable
    private fun Screen() {
        val current = pending
        LaunchedEffect(current) {
            while (current != null) {
                remaining = (current.remainingMs / 1000).coerceAtLeast(0)
                if (remaining == 0L && state == State.Asking) state = State.Done("This request has expired. Try again from your computer.")
                delay(500)
            }
        }
        Surface(color = MaterialTheme.colorScheme.background, modifier = Modifier.fillMaxSize()) {
            Box(Modifier.safeDrawingPadding(), contentAlignment = Alignment.TopCenter) {
                Column(
                    Modifier.widthIn(max = 560.dp).fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 24.dp),
                    verticalArrangement = Arrangement.spacedBy(16.dp),
                ) {
                    when (val shown = state) {
                        is State.Done -> {
                            Text(shown.message, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                            Button(onClick = { finish() }, modifier = Modifier.fillMaxWidth().height(48.dp)) { Text("Close") }
                        }
                        else -> if (current != null) Request(current, working = shown == State.Working)
                    }
                }
            }
        }
    }

    @Composable
    private fun Request(current: ApprovalRequests.Pending, working: Boolean) {
        Text(
            "${current.desktopName} is asking you to approve",
            style = MaterialTheme.typography.headlineMedium, modifier = Modifier.semantics { heading() },
        )
        Surface(color = MaterialTheme.colorScheme.surface, shape = MaterialTheme.shapes.large, modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(current.request.reason, style = MaterialTheme.typography.titleLarge)
                Text("Requested by ${current.request.app}", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Text(
            "Approve only if you just started this on your computer. You'll confirm with your fingerprint, face or screen lock.",
            style = MaterialTheme.typography.bodyMedium,
        )
        Text("Expires in $remaining seconds", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedButton(onClick = { deny(current) }, enabled = !working, modifier = Modifier.weight(1f).height(48.dp)) { Text("Deny") }
            Button(onClick = { approve(current) }, enabled = !working, modifier = Modifier.weight(1f).height(48.dp)) { Text("Approve") }
        }
    }

    private fun deny(current: ApprovalRequests.Pending) {
        val key = runCatching { ApprovalKey.publicKey() }.getOrElse { ApprovalKey.ephemeralPublicKey() }
        send(current, approved = false, signature = null, publicKey = key)
    }

    private fun approve(current: ApprovalRequests.Pending) {
        val (signer, replaced) = try {
            ApprovalKey.signer()
        } catch (_: Exception) {
            state = State.Done(
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) "Set a screen lock on this phone to approve with it."
                else "Add a fingerprint or face on this phone to approve with it.",
            )
            return
        }
        val message = Approval.message(current.desktopPin, current.request.request, current.request.challenge, approved = true)
        val info = BiometricPrompt.PromptInfo.Builder()
            .setTitle("Approve on ${current.desktopName}")
            .setSubtitle(current.request.app)
            .setDescription(current.request.reason)
            .setConfirmationRequired(true)
            .apply {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    setAllowedAuthenticators(Authenticators.BIOMETRIC_STRONG or Authenticators.DEVICE_CREDENTIAL)
                } else {
                    setAllowedAuthenticators(Authenticators.BIOMETRIC_STRONG)
                    setNegativeButtonText("Cancel")
                }
            }
            .build()
        val prompt = BiometricPrompt(this, ContextCompat.getMainExecutor(this), object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                if (current.remainingMs <= 0) {
                    state = State.Done("This request has expired. Try again from your computer."); return
                }
                val signed = runCatching {
                    val signature = result.cryptoObject?.signature ?: error("no signature")
                    signature.update(message)
                    signature.sign()
                }.getOrElse {
                    state = State.Done("Couldn't sign the approval. Try again from your computer."); return
                }
                send(current, approved = true, signature = signed, publicKey = ApprovalKey.publicKey(), replaced = replaced)
            }

            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                // Cancelling the prompt leaves the request open for Approve or Deny.
                if (errorCode != BiometricPrompt.ERROR_USER_CANCELED && errorCode != BiometricPrompt.ERROR_NEGATIVE_BUTTON &&
                    errorCode != BiometricPrompt.ERROR_CANCELED
                ) {
                    state = State.Done(errString.toString())
                }
            }
        })
        prompt.authenticate(info, BiometricPrompt.CryptoObject(signer))
    }

    private fun send(current: ApprovalRequests.Pending, approved: Boolean, signature: ByteArray?, publicKey: ByteArray, replaced: Boolean = false) {
        state = State.Working
        ApprovalRequests.finish(current.request.request)
        val payload = Approval.response(current.request.request, approved, signature, publicKey)
        Connect.send(Capabilities.AUTH_RESPONSE, payload) { result ->
            val receipt = result.getOrNull()
            state = State.Done(
                when {
                    receipt == null || receipt.state != "complete" -> "Couldn't reach ${current.desktopName}. Nothing was approved."
                    receipt.error == "key-changed" -> "${current.desktopName} doesn't recognise this phone's approval key yet. " +
                        "On your computer, choose to trust this phone again, then retry."
                    receipt.error == "expired" -> "This request had already expired. Nothing was approved."
                    receipt.error != null -> "${current.desktopName} didn't accept the answer. Nothing was approved."
                    approved && replaced -> "Approved. This phone made a new approval key after a fingerprint change, so ${current.desktopName} may ask you to trust it."
                    approved -> "Approved on ${current.desktopName}."
                    else -> "Denied. ${current.desktopName} was told no."
                },
            )
        }
    }

    companion object {
        const val EXTRA_REQUEST = "request"
    }
}
