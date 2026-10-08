package org.projectluma.connect.core

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import org.projectluma.connect.protocol.Capabilities
import org.projectluma.connect.protocol.DeviceIdentity
import org.projectluma.connect.protocol.Exchange
import org.projectluma.connect.protocol.Ids
import org.projectluma.connect.protocol.Journal
import org.projectluma.connect.protocol.Peer
import org.projectluma.connect.protocol.Receipt
import org.projectluma.connect.protocol.Transport
import java.util.concurrent.CopyOnWriteArraySet
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Application-wide state: identity, journal, the paired computer and the
 * user's feature choices. Network calls always run off the main thread.
 */
object Connect {
    const val PHONE_PORT = 47811

    /** What this app asks to be allowed to do on the computer during pairing. */
    val REQUESTED = setOf(
        Capabilities.DEVICE_STATUS, Capabilities.NOTIFICATIONS_MIRROR, Capabilities.MEDIA_MIRROR,
        Capabilities.CLIPBOARD_WRITE, Capabilities.CLIPBOARD_READ, Capabilities.FILES_WRITE,
        Capabilities.LINKS_OPEN, Capabilities.INPUT_CONTROL, Capabilities.DEVICE_RING,
        Capabilities.DND_SET, Capabilities.AUTH_RESPONSE,
    )

    /** What this app lets the computer do on the phone during pairing. The computer's owner narrows both. */
    val OFFERED = setOf(
        Capabilities.DEVICE_RING, Capabilities.CLIPBOARD_WRITE, Capabilities.LINKS_OPEN, Capabilities.FILES_WRITE,
        "notifications.act", Capabilities.MEDIA_CONTROL, Capabilities.CAMERA_STREAM, "screen.view", "screen.control",
        Capabilities.DND_SET, Capabilities.HOTSPOT_REQUEST, Capabilities.AUTH_REQUEST, Capabilities.INPUT_CONTROL,
        Capabilities.BLUETOOTH_BOND,
    )

    /** [OFFERED] plus text messages in builds that declare SMS access. */
    fun offered(): Set<String> =
        if (org.projectluma.connect.messages.SmsAdapter.available(app)) OFFERED + setOf("messages.read", "messages.send") else OFFERED

    lateinit var app: Context
        private set
    lateinit var store: SqliteJournalStore
        private set
    lateinit var journal: Journal
        private set
    private lateinit var preferences: SharedPreferences

    /** Serializes outgoing calls so notifications arrive in order. */
    val calls: ExecutorService = Executors.newSingleThreadExecutor { Thread(it, "luma-connect-calls") }
    /** Bulk work (files, discovery) that must not block the ordered call queue. */
    val background: ExecutorService = Executors.newCachedThreadPool { Thread(it, "luma-connect-work") }
    private val main = Handler(Looper.getMainLooper())
    private val listeners = CopyOnWriteArraySet<() -> Unit>()

    @Volatile private var cachedIdentity: DeviceIdentity? = null
    @Volatile var linkState: LinkState = LinkState.Idle
        private set

    enum class LinkState { Idle, Searching, Connected, Paused }

    fun init(context: Context) {
        app = context.applicationContext
        store = SqliteJournalStore(app)
        journal = Journal(store)
        preferences = app.getSharedPreferences("luma-connect", Context.MODE_PRIVATE)
        if (!preferences.contains("salt")) preferences.edit().putString("salt", Ids.token() + Ids.token()).apply()
    }

    fun identity(): DeviceIdentity = cachedIdentity ?: synchronized(this) {
        cachedIdentity ?: Identity.load(app).also { cachedIdentity = it }
    }

    /**
     * Renews this phone's certificate with the paired computer before it expires. Returns true
     * when a new identity became active; the caller must restart anything holding the old one.
     */
    fun rotateIdentityIfDue(): Boolean = synchronized(this) {
        val current = identity()
        val peer = desktop() ?: return false
        if (Identity.daysRemaining(current) > Identity.ROTATE_BEFORE_DAYS) return false
        if (org.projectluma.connect.protocol.Capabilities.DEVICE_ROTATE !in peer.outgoing) return false
        val next = Identity.prepareNext(app)
        val accepted = runCatching { org.projectluma.connect.protocol.Rotation.rotateSelf(current, next, journal, peer.pin) }.getOrDefault(false)
        if (!accepted) return false
        Identity.commitNext(app)
        cachedIdentity = next
        true
    }

    /** The paired computer. Luma Connect keeps one active computer at a time in this release. */
    fun desktop(): Peer? = journal.activePeers().firstOrNull()

    val paired: Boolean get() = desktop() != null

    fun deviceName(): String =
        Settings.Global.getString(app.contentResolver, Settings.Global.DEVICE_NAME)?.takeIf { it.isNotBlank() }
            ?: "${Build.MANUFACTURER.replaceFirstChar { it.uppercase() }} ${Build.MODEL}"

    fun model(): String = "${Build.MANUFACTURER} ${Build.MODEL}".trim().take(128)

    /** Stable 32-hex keys that do not reveal Android notification keys to the computer. */
    fun opaqueKey(vararg parts: String): String =
        Transport.sha256Hex((preferences.getString("salt", "")!! + "|" + parts.joinToString("|")).toByteArray()).take(32)

    fun featureEnabled(capability: String): Boolean = preferences.getBoolean("feature.$capability", true)

    fun setFeatureEnabled(capability: String, enabled: Boolean) {
        preferences.edit().putBoolean("feature.$capability", enabled).apply()
        changed()
    }

    fun appMuted(packageName: String): Boolean = preferences.getStringSet("muted.apps", emptySet())!!.contains(packageName)

    fun setAppMuted(packageName: String, muted: Boolean) {
        val current = preferences.getStringSet("muted.apps", emptySet())!!.toMutableSet()
        if (muted) current += packageName else current -= packageName
        preferences.edit().putStringSet("muted.apps", current).apply()
        changed()
    }

    /** Whether the computer granted [capability] and the user has not paused it on this phone. */
    fun canSend(capability: String): Boolean {
        val peer = desktop() ?: return false
        return capability in peer.outgoing && featureEnabled(capability) && linkState != LinkState.Paused
    }

    fun canReceive(capability: String): Boolean {
        val peer = desktop() ?: return false
        return capability in peer.incoming && featureEnabled(capability) && linkState != LinkState.Paused
    }

    /** Runs a call on the ordered queue. [done] runs on the main thread. */
    fun send(capability: String, payload: Map<String, Any?>, done: ((Result<Receipt>) -> Unit)? = null) {
        calls.execute {
            val result = runCatching { callNow(capability, payload) }
            if (done != null) main.post { done(result) }
        }
    }

    /** Pointer and keyboard batches expire after 10 seconds so their journal tombstones are pruned quickly. */
    fun sendShortLived(capability: String, payload: Map<String, Any?>) {
        calls.execute { runCatching { callNow(capability, payload, lifetimeSeconds = 10, timeoutMs = 3_000) } }
    }

    /** Blocking call; never on the main thread. */
    fun callNow(capability: String, payload: Map<String, Any?>, timeoutMs: Int = 10_000, lifetimeSeconds: Long = 120): Receipt {
        check(Looper.myLooper() != Looper.getMainLooper()) { "network call on main thread" }
        val peer = desktop() ?: throw IllegalStateException("not paired")
        return try {
            Exchange(identity(), journal).call(peer.pin, capability, payload, lifetimeSeconds = lifetimeSeconds, timeoutMs = timeoutMs).also {
                setLinkState(LinkState.Connected)
            }
        } catch (error: Exception) {
            if (error is java.io.IOException) setLinkState(LinkState.Searching)
            throw error
        }
    }

    fun setLinkState(state: LinkState) {
        if (linkState == state) return
        if (linkState == LinkState.Paused && state != LinkState.Idle && state != LinkState.Paused) return
        linkState = state
        changed()
    }

    fun resume() {
        linkState = LinkState.Searching
        changed()
    }

    fun unpair(pin: String) {
        store.forget(pin)
        changed()
    }

    fun observe(listener: () -> Unit): () -> Unit {
        listeners += listener
        return { listeners -= listener }
    }

    fun changed() {
        main.post { listeners.forEach { it() } }
    }
}
