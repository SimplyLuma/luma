package org.projectluma.connect.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Capabilities
import org.projectluma.connect.protocol.InputStreamClient
import org.projectluma.connect.protocol.Streams
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * The phone as a trackpad and keyboard for the computer. Movement is
 * accumulated and flushed on a timer; clicks and typing are sent as they happen.
 */
@Composable
fun Trackpad(onBack: () -> Unit) {
    BackHandler(onBack = onBack)
    var needsUser by remember { mutableStateOf(false) }
    val batcher = remember { InputBatcher { needsUser = it } }
    DisposableEffect(Unit) { onDispose { batcher.close() } }
    var typed by remember { mutableStateOf("") }
    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) { Text("Done") }
            Text("Trackpad", style = MaterialTheme.typography.titleLarge)
        }
        if (needsUser) Text("Unlock your computer to use the trackpad", color = MaterialTheme.colorScheme.error)
        Surface(
            color = MaterialTheme.colorScheme.surfaceVariant, shape = MaterialTheme.shapes.large,
            modifier = Modifier.weight(1f).fillMaxWidth().semantics { contentDescription = "Trackpad. Drag to move the pointer, tap to click, drag with two fingers to scroll." }
                .pointerInput(Unit) {
                    awaitEachGesture {
                        val down = awaitFirstDown()
                        var moved = 0f
                        var fingers = 1
                        val started = System.currentTimeMillis()
                        do {
                            val event = awaitPointerEvent()
                            fingers = maxOf(fingers, event.changes.count { it.pressed })
                            val delta = event.changes.first().positionChange()
                            moved += kotlin.math.abs(delta.x) + kotlin.math.abs(delta.y)
                            if (fingers >= 2) batcher.scroll(delta.x, delta.y) else batcher.move(delta.x, delta.y)
                            event.changes.forEach { it.consume() }
                        } while (event.changes.any { it.pressed })
                        if (moved < 12f && System.currentTimeMillis() - started < 300) {
                            batcher.click(if (fingers >= 2) "right" else "left")
                        }
                        down.consume()
                    }
                },
        ) { Box(contentAlignment = Alignment.Center) { Text("Drag to move · tap to click · two fingers to scroll", color = MaterialTheme.colorScheme.onSurfaceVariant) } }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { batcher.click("left") }, modifier = Modifier.weight(1f)) { Text("Click") }
            OutlinedButton(onClick = { batcher.click("right") }, modifier = Modifier.weight(1f)) { Text("Right-click") }
        }
        OutlinedTextField(
            value = typed,
            onValueChange = { next ->
                if (next.length > typed.length && next.startsWith(typed)) batcher.text(next.substring(typed.length))
                else if (next.length < typed.length) repeat(typed.length - next.length) { batcher.key("backspace") }
                typed = next.takeLast(64)
            },
            label = { Text("Type on your computer") }, modifier = Modifier.fillMaxWidth(),
        )
    }
}

/**
 * Sends trackpad and keyboard events on a `luma-input/1` stream (one journaled request,
 * then frames on one TLS connection). If the computer predates streams it answers
 * `invalid-request` or `unavailable` and every batch uses a short-lived `input.control`
 * request instead, as before. A stream that cannot be opened is retried after five
 * seconds; batches meanwhile use the request path.
 */
private class InputBatcher(private val onNeedsUser: (Boolean) -> Unit) : AutoCloseable {
    private val pending = AtomicReference(Triple(0f, 0f, false))
    private val scrollPending = AtomicReference(0f to 0f)
    private val timer = Executors.newSingleThreadScheduledExecutor()
    private val lock = Any()
    @Volatile private var client: InputStreamClient? = null
    private var opening = false
    private var requestsOnly = false
    private var retryAt = 0L
    @Volatile private var closed = false
    private val waiting = ArrayList<List<Map<String, Any?>>>()
    private var ticks = 0

    init {
        timer.scheduleWithFixedDelay(::tick, 16, 16, TimeUnit.MILLISECONDS)
    }

    fun move(dx: Float, dy: Float) { pending.getAndUpdate { Triple(it.first + dx, it.second + dy, true) } }
    fun scroll(dx: Float, dy: Float) { scrollPending.getAndUpdate { (it.first + dx) to (it.second + dy) } }

    fun click(button: String) = send(listOf(
        mapOf("type" to "button", "button" to button, "pressed" to true),
        mapOf("type" to "button", "button" to button, "pressed" to false),
    ))

    fun text(value: String) = value.take(1024).chunked(256).let { parts -> send(parts.map { mapOf("type" to "text", "text" to it) }) }

    fun key(name: String) = send(listOf(mapOf("type" to "key", "key" to name, "pressed" to true), mapOf("type" to "key", "key" to name, "pressed" to false)))

    /** Pointer motion goes out about 60 times a second on a stream and 20 times a second as requests. */
    private fun tick() {
        ticks++
        val streaming = client?.closed == false
        if (!streaming && ticks % 3 != 0) return
        val (dx, dy, dirty) = pending.getAndSet(Triple(0f, 0f, false))
        // Each batch with scrolling ends a kinetic segment on the desktop, so scrolls keep the 50 ms cadence.
        val (sx, sy) = if (ticks % 3 == 0) scrollPending.getAndSet(0f to 0f) else (0f to 0f)
        val events = buildList {
            if (dirty && (dx != 0f || dy != 0f)) add(mapOf("type" to "move", "dx" to (dx * 1.6f).toLong().coerceIn(-1024, 1024), "dy" to (dy * 1.6f).toLong().coerceIn(-1024, 1024)))
            if (sx != 0f || sy != 0f) add(mapOf("type" to "scroll", "dx" to (-sx / 4f).toLong().coerceIn(-400, 400), "dy" to (-sy / 4f).toLong().coerceIn(-400, 400)))
        }
        if (events.isNotEmpty()) send(events)
    }

    private fun send(events: List<Map<String, Any?>>) {
        if (!Connect.canSend(Capabilities.INPUT_CONTROL)) return
        if (client?.send(events) == true) return
        synchronized(lock) {
            if (closed) return
            if (requestsOnly || System.currentTimeMillis() < retryAt || waiting.size >= 64) {
                request(events)
                return
            }
            waiting += events
            if (!opening) {
                opening = true
                Connect.background.execute(::open)
            }
        }
    }

    private fun request(events: List<Map<String, Any?>>) = Connect.sendShortLived(Capabilities.INPUT_CONTROL, mapOf("events" to events))

    private fun open() {
        val outcome = runCatching {
            val desktop = Connect.desktop() ?: throw IllegalStateException("not paired")
            val host = desktop.host ?: throw IllegalStateException("no route")
            val receipt = Connect.callNow(Capabilities.INPUT_CONTROL, mapOf("stream" to emptyMap<String, Any?>()), timeoutMs = 3_000, lifetimeSeconds = 10)
            if (Streams.shouldFallBack(receipt)) {
                null
            } else {
                InputStreamClient.open(
                    Connect.identity(), desktop.pin, host, Streams.inputSession(receipt),
                    onState = { state -> onNeedsUser(state == "needs-user") },
                    onClosed = { onNeedsUser(false) },
                )
            }
        }
        val backlog: List<List<Map<String, Any?>>>
        val opened = outcome.getOrNull()
        synchronized(lock) {
            opening = false
            backlog = waiting.toList()
            waiting.clear()
            when {
                closed -> opened?.close()
                outcome.isFailure -> retryAt = System.currentTimeMillis() + 5_000
                opened == null -> requestsOnly = true
                else -> client = opened
            }
        }
        if (closed) return
        backlog.forEach { batch -> if (opened == null || !opened.send(batch)) request(batch) }
    }

    override fun close() {
        synchronized(lock) { closed = true }
        timer.shutdownNow()
        client?.close()
    }
}
