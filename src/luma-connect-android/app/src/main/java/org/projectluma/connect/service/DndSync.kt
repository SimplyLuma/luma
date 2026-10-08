package org.projectluma.connect.service

import android.app.AutomaticZenRule
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.service.notification.Condition
import androidx.annotation.RequiresApi
import androidx.core.content.ContextCompat
import org.projectluma.connect.R
import org.projectluma.connect.core.Connect
import org.projectluma.connect.protocol.Capabilities
import org.projectluma.connect.protocol.ProtocolException
import org.projectluma.connect.ui.MainActivity

/**
 * Do Not Disturb shared with the paired computer (`dnd.set`, both directions, `{on: bool}`).
 *
 * Receiving needs Notification Policy access, which the person grants in
 * Settings (`Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS`).
 *
 * - Android 15 (API 35) and newer: apps targeting API 35+ "can no longer change the global
 *   state or policy of Do Not Disturb"; they contribute an `AutomaticZenRule` instead
 *   (developer.android.com/about/versions/15/behavior-changes-15, "Changes to when apps can
 *   modify the global state of Do Not Disturb mode"). Luma Connect owns one rule,
 *   "Luma Connect – focus on computer", created with `NotificationManager.addAutomaticZenRule`
 *   (API 24) from `AutomaticZenRule.Builder` (API 35) and switched with
 *   `NotificationManager.setAutomaticZenRuleState(id, Condition)` (API 29). The condition uses
 *   `Condition.SOURCE_USER_ACTION` (API 35) because each change starts with a person switching
 *   Do Not Disturb on the computer; the platform otherwise ignores an app's state while the
 *   person has overridden the rule (NotificationManager#setAutomaticZenRuleState javadoc).
 *   Turning it off only ends Luma Connect's mode: another mode the person started stays on.
 * - Android 10–14: `NotificationManager.setInterruptionFilter` (API 23) with
 *   `INTERRUPTION_FILTER_PRIORITY` or `INTERRUPTION_FILTER_ALL`.
 *
 * Sending: `NotificationManager.ACTION_INTERRUPTION_FILTER_CHANGED` (API 23) "is only sent
 * to registered receivers", so the persistent link registers [register] while it runs.
 *
 * Echo suppression is by state in both directions: the last value received from or sent
 * to the computer is never sent again, so applying the computer's value does not bounce back.
 */
object DndSync {
    private const val RULE_NAME = "Luma Connect – focus on computer"
    private const val PREFERENCE_RULE = "dnd.rule"
    private val CONDITION: Uri = Uri.parse("luma-connect://dnd/computer")
    const val ID_ACCESS = 40

    @Volatile private var synced: Boolean? = null

    private fun manager(context: Context) = context.getSystemService(NotificationManager::class.java)

    fun hasAccess(context: Context): Boolean = manager(context).isNotificationPolicyAccessGranted

    fun accessIntent(): Intent = Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS)

    /** True when anything other than "all notifications" is in effect. */
    fun isOn(context: Context): Boolean = when (manager(context).currentInterruptionFilter) {
        NotificationManager.INTERRUPTION_FILTER_ALL, NotificationManager.INTERRUPTION_FILTER_UNKNOWN -> false
        else -> true
    }

    /** `dnd.set` receiver adapter. */
    fun apply(context: Context, payload: Map<String, Any?>): Map<String, Any?> {
        if (payload.keys != setOf("on")) throw ProtocolException("invalid do not disturb")
        val on = payload["on"] as? Boolean ?: throw ProtocolException("invalid do not disturb")
        if (!hasAccess(context)) {
            askForAccess(context)
            return mapOf("error" to "needs-user")
        }
        synced = on
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.VANILLA_ICE_CREAM) {
            setRule(context, on)
        } else {
            manager(context).setInterruptionFilter(
                if (on) NotificationManager.INTERRUPTION_FILTER_PRIORITY else NotificationManager.INTERRUPTION_FILTER_ALL,
            )
        }
        return mapOf("on" to isOn(context))
    }

    @RequiresApi(Build.VERSION_CODES.VANILLA_ICE_CREAM)
    private fun setRule(context: Context, on: Boolean) {
        val manager = manager(context)
        val id = ruleId(context)
        manager.setAutomaticZenRuleState(
            id,
            Condition(CONDITION, context.getString(if (on) R.string.dnd_rule_on else R.string.dnd_rule_off),
                if (on) Condition.STATE_TRUE else Condition.STATE_FALSE, Condition.SOURCE_USER_ACTION),
        )
    }

    @RequiresApi(Build.VERSION_CODES.VANILLA_ICE_CREAM)
    private fun ruleId(context: Context): String {
        val manager = manager(context)
        val preferences = context.getSharedPreferences("luma-connect-features", Context.MODE_PRIVATE)
        preferences.getString(PREFERENCE_RULE, null)?.let { if (manager.getAutomaticZenRule(it) != null) return it }
        // Rules outlive cleared app data (but not uninstalling); adopt ours instead of adding a duplicate.
        manager.automaticZenRules.entries.firstOrNull { it.value.conditionId == CONDITION }?.let { (existing, _) ->
            preferences.edit().putString(PREFERENCE_RULE, existing).apply()
            return existing
        }
        val rule = AutomaticZenRule.Builder(RULE_NAME, CONDITION)
            .setType(AutomaticZenRule.TYPE_OTHER)
            // A rule needs an owner service or a configuration activity in the calling package.
            .setConfigurationActivity(ComponentName(context, MainActivity::class.java))
            .setInterruptionFilter(NotificationManager.INTERRUPTION_FILTER_PRIORITY)
            .setTriggerDescription(context.getString(R.string.dnd_rule_trigger))
            .setIconResId(R.drawable.ic_notification)
            .build()
        val id = manager.addAutomaticZenRule(rule) ?: throw IllegalStateException("mode not created")
        preferences.edit().putString(PREFERENCE_RULE, id).apply()
        return id
    }

    /** Removes Luma Connect's mode, for example when the phone is unpaired. */
    fun removeRule(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.VANILLA_ICE_CREAM || !hasAccess(context)) return
        val preferences = context.getSharedPreferences("luma-connect-features", Context.MODE_PRIVATE)
        preferences.getString(PREFERENCE_RULE, null)?.let { runCatching { manager(context).removeAutomaticZenRule(it) } }
        preferences.edit().remove(PREFERENCE_RULE).apply()
    }

    private fun askForAccess(context: Context) {
        val open = PendingIntent.getActivity(
            context, ID_ACCESS, accessIntent().addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE,
        )
        Notifications.manager(context).notify(
            ID_ACCESS,
            Notifications.builder(context, Notifications.INCOMING)
                .setContentTitle(context.getString(R.string.dnd_needs_access_title, Connect.desktop()?.name ?: ""))
                .setContentText(context.getString(R.string.dnd_needs_access_text))
                .setContentIntent(open)
                .setAutoCancel(true)
                .build(),
        )
    }

    /** Called when this phone's Do Not Disturb state changes. */
    fun onPhoneChanged(context: Context) {
        val on = isOn(context)
        synchronized(this) {
            if (synced == on) return
            synced = on
        }
        if (Connect.canSend(Capabilities.DND_SET)) {
            Connect.send(Capabilities.DND_SET, mapOf("on" to on))
        }
    }

    /**
     * Starts observing this phone's Do Not Disturb. Returns the call that stops it.
     * LinkService: `private var stopDnd: (() -> Unit)? = null`, `stopDnd = DndSync.register(this)` in
     * `onCreate`, and `stopDnd?.invoke()` in `onDestroy`.
     */
    fun register(context: Context): () -> Unit {
        synced = isOn(context)
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                if (intent.action == NotificationManager.ACTION_INTERRUPTION_FILTER_CHANGED) onPhoneChanged(context)
            }
        }
        ContextCompat.registerReceiver(
            context, receiver, IntentFilter(NotificationManager.ACTION_INTERRUPTION_FILTER_CHANGED), ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        return { runCatching { context.unregisterReceiver(receiver) } }
    }
}
