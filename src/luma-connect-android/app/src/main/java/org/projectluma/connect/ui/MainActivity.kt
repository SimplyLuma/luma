package org.projectluma.connect.ui

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import android.view.inputmethod.InputMethodManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import org.projectluma.connect.clipboard.ClipboardSendActivity
import org.projectluma.connect.control.RemoteControlService
import org.projectluma.connect.ime.LumaKeyboardService
import org.projectluma.connect.service.DndSync
import org.projectluma.connect.core.Connect
import org.projectluma.connect.pairing.QrScanner
import org.projectluma.connect.protocol.Capabilities
import org.projectluma.connect.protocol.Pairing
import org.projectluma.connect.protocol.Peer
import org.projectluma.connect.service.LinkService
import org.projectluma.connect.service.NotificationMirrorService
import org.projectluma.connect.share.FileSender

class MainActivity : ComponentActivity() {
    private var incomingLink by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        incomingLink = intent?.dataString
        if (Connect.paired) LinkService.start(this)
        setContent { LumaTheme { App(incomingLink) { incomingLink = null } } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        incomingLink = intent.dataString
    }
}

private sealed interface Screen {
    data object Welcome : Screen
    data object Scan : Screen
    data class Pairing(val link: String) : Screen
    data class Confirm(val sas: String, val name: String) : Screen
    data object Home : Screen
    data object Trackpad : Screen
}

@Composable
private fun App(incomingLink: String?, consumeLink: () -> Unit) {
    var revision by remember { mutableIntStateOf(0) }
    DisposableEffect(Unit) { onDispose(Connect.observe { revision++ }) }
    var screen by remember { mutableStateOf(if (Connect.paired) Screen.Home else Screen.Welcome) }
    LaunchedEffect(incomingLink) {
        if (incomingLink != null) {
            // Opening a pairing link never pairs by itself: the person still confirms the six-digit code.
            screen = Screen.Pairing(incomingLink)
            consumeLink()
        }
    }
    Surface(color = MaterialTheme.colorScheme.background, modifier = Modifier.fillMaxSize()) {
        Box(Modifier.safeDrawingPadding(), contentAlignment = Alignment.TopCenter) {
            Box(Modifier.widthIn(max = 560.dp).fillMaxSize()) {
                when (val current = screen) {
                    Screen.Welcome -> Welcome { screen = Screen.Scan }
                    Screen.Scan -> Scan(onBack = { screen = Screen.Welcome }) { screen = Screen.Pairing(it) }
                    is Screen.Pairing -> PairingProgress(current.link, onDone = { sas, name -> screen = Screen.Confirm(sas, name) }) { screen = Screen.Scan }
                    is Screen.Confirm -> Confirm(current.sas, current.name, onMatch = { screen = Screen.Home }) { screen = Screen.Welcome }
                    // Link updates recompose Home in place; recreating it would jump to the top and close dialogs.
                    Screen.Home -> Home(revision, onTrackpad = { screen = Screen.Trackpad }, onUnpaired = { screen = Screen.Welcome })
                    Screen.Trackpad -> Trackpad { screen = Screen.Home }
                }
            }
        }
    }
}

@Composable
private fun <T> key(value: Int, content: @Composable () -> T): T = androidx.compose.runtime.key(value) { content() }

@Composable
private fun Page(content: @Composable () -> Unit) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) { content() }
}

@Composable
private fun Heading(text: String, detail: String? = null) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(text, style = MaterialTheme.typography.headlineMedium, modifier = Modifier.semantics { heading() })
        if (detail != null) Text(detail, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun Island(content: @Composable () -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = MaterialTheme.shapes.large,
        modifier = Modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outline, MaterialTheme.shapes.large),
    ) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) { content() } }
}

@Composable
private fun Welcome(onStart: () -> Unit) {
    Page {
        Spacer(Modifier.height(24.dp))
        Heading("Your phone and your Luma computer, working together")
        Text(
            "See phone notifications on your computer, copy on one and paste on the other, send files and links, and use this phone as a webcam. You choose each feature, and you can pause or remove it from either device.",
            style = MaterialTheme.typography.bodyLarge,
        )
        Island {
            Text("To pair", style = MaterialTheme.typography.titleMedium)
            Text("1. On your computer, open Luma Connect and choose Pair an Android phone.", style = MaterialTheme.typography.bodyMedium)
            Text("2. Scan the code it shows.", style = MaterialTheme.typography.bodyMedium)
            Text("3. Check that both screens show the same six digits.", style = MaterialTheme.typography.bodyMedium)
        }
        Text(
            "Both devices need to be on the same network while pairing. Everything travels directly between them and is encrypted to these two devices only.",
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Button(onClick = onStart, modifier = Modifier.fillMaxWidth().height(48.dp)) { Text("Scan pairing code") }
    }
}

@Composable
private fun Scan(onBack: () -> Unit, onCode: (String) -> Unit) {
    val context = LocalContext.current
    BackHandler(onBack = onBack)
    var granted by remember { mutableStateOf(context.checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted = it }
    LaunchedEffect(Unit) { if (!granted) permission.launch(Manifest.permission.CAMERA) }
    var manual by remember { mutableStateOf("") }
    Page {
        Heading("Scan the code on your computer")
        if (granted) {
            Box(Modifier.fillMaxWidth().aspectRatio(1f).clip(MaterialTheme.shapes.large)) { QrScanner(Modifier.fillMaxSize(), onCode) }
        } else {
            Island {
                Text("Luma Connect needs the camera to read the pairing code. The picture never leaves this phone.", style = MaterialTheme.typography.bodyMedium)
                OutlinedButton(onClick = { permission.launch(Manifest.permission.CAMERA) }) { Text("Allow camera") }
            }
        }
        Text("Can't scan? Copy the pairing link from your computer and paste it here.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedTextField(
            value = manual, onValueChange = { manual = it.trim() }, label = { Text("Pairing link") }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            TextButton(onClick = onBack) { Text("Back") }
            Button(onClick = { onCode(manual) }, enabled = manual.startsWith("luma-connect://")) { Text("Continue") }
        }
    }
}

@Composable
private fun PairingProgress(link: String, onDone: (String, String) -> Unit, onRetry: () -> Unit) {
    var error by remember { mutableStateOf<String?>(null) }
    val invitation = remember(link) { runCatching { Pairing.parse(link) } }
    LaunchedEffect(link) {
        val parsed = invitation.getOrElse { error = it.message; return@LaunchedEffect }
        Connect.background.execute {
            val result = runCatching {
                val listenPort = Connect.PHONE_PORT
                Pairing.pair(parsed, Connect.identity(), Connect.journal, Connect.deviceName(), Connect.model(), listenPort, Connect.REQUESTED, Connect.offered())
            }
            android.os.Handler(android.os.Looper.getMainLooper()).post {
                result.onSuccess {
                    Connect.resume()
                    LinkService.start(Connect.app)
                    onDone(it.sas, it.peer.name)
                }.onFailure { error = it.message ?: "Pairing didn't finish" }
            }
        }
    }
    Page {
        val name = invitation.getOrNull()?.name ?: "your computer"
        if (error == null) {
            Heading("Connecting to $name")
            CircularProgressIndicator()
        } else {
            Heading("Couldn't pair", error)
            Text("Make sure both devices are on the same network and the code on your computer is still showing. Codes expire after ten minutes.", style = MaterialTheme.typography.bodyMedium)
            Button(onClick = onRetry) { Text("Scan again") }
        }
    }
}

@Composable
private fun Confirm(sas: String, name: String, onMatch: () -> Unit, onMismatch: () -> Unit) {
    Page {
        Heading("Check the code", "$name should show the same six digits.")
        Text(
            sas.chunked(3).joinToString(" "),
            fontSize = 44.sp, fontFamily = FontFamily.Monospace, textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth().padding(vertical = 24.dp),
        )
        Button(onClick = onMatch, modifier = Modifier.fillMaxWidth().height(48.dp)) { Text("The codes match") }
        OutlinedButton(
            onClick = { Connect.desktop()?.let { Connect.unpair(it.pin) }; LinkService.stop(Connect.app); onMismatch() },
            modifier = Modifier.fillMaxWidth(),
        ) { Text("They're different — cancel") }
        Text("If the codes are different, cancel here and remove the phone on your computer too.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

private data class Feature(
    val capability: String,
    val title: String,
    val detail: String,
    val direction: Direction,
    val needs: Need = Need.None,
    /** The on/off switch on this phone; differs from [capability] when one capability backs two features. */
    val preference: String = capability,
)

private enum class Direction { ToComputer, FromComputer, Both }
private enum class Need { None, NotificationAccess, Accessibility, NotificationPolicy, Keyboard, Sms }

private val FEATURES = listOf(
    Feature(Capabilities.NOTIFICATIONS_MIRROR, "Notifications on your computer", "Read, reply to and dismiss phone notifications there.", Direction.ToComputer, Need.NotificationAccess),
    Feature("notifications.act", "Replies and actions from your computer", "Your computer can reply to, act on and dismiss these notifications.", Direction.FromComputer, Need.NotificationAccess),
    Feature(Capabilities.MEDIA_MIRROR, "What's playing", "Show and control this phone's music and podcasts on your computer.", Direction.ToComputer, Need.NotificationAccess),
    Feature(Capabilities.CLIPBOARD_WRITE, "Clipboard", "Copy on your computer and paste here. To send this phone's clipboard, use Send clipboard or the Quick Settings tile.", Direction.FromComputer),
    Feature(Capabilities.FILES_WRITE, "Files", "Receive files from your computer in Downloads.", Direction.FromComputer),
    Feature(Capabilities.LINKS_OPEN, "Links", "Links from your computer arrive as a notification you tap to open.", Direction.FromComputer),
    Feature(Capabilities.DEVICE_RING, "Find my phone", "Your computer can ring this phone, even on silent.", Direction.FromComputer),
    Feature(Capabilities.CAMERA_STREAM, "Webcam", "Use this phone's camera and microphone on your computer. You always start it with a tap here.", Direction.FromComputer),
    Feature("screen.view", "Screen sharing", "Show this phone's screen on your computer. Android asks every time.", Direction.FromComputer),
    Feature("screen.control", "Control while sharing", "Let your computer tap and type on this phone while you share its screen.", Direction.FromComputer, Need.Accessibility),
    Feature(Capabilities.DEVICE_STATUS, "Battery and connection", "Show this phone's battery on your computer.", Direction.ToComputer),
    Feature(Capabilities.INPUT_CONTROL, "Trackpad", "Use this phone as a trackpad and keyboard for your computer.", Direction.ToComputer),
    Feature(Capabilities.DND_SET, "Do Not Disturb together", "Turn Do Not Disturb on or off on either device and the other follows.", Direction.Both, Need.NotificationPolicy),
    Feature("messages.read", "Text messages on your computer", "Read and send this phone's text messages in Messages on your computer. Picture messages and RCS chats stay on the phone.", Direction.FromComputer, Need.Sms),
    Feature(Capabilities.BLUETOOTH_BOND, "Calls on your computer", "Answer and make phone calls on your computer over Bluetooth. Call audio stays on this phone until you choose the computer.", Direction.FromComputer),
    Feature(Capabilities.HOTSPOT_REQUEST, "Hotspot", "Your computer can ask to use this phone's mobile connection. You turn it on with one tap.", Direction.FromComputer),
    Feature(Capabilities.AUTH_REQUEST, "Approve with your phone", "Confirm requests from your computer with your fingerprint, face or screen lock.", Direction.FromComputer),
    Feature(Capabilities.INPUT_CONTROL, "Type from your computer", "Use your computer's keyboard to type on this phone while Luma Keyboard is the keyboard.", Direction.FromComputer, Need.Keyboard, LumaKeyboardService.PREFERENCE_TYPING),
    Feature(Capabilities.CLIPBOARD_WRITE, "Send what you copy", "While Luma Keyboard is the keyboard, text you copy goes to your computer. Passwords and codes that apps mark as private are never sent.", Direction.ToComputer, Need.Keyboard, LumaKeyboardService.PREFERENCE_CLIPBOARD),
)

@Composable
private fun Home(linkRevision: Int, onTrackpad: () -> Unit, onUnpaired: () -> Unit) {
    val context = LocalContext.current
    val desktop = Connect.desktop() ?: return onUnpaired()
    var resumed by remember { mutableIntStateOf(0) }
    val lifecycle = LocalLifecycleOwner.current
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_RESUME) resumed++ }
        lifecycle.lifecycle.addObserver(observer)
        onDispose { lifecycle.lifecycle.removeObserver(observer) }
    }
    val notificationsPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { resumed++ }
    LaunchedEffect(Unit) {
        if (Build.VERSION.SDK_INT >= 33 && context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            notificationsPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }
    val pickFiles = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isNotEmpty()) FileSender.send(context, uris)
    }
    var confirmRemove by remember { mutableStateOf(false) }
    var disclosure by remember { mutableStateOf<Need?>(null) }
    var smsBlocked by remember { mutableStateOf(false) }
    val smsPermissions = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { results ->
        resumed++
        // Android refuses SMS permission without a prompt for an app installed from a file until
        // "Allow restricted settings" is chosen in App info, and after "Don't allow" twice.
        if (results.filterKeys { it != Manifest.permission.READ_CONTACTS }.values.any { !it }) smsBlocked = true
    }
    val grant: (Need) -> Unit = { need ->
        when (need) {
            Need.Accessibility, Need.Keyboard -> disclosure = need
            Need.Sms -> smsPermissions.launch(arrayOf(Manifest.permission.READ_SMS, Manifest.permission.SEND_SMS, Manifest.permission.READ_CONTACTS))
            else -> openGrant(context, need)
        }
    }
    GrantDialogs(context, disclosure, onDisclosureClosed = { disclosure = null }, smsBlocked = smsBlocked, onSmsBlockedClosed = { smsBlocked = false })

    // Settings and permissions change while this screen is in the background, and link updates arrive
    // at any time. Both recompose in place; recreating the page would lose the scroll position.
    val revision = resumed + linkRevision
    Page {
        val state = Connect.linkState
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                val color = when (state) {
                    Connect.LinkState.Connected -> Luma.green
                    Connect.LinkState.Paused -> MaterialTheme.colorScheme.onSurfaceVariant
                    else -> Luma.amber
                }
                Box(Modifier.size(9.dp).clip(CircleShape).background(color))
                Text(
                    when (state) {
                        Connect.LinkState.Connected -> "Connected"
                        Connect.LinkState.Paused -> "Paused"
                        else -> "Looking for your computer"
                    },
                    style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(desktop.name, style = MaterialTheme.typography.headlineMedium, modifier = Modifier.semantics { heading() })
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            if (Capabilities.CLIPBOARD_WRITE in desktop.outgoing) {
                Button(onClick = { context.startActivity(Intent(context, ClipboardSendActivity::class.java)) }, modifier = Modifier.weight(1f)) { Text("Send clipboard") }
            }
            if (Capabilities.FILES_WRITE in desktop.outgoing) {
                OutlinedButton(onClick = { pickFiles.launch(arrayOf("*/*")) }, modifier = Modifier.weight(1f)) { Text("Send files") }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            if (Capabilities.INPUT_CONTROL in desktop.outgoing) {
                OutlinedButton(onClick = onTrackpad, modifier = Modifier.weight(1f)) { Text("Trackpad") }
            }
            if (state == Connect.LinkState.Paused) {
                OutlinedButton(onClick = { Connect.resume(); LinkService.start(context) }, modifier = Modifier.weight(1f)) { Text("Resume") }
            } else {
                OutlinedButton(onClick = { Connect.setLinkState(Connect.LinkState.Paused); LinkService.refresh(context) }, modifier = Modifier.weight(1f)) { Text("Pause") }
            }
        }

        Setup(context, desktop, revision, grant)

        Text("What's shared", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(top = 8.dp).semantics { heading() })
        Text(
            "Your computer chose what to allow when you paired. Turn anything off here and it stops right away.",
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Island {
            FEATURES.filter { it.needs != Need.Sms || org.projectluma.connect.messages.SmsAdapter.available(context) }.forEach { feature ->
                val granted = when (feature.direction) {
                    Direction.ToComputer -> feature.capability in desktop.outgoing
                    Direction.FromComputer -> feature.capability in desktop.incoming
                    Direction.Both -> feature.capability in desktop.outgoing || feature.capability in desktop.incoming
                }
                // Do Not Disturb access is only needed when the computer may change this phone's.
                val needsApply = feature.needs != Need.NotificationPolicy || feature.capability in desktop.incoming
                FeatureRow(context, feature, granted, needsApply, revision, grant)
            }
        }

        Island {
            Text("Pairing", style = MaterialTheme.typography.titleMedium)
            Text("This phone's code: ${Connect.identity().pin.take(8)}…${Connect.identity().pin.takeLast(8)}", style = MaterialTheme.typography.bodyMedium, fontFamily = FontFamily.Monospace)
            if (!confirmRemove) {
                TextButton(onClick = { confirmRemove = true }) { Text("Remove ${desktop.name}", color = MaterialTheme.colorScheme.error) }
            } else {
                Text("This phone will stop sharing with ${desktop.name} immediately. Remove this phone in Luma Connect on your computer too.", style = MaterialTheme.typography.bodyMedium)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = { confirmRemove = false }) { Text("Keep") }
                    Button(onClick = {
                        DndSync.removeRule(context)
                        Connect.unpair(desktop.pin)
                        LinkService.stop(context)
                        onUnpaired()
                    }) { Text("Remove") }
                }
            }
        }
    }
}

@Composable
@Suppress("UNUSED_PARAMETER") // revision recomposes the row when the app returns from Settings.
private fun FeatureRow(context: Context, feature: Feature, granted: Boolean, needsApply: Boolean, revision: Int, grant: (Need) -> Unit) {
    var enabled by remember { mutableStateOf(Connect.featureEnabled(feature.preference)) }
    val missing = needsApply && missing(context, feature.needs)
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(feature.title, style = MaterialTheme.typography.labelLarge)
            Text(
                when {
                    !granted -> "Not allowed by your computer. Pair again to change this."
                    missing && feature.needs == Need.NotificationAccess -> "Needs notification access."
                    missing && feature.needs == Need.NotificationPolicy -> "Needs Do Not Disturb access."
                    missing && feature.needs == Need.Keyboard -> "Needs Luma Keyboard turned on."
                    missing && feature.needs == Need.Sms -> "Needs permission to read and send text messages."
                    missing -> "Needs the Luma Connect remote control setting."
                    feature.needs == Need.Keyboard && !isKeyboardCurrent(context) -> feature.detail + " Luma Keyboard isn't your keyboard right now."
                    else -> feature.detail
                },
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (granted && missing) {
                TextButton(onClick = { grant(feature.needs) }, modifier = Modifier.padding(0.dp)) { Text("Turn on") }
            } else if (granted && feature.needs == Need.Keyboard && !isKeyboardCurrent(context)) {
                TextButton(onClick = { context.getSystemService(InputMethodManager::class.java).showInputMethodPicker() }, modifier = Modifier.padding(0.dp)) {
                    Text("Switch keyboard")
                }
            }
        }
        Switch(
            checked = granted && enabled && !missing, enabled = granted && !missing,
            onCheckedChange = { enabled = it; Connect.setFeatureEnabled(feature.preference, it) },
        )
    }
}

private fun missing(context: Context, need: Need): Boolean = when (need) {
    Need.NotificationAccess -> !hasNotificationAccess(context)
    Need.Accessibility -> !hasAccessibility(context)
    Need.NotificationPolicy -> !DndSync.hasAccess(context)
    Need.Keyboard -> !isKeyboardEnabled(context)
    Need.Sms -> listOf(Manifest.permission.READ_SMS, Manifest.permission.SEND_SMS).any { context.checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
    Need.None -> false
}

private class SetupStep(val title: String, val detail: String, val action: String, val onClick: () -> Unit)

/**
 * Everything Android asks for, in one place and in an order that works: restricted settings first
 * for an app installed from a file, then each access the allowed features need, then battery. Steps
 * disappear as they are done, and the island disappears when nothing is left. Luma Keyboard stays
 * in its feature rows because it replaces the person's keyboard.
 */
@Composable
@Suppress("UNUSED_PARAMETER") // revision re-checks the steps when the app returns from Settings.
private fun Setup(context: Context, desktop: Peer, revision: Int, grant: (Need) -> Unit) {
    val needed = FEATURES.filter { feature ->
        val granted = when (feature.direction) {
            Direction.ToComputer -> feature.capability in desktop.outgoing
            Direction.FromComputer -> feature.capability in desktop.incoming
            Direction.Both -> feature.capability in desktop.outgoing || feature.capability in desktop.incoming
        }
        granted && Connect.featureEnabled(feature.preference) && feature.needs != Need.Keyboard &&
            (feature.needs != Need.NotificationPolicy || feature.capability in desktop.incoming) &&
            (feature.needs != Need.Sms || org.projectluma.connect.messages.SmsAdapter.available(context))
    }.map { it.needs }.filter { it != Need.None && missing(context, it) }.toSet()
    val steps = buildList {
        if (Build.VERSION.SDK_INT >= 33 && installedFromFile(context) && needed.any { it == Need.NotificationAccess || it == Need.Accessibility || it == Need.Sms }) {
            add(SetupStep(
                "Allow restricted settings",
                "Android holds back some access for apps installed from a file. In App info, tap ⋮ at the top right and choose Allow restricted settings. If it isn't there, skip this.",
                "App info",
            ) { openAppInfo(context) })
        }
        if (Need.NotificationAccess in needed) add(SetupStep("Notification access", "For notifications and what's playing on your computer.", "Allow") { grant(Need.NotificationAccess) })
        if (Need.Sms in needed) add(SetupStep("Text messages", "So Messages on your computer can read and send your texts.", "Allow") { grant(Need.Sms) })
        if (Need.Accessibility in needed) add(SetupStep("Remote control", "So your computer can tap and type while you share this screen.", "Allow") { grant(Need.Accessibility) })
        if (Need.NotificationPolicy in needed) add(SetupStep("Do Not Disturb", "So Do Not Disturb follows your computer.", "Allow") { grant(Need.NotificationPolicy) })
        if (!context.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(context.packageName)) {
            add(SetupStep("Stay connected", "Some phones stop background apps to save battery. Luma Connect uses almost no power while idle.", "Allow") {
                @Suppress("BatteryLife")
                runCatching { context.startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${context.packageName}"))) }
            })
        }
    }
    if (steps.isEmpty()) return
    Island {
        Text("Finish setting up", style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
        Text(
            if (steps.size == 1) "One step left so everything ${desktop.name} can use works." else "${steps.size} steps left so everything ${desktop.name} can use works.",
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        steps.forEach { step ->
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(step.title, style = MaterialTheme.typography.labelLarge)
                    Text(step.detail, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                FilledTonalButton(onClick = step.onClick) { Text(step.action) }
            }
        }
    }
}

/** Installed by opening an APK rather than from an app store, which is when Android restricts settings. */
private fun installedFromFile(context: Context): Boolean = runCatching {
    val installer = if (Build.VERSION.SDK_INT >= 30) {
        context.packageManager.getInstallSourceInfo(context.packageName).installingPackageName
    } else {
        @Suppress("DEPRECATION") context.packageManager.getInstallerPackageName(context.packageName)
    }
    installer !in setOf("com.android.vending", "com.sec.android.app.samsungapps", "org.fdroid.fdroid")
}.getOrDefault(true)

private fun openAppInfo(context: Context) {
    runCatching { context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${context.packageName}"))) }
}

@Composable
private fun GrantDialogs(context: Context, disclosure: Need?, onDisclosureClosed: () -> Unit, smsBlocked: Boolean, onSmsBlockedClosed: () -> Unit) {
    if (smsBlocked) {
        AlertDialog(
            onDismissRequest = onSmsBlockedClosed,
            title = { Text("Allow text messages in Settings") },
            text = {
                Text(
                    "Android didn't show the permission prompt. In App info:\n\n" +
                        "1. Open Permissions, tap SMS and select Allow.\n" +
                        "2. If it says Restricted setting, go back to App info, tap ⋮ at the top right, choose Allow restricted settings, then repeat step 1.\n\n" +
                        "Come back here when you're done.",
                )
            },
            confirmButton = { TextButton(onClick = { onSmsBlockedClosed(); openAppInfo(context) }) { Text("Open App info") } },
            dismissButton = { TextButton(onClick = onSmsBlockedClosed) { Text("Not now") } },
        )
    }
    when (disclosure) {
        // Prominent disclosure for the input method's clipboard use and remote typing (Google Play User Data policy).
        Need.Keyboard -> AlertDialog(
            onDismissRequest = onDisclosureClosed,
            title = { Text("Turn on Luma Keyboard?") },
            text = {
                Text(
                    "Luma Keyboard is a simple keyboard for this phone. While it is your keyboard, it can read your clipboard and sends text you copy " +
                        "to your paired computer, unless the app you copied from marks it as private, such as a password. It also lets your computer " +
                        "type into the field you have open. It does not record what you type on this phone, and nothing goes anywhere except your paired computer. " +
                        "Android will warn that a keyboard can collect what you type; you can switch back to your usual keyboard at any time.",
                )
            },
            confirmButton = { TextButton(onClick = { onDisclosureClosed(); openGrant(context, Need.Keyboard) }) { Text("Open settings") } },
            dismissButton = { TextButton(onClick = onDisclosureClosed) { Text("Not now") } },
        )
        // Prominent disclosure required by Google Play before sending people to accessibility settings.
        Need.Accessibility -> AlertDialog(
            onDismissRequest = onDisclosureClosed,
            title = { Text("Let your computer control this phone?") },
            text = {
                Text(
                    "Luma Connect uses Android's accessibility service only while you share this phone's screen with your paired computer. " +
                        "It performs the taps, swipes, Back/Home and typing you make on the computer. It does not read, store or send what's on your screen, " +
                        "and it does nothing when no sharing session is running. You can turn it off at any time in Settings.\n\n" +
                        "In Settings, open Installed apps, choose Luma Connect remote control and turn it on.",
                )
            },
            confirmButton = { TextButton(onClick = { onDisclosureClosed(); openGrant(context, Need.Accessibility) }) { Text("Open settings") } },
            dismissButton = { TextButton(onClick = onDisclosureClosed) { Text("Not now") } },
        )
        else -> Unit
    }
}

private fun hasNotificationAccess(context: Context): Boolean {
    val component = ComponentName(context, NotificationMirrorService::class.java)
    return if (Build.VERSION.SDK_INT >= 27) {
        context.getSystemService(android.app.NotificationManager::class.java).isNotificationListenerAccessGranted(component)
    } else {
        Settings.Secure.getString(context.contentResolver, "enabled_notification_listeners").orEmpty().contains(component.flattenToString())
    }
}

private fun hasAccessibility(context: Context): Boolean {
    val manager = context.getSystemService(AccessibilityManager::class.java)
    return manager.getEnabledAccessibilityServiceList(android.accessibilityservice.AccessibilityServiceInfo.FEEDBACK_ALL_MASK)
        .any { it.resolveInfo.serviceInfo.packageName == context.packageName } || RemoteControlService.instance != null
}

private fun isKeyboardEnabled(context: Context): Boolean =
    context.getSystemService(InputMethodManager::class.java).enabledInputMethodList.any { it.packageName == context.packageName }

private fun isKeyboardCurrent(context: Context): Boolean =
    Settings.Secure.getString(context.contentResolver, Settings.Secure.DEFAULT_INPUT_METHOD).orEmpty()
        .startsWith(context.packageName + "/")

private fun openGrant(context: Context, need: Need) {
    val intent = when (need) {
        Need.NotificationAccess -> if (Build.VERSION.SDK_INT >= 30) {
            Intent(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS)
                .putExtra(Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME, ComponentName(context, NotificationMirrorService::class.java).flattenToString())
        } else {
            Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS")
        }
        Need.Accessibility -> Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
        Need.NotificationPolicy -> DndSync.accessIntent()
        Need.Keyboard -> Intent(Settings.ACTION_INPUT_METHOD_SETTINGS)
        Need.Sms, Need.None -> return
    }
    runCatching { context.startActivity(intent) }
}
