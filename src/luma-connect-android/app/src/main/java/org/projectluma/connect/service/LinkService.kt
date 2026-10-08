package org.projectluma.connect.service

import android.app.Notification
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.os.IBinder
import org.projectluma.connect.R
import org.projectluma.connect.clipboard.ClipboardSendActivity
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Capabilities
import org.projectluma.connect.protocol.Listener
import org.projectluma.connect.protocol.Receiver
import org.projectluma.connect.protocol.Rotation
import org.projectluma.connect.protocol.Transport
import java.net.Inet4Address
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The persistent link to the paired computer.
 *
 * Runs as a `connectedDevice` foreground service (no Android 15 timeout),
 * listens for the computer's requests, advertises `_luma-connect._tcp`, finds
 * the computer again after network changes and reports battery status. There
 * is no polling: work happens on network, battery and user events only.
 */
class LinkService : Service() {
    private var listener: Listener? = null
    private var nsd: NsdManager? = null
    private var registration: NsdManager.RegistrationListener? = null
    private var discovery: NsdManager.DiscoveryListener? = null
    private var multicastLock: WifiManager.MulticastLock? = null
    private var networkCallback: ConnectivityManager.NetworkCallback? = null
    private var lastStatus: Map<String, Any?>? = null
    private val resolving = AtomicBoolean(false)
    private val pendingResolve = ConcurrentLinkedQueue<NsdServiceInfo>()
    private var unobserve: (() -> Unit)? = null
    private var stopDnd: (() -> Unit)? = null

    private val batteryReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            Connect.background.execute { reportStatus(force = false) }
        }
    }

    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (Connect.linkState != Connect.LinkState.Connected) reconnect()
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        running = this
        startInForeground()
        if (!Connect.paired) {
            stopSelf()
            return
        }
        unobserve = Connect.observe { updateNotification() }
        startListener()
        stopDnd = DndSync.register(this)
        registerService()
        watchNetwork()
        registerReceiver(batteryReceiver, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        registerReceiver(screenReceiver, IntentFilter(Intent.ACTION_SCREEN_ON))
        reconnect()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!Connect.paired) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_RECONNECT) reconnect()
        updateNotification()
        return START_STICKY
    }

    override fun onDestroy() {
        running = null
        unobserve?.invoke()
        stopDnd?.invoke()
        runCatching { unregisterReceiver(batteryReceiver) }
        runCatching { unregisterReceiver(screenReceiver) }
        networkCallback?.let { runCatching { getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(it) } }
        stopDiscovery()
        registration?.let { runCatching { nsd?.unregisterService(it) } }
        listener?.stop()
        multicastLock?.runCatching { release() }
        super.onDestroy()
    }

    private fun startInForeground() {
        val notification = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(Notifications.ID_LINK, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } else {
            startForeground(Notifications.ID_LINK, notification)
        }
    }

    private fun buildNotification(): Notification {
        val name = Connect.desktop()?.name ?: getString(R.string.app_name)
        val paused = Connect.linkState == Connect.LinkState.Paused
        val title = when (Connect.linkState) {
            Connect.LinkState.Connected -> getString(R.string.link_connected, name)
            Connect.LinkState.Paused -> getString(R.string.link_paused)
            else -> getString(R.string.link_searching, name)
        }
        val clipboard = android.app.PendingIntent.getActivity(
            this, 20, Intent(this, ClipboardSendActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            android.app.PendingIntent.FLAG_IMMUTABLE,
        )
        return Notifications.builder(this, Notifications.LINK)
            .setContentTitle(title)
            .setOngoing(true)
            .setShowWhen(false)
            .addAction(Notification.Action.Builder(null, getString(R.string.action_send_clipboard), clipboard).build())
            .addAction(
                Notification.Action.Builder(
                    null, getString(if (paused) R.string.tap_to_start else R.string.action_pause),
                    Notifications.action(this, if (paused) ActionReceiver.RESUME else ActionReceiver.PAUSE, 21),
                ).build(),
            )
            .build()
    }

    private fun updateNotification() {
        Notifications.manager(this).notify(Notifications.ID_LINK, buildNotification())
    }

    private fun startListener() {
        val receiver = Receiver(
            Connect.journal,
            PhoneAdapters(this).all() + StreamAdapters(this).all() + org.projectluma.connect.messages.SmsAdapter(this).all() + (Capabilities.DEVICE_ROTATE to Rotation.adapter(Connect.journal) { Connect.identity().pin }) +
                (Capabilities.BLUETOOTH_BOND to org.projectluma.connect.calls.BluetoothBondAdapter.adapter(this)),
        )
        val preferred = runCatching { Listener(Connect.identity(), Connect.journal, receiver, Connect.PHONE_PORT).also { it.start() } }
        listener = preferred.getOrElse { Listener(Connect.identity(), Connect.journal, receiver, 0).also { it.start() } }
    }

    val port: Int get() = listener?.boundPort ?: 0

    /** After certificate rotation the listener must present the new certificate. */
    private fun restartListener() {
        listener?.stop()
        startListener()
        lastStatus = null
        Connect.background.execute { reportStatus(force = true) }
    }

    private fun registerService() {
        val manager = getSystemService(NsdManager::class.java)
        nsd = manager
        multicastLock = getSystemService(WifiManager::class.java).createMulticastLock("luma-connect").apply {
            setReferenceCounted(false)
            acquire()
        }
        val info = NsdServiceInfo().apply {
            serviceName = "Luma Connect"
            serviceType = SERVICE_TYPE
            port = this@LinkService.port
            // Deliberately no name, model or pin: the advertisement must not identify a person on shared Wi-Fi.
            setAttribute("v", "1")
        }
        val listener = object : NsdManager.RegistrationListener {
            override fun onServiceRegistered(info: NsdServiceInfo) = Unit
            override fun onRegistrationFailed(info: NsdServiceInfo, errorCode: Int) = Unit
            override fun onServiceUnregistered(info: NsdServiceInfo) = Unit
            override fun onUnregistrationFailed(info: NsdServiceInfo, errorCode: Int) = Unit
        }
        registration = listener
        runCatching { manager.registerService(info, NsdManager.PROTOCOL_DNS_SD, listener) }
    }

    private fun watchNetwork() {
        val connectivity = getSystemService(ConnectivityManager::class.java)
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) = reconnect()
            override fun onLost(network: Network) = Connect.setLinkState(Connect.LinkState.Searching)
        }
        networkCallback = callback
        connectivity.registerDefaultNetworkCallback(callback)
    }

    /** Tries the last known address first, then asks mDNS for every Luma Connect service and probes each with TLS. */
    fun reconnect() {
        if (Connect.linkState == Connect.LinkState.Paused) return
        Connect.background.execute {
            if (reportStatus(force = true)) return@execute
            Connect.setLinkState(Connect.LinkState.Searching)
            startDiscovery()
        }
    }

    private fun startDiscovery() {
        val manager = nsd ?: return
        stopDiscovery()
        val listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) = Unit
            override fun onDiscoveryStopped(serviceType: String) = Unit
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) = Unit
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) = Unit
            override fun onServiceLost(info: NsdServiceInfo) = Unit
            override fun onServiceFound(info: NsdServiceInfo) {
                if (info.serviceType.trimEnd('.') != SERVICE_TYPE.trimEnd('.')) return
                pendingResolve += info
                resolveNext()
            }
        }
        discovery = listener
        runCatching { manager.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, listener) }
    }

    private fun stopDiscovery() {
        discovery?.let { runCatching { nsd?.stopServiceDiscovery(it) } }
        discovery = null
        pendingResolve.clear()
    }

    @Suppress("DEPRECATION")
    private fun resolveNext() {
        val manager = nsd ?: return
        if (!resolving.compareAndSet(false, true)) return
        val next = pendingResolve.poll() ?: run { resolving.set(false); return }
        manager.resolveService(next, object : NsdManager.ResolveListener {
            override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) {
                resolving.set(false)
                resolveNext()
            }

            override fun onServiceResolved(info: NsdServiceInfo) {
                resolving.set(false)
                val host = info.host
                if (host != null && info.port != port) {
                    Connect.background.execute { probe(host.hostAddress ?: return@execute, info.port, host is Inet4Address) }
                }
                resolveNext()
            }
        })
    }

    private fun probe(host: String, desktopPort: Int, ipv4: Boolean) {
        val peer = Connect.desktop() ?: return
        if (!ipv4 && !host.contains('%') && host.startsWith("fe80")) return
        // A candidate is only accepted if it proves the paired certificate.
        val reachable = runCatching {
            Transport.connect(Transport.context(Connect.identity()) { setOf(peer.pin) }, host, desktopPort, peer.pin, 3_000).close()
        }.isSuccess
        if (!reachable) return
        Connect.journal.updateRoute(peer.pin, host, desktopPort)
        stopDiscovery()
        reportStatus(force = true)
    }

    /** Sends battery and route. Returns whether the computer answered. */
    private fun reportStatus(force: Boolean): Boolean {
        if (!Connect.canSend(Capabilities.DEVICE_STATUS)) {
            return false
        }
        val status = currentStatus()
        val previous = lastStatus
        if (!force && previous != null) {
            val batteryDelta = kotlin.math.abs(((status["battery"] as Long?) ?: 0L) - ((previous["battery"] as Long?) ?: 0L))
            if (batteryDelta < 5 && status["charging"] == previous["charging"] && status["network"] == previous["network"]) return true
        }
        if (Thread.currentThread().name.startsWith("main")) {
            Connect.background.execute { reportStatus(force) }
            return true
        }
        val wasConnected = Connect.linkState == Connect.LinkState.Connected
        return runCatching { Connect.callNow(Capabilities.DEVICE_STATUS, status, timeoutMs = 4_000) }
            .onSuccess {
                lastStatus = status
                if (!wasConnected) NotificationMirrorService.instance?.resync()
                if (Connect.rotateIdentityIfDue()) restartListener()
            }
            .isSuccess
    }

    private fun currentStatus(): Map<String, Any?> {
        val battery = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, 100) ?: 100
        val plugged = (battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
        val capabilities = getSystemService(ConnectivityManager::class.java).let { it.getNetworkCapabilities(it.activeNetwork) }
        val network = when {
            capabilities == null -> "none"
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
            else -> "other"
        }
        return mapOf(
            "battery" to if (level >= 0 && scale > 0) (level * 100L / scale).coerceIn(0, 100) else null,
            "charging" to plugged,
            "network" to network,
            "listen_port" to port.toLong(),
            "name" to Connect.deviceName().take(128),
        )
    }

    companion object {
        const val SERVICE_TYPE = "_luma-connect._tcp"
        const val ACTION_RECONNECT = "org.projectluma.connect.RECONNECT"

        @Volatile var running: LinkService? = null
            private set

        /** Starting can be refused in some background states (Android 12+); the next user or network event retries. */
        fun start(context: Context) {
            if (!Connect.paired) return
            runCatching { context.startForegroundService(Intent(context, LinkService::class.java).setAction(ACTION_RECONNECT)) }
        }

        fun refresh(context: Context) {
            running?.let { runCatching { context.startForegroundService(Intent(context, LinkService::class.java)) } }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, LinkService::class.java))
        }
    }
}
