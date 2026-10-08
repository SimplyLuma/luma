package org.projectluma.connect.calls

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.delay
import org.projectluma.connect.R
import org.projectluma.connect.ui.LumaTheme

/**
 * Visible step for `bluetooth.bond`: says what pairing is for, that call audio stays on the
 * phone until the person chooses the computer, asks for `BLUETOOTH_CONNECT` when missing and
 * starts Android's own pairing dialog. Pairing itself is confirmed in that system dialog with
 * the code the computer also shows. The result is not sent anywhere: the computer sees the new
 * bond in BlueZ.
 */
class BluetoothBondActivity : ComponentActivity() {
    private var pending: BluetoothBondAdapter.Pending? = null
    private var state by mutableStateOf<State>(State.Explain)
    private var remaining by mutableLongStateOf(0L)
    private var receiving = false

    private sealed interface State {
        data object Explain : State
        data object Bonding : State
        data class Done(val message: String) : State
    }

    private val permission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val current = pending ?: return@registerForActivityResult
        if (granted) bond(current) else state = State.Done(getString(R.string.calls_bond_denied, current.request.name))
    }

    private val bondChanges = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val current = pending ?: return
            if (intent.action != BluetoothDevice.ACTION_BOND_STATE_CHANGED || state != State.Bonding) return
            val device = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java)
            } else {
                @Suppress("DEPRECATION") intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
            }
            if (device?.address != current.request.address) return
            when (intent.getIntExtra(BluetoothDevice.EXTRA_BOND_STATE, BluetoothDevice.ERROR)) {
                BluetoothDevice.BOND_BONDED -> finishWith(current, getString(R.string.calls_bond_done, current.request.name))
                BluetoothDevice.BOND_NONE -> finishWith(current, getString(R.string.calls_bond_failed))
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // Another app must not draw over, or tap through, a pairing request.
        window.decorView.filterTouchesWhenObscured = true
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) window.setHideOverlayWindows(true)
        getSystemService(android.app.NotificationManager::class.java).cancel(BluetoothBondAdapter.ID_NOTIFICATION)
        pending = BluetoothBondAdapter.current(intent.getStringExtra(EXTRA_TOKEN))
        if (pending == null) state = State.Done(getString(R.string.calls_bond_expired))
        setContent { LumaTheme { Screen() } }
    }

    override fun onStart() {
        super.onStart()
        // ACTION_BOND_STATE_CHANGED is a protected broadcast: only the system can send it.
        ContextCompat.registerReceiver(
            this, bondChanges, IntentFilter(BluetoothDevice.ACTION_BOND_STATE_CHANGED), ContextCompat.RECEIVER_EXPORTED,
        )
        receiving = true
    }

    override fun onStop() {
        if (receiving) unregisterReceiver(bondChanges)
        receiving = false
        super.onStop()
    }

    @Composable
    private fun Screen() {
        val current = pending
        LaunchedEffect(current) {
            while (current != null) {
                remaining = (current.remainingMs / 1000).coerceAtLeast(0)
                if (remaining == 0L && state != State.Bonding && state !is State.Done) {
                    state = State.Done(getString(R.string.calls_bond_expired))
                }
                delay(500)
            }
        }
        Surface(color = MaterialTheme.colorScheme.background, modifier = Modifier.fillMaxSize()) {
            Box(Modifier.safeDrawingPadding(), contentAlignment = Alignment.TopCenter) {
                Column(
                    Modifier.widthIn(max = 560.dp).fillMaxSize().verticalScroll(rememberScrollState())
                        .padding(horizontal = 20.dp, vertical = 24.dp),
                    verticalArrangement = Arrangement.spacedBy(16.dp),
                ) {
                    when (val shown = state) {
                        is State.Done -> {
                            Text(
                                shown.message, style = MaterialTheme.typography.headlineSmall,
                                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                            )
                            Button(onClick = { finish() }, modifier = Modifier.fillMaxWidth().height(48.dp)) {
                                Text(stringResource(R.string.calls_bond_close))
                            }
                        }
                        State.Bonding -> if (current != null) {
                            Text(
                                stringResource(R.string.calls_bond_waiting, current.request.name),
                                style = MaterialTheme.typography.headlineSmall,
                                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                            )
                            OutlinedButton(onClick = { finish() }, modifier = Modifier.fillMaxWidth().height(48.dp)) {
                                Text(stringResource(R.string.calls_bond_close))
                            }
                        }
                        State.Explain -> if (current != null) Explain(current)
                    }
                }
            }
        }
    }

    @Composable
    private fun Explain(current: BluetoothBondAdapter.Pending) {
        val name = current.request.name
        Text(
            stringResource(R.string.calls_bond_title, name),
            style = MaterialTheme.typography.headlineMedium, modifier = Modifier.semantics { heading() },
        )
        Text(stringResource(R.string.calls_bond_explain, name), style = MaterialTheme.typography.bodyLarge)
        Surface(color = MaterialTheme.colorScheme.surface, shape = MaterialTheme.shapes.large, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.calls_bond_audio), style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(16.dp))
        }
        Text(stringResource(R.string.calls_bond_code, name), style = MaterialTheme.typography.bodyMedium)
        Text(stringResource(R.string.calls_bond_contacts), style = MaterialTheme.typography.bodyMedium)
        if (!BluetoothBondAdapter.hasConnectPermission(this)) {
            Text(stringResource(R.string.calls_bond_permission), style = MaterialTheme.typography.bodyMedium)
        }
        Text(
            stringResource(R.string.calls_bond_expires, remaining.toInt()),
            style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedButton(onClick = { decline(current) }, modifier = Modifier.weight(1f).height(48.dp)) {
                Text(stringResource(R.string.calls_bond_not_now))
            }
            Button(onClick = { start(current) }, modifier = Modifier.weight(1f).height(48.dp)) {
                Text(stringResource(R.string.calls_bond_pair))
            }
        }
    }

    private fun decline(current: BluetoothBondAdapter.Pending) {
        BluetoothBondAdapter.finish(current.token)
        finish()
    }

    private fun start(current: BluetoothBondAdapter.Pending) {
        if (current.remainingMs <= 0) {
            state = State.Done(getString(R.string.calls_bond_expired)); return
        }
        if (BluetoothBondAdapter.hasConnectPermission(this)) bond(current)
        else permission.launch(Manifest.permission.BLUETOOTH_CONNECT)
    }

    // Guarded by hasConnectPermission on Android 12+; BLUETOOTH and BLUETOOTH_ADMIN cover Android 10 and 11.
    @SuppressLint("MissingPermission")
    private fun bond(current: BluetoothBondAdapter.Pending) {
        if (current.remainingMs <= 0) {
            state = State.Done(getString(R.string.calls_bond_expired)); return
        }
        val adapter = getSystemService(BluetoothManager::class.java)?.adapter
        if (adapter == null) {
            finishWith(current, getString(R.string.calls_bond_unavailable)); return
        }
        try {
            if (!adapter.isEnabled) {
                state = State.Done(getString(R.string.calls_bond_bluetooth_off)); return
            }
            val device = adapter.getRemoteDevice(current.request.address)
            when (device.bondState) {
                BluetoothDevice.BOND_BONDED -> finishWith(current, getString(R.string.calls_bond_already, current.request.name))
                BluetoothDevice.BOND_BONDING -> state = State.Bonding
                else -> if (device.createBond()) state = State.Bonding
                else finishWith(current, getString(R.string.calls_bond_failed))
            }
        } catch (_: SecurityException) {
            state = State.Done(getString(R.string.calls_bond_denied, current.request.name))
        } catch (_: IllegalArgumentException) {
            finishWith(current, getString(R.string.calls_bond_failed))
        }
    }

    private fun finishWith(current: BluetoothBondAdapter.Pending, message: String) {
        BluetoothBondAdapter.finish(current.token)
        state = State.Done(message)
    }

    companion object {
        const val EXTRA_TOKEN = "token"
    }
}
