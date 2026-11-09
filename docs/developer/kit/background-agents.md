---
title: Background agents
summary: Keep your app working when its windows are closed. Declare a small background agent, handle wakes, publish values for Live Extensions and dock badges, schedule work, and ask for permission.
order: 7
section: Kit
---

<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Background agents

Some apps are only useful if they keep working when their windows are closed:
a message arrives, a reminder is due, an alarm goes off. On Luma such an app
does not keep its whole interface running. It ships a **background agent**: a
small process with no windows that the system starts, wakes and limits.

People see agents only where they look for them. There is no tray icon and no
"running" badge. An app with an agent gets a **Run in the Background** switch in
its dock menu and a row in **Settings › Apps › Background Activity**, and the
agent shows up as the notifications, Live Extensions and badges it produces.

> **Status: preview.** The `luma-background` service (version 0.1, D-Bus
> interface version 2) is part of the Luma desktop image. The Python and C
> APIs below are in `luma_appkit` and `LumaSemantics-1`.

## How it works

`luma-background` is a session service. It reads your declaration, applies the
person's decision, and runs your agent as a systemd user unit named
`app-<app-id>-agent.service` with your category's limits. It wakes the agent at
login, when the network comes online, after sleep, and on schedules. Your agent
owns a D-Bus name, implements `org.projectluma.BackgroundAgent1`, and publishes
values that the dock and Live Extensions read.

It never runs your code with more rights than your app has: a native agent
runs as the person, with your app's own executable; a Flatpak agent runs
through `flatpak run` inside the app's sandbox.

## Declaring an agent

Add a `[background]` table to `luma-app.toml`:

```toml
[background]
agent = "com.example.Usage.Agent"          # D-Bus name the agent owns
exec = "usage-meter --agent"               # your app's executable plus arguments
category = "widget-data"
wake = ["login", "network", "resume", "schedule"]
interval = "15m"                           # optional repeating wake
publishes = ["remaining", "resets-at", "live-extension:com.example.Usage.Remaining"]
```

| Key | Type | Rule |
| --- | --- | --- |
| `agent` | string | *Required.* A D-Bus well-known name that starts with your application ID followed by a dot. The agent must own this name. |
| `exec` | string | *Required.* The command that starts the agent: words separated by spaces, at most 32. Its program must be the program in your desktop file's `Exec` (or, for a Flatpak, the manifest's `command`). No quoting, desktop field codes (`%f`, `%u`), shell syntax or environment assignments. |
| `category` | string | *Required.* One of `communication`, `calendar`, `alarms`, `mail`, `sync`, `widget-data`, `other`. It sets the limits, whether the agent keeps running in Power Saver, and the sentence people read. |
| `wake` | array of strings | *Required.* Any of `login`, `network`, `resume`, `schedule`; at least one. Without `login` the agent starts only when woken or requested. |
| `interval` | string | Optional. A repeating wake such as `"15m"`, `"1h"` or `"6h"`. Units `m`, `h`, `d`; at least `5m`, at most `7d`. Requires `schedule` in `wake`. |
| `publishes` | array of strings | Optional. The value names the agent publishes, and `live-extension:<id>` for each Live Extension it publishes. Value names are 1 to 64 characters of lowercase letters, digits, `-`, `_` and `.`, starting with a letter. |

A schema 0.2 manifest that declares `[background]` must also list
`background` in `distribution.portals`. `luma lint` checks all of these rules,
and `luma-background` applies the same ones to the installed file.

### Where the declaration is installed

`luma-background` reads declarations only from installed places. Install your
`luma-app.toml` (or a file with just `[application]` `id` and `name`, and
`[background]`) as `<app-id>.toml` in:

| App | Path | Notes |
| --- | --- | --- |
| RPM or other system package | `/usr/share/luma/background/<app-id>.toml` | `exec` may name a separate helper if it is an absolute path to a root-owned executable under `/usr`. |
| Flatpak | `share/luma/background/<app-id>.toml` under `/app` | Read from the installed deployment. The agent always runs as `flatpak run --command=<program> <app-id>`. |
| Per-user install | `~/.local/share/luma/background/<app-id>.toml` | `exec`'s program must be the desktop file's `Exec` program. |

A declaration is ignored, with a journal message saying why, unless a desktop
file `<app-id>.desktop` for the same ID is installed and the declaration
passes every rule above.

## Categories and defaults

| Category | For | Memory high / max | Power Saver | People read |
| --- | --- | --- | --- | --- |
| `communication` | Messages, calls, chat | 64 / 128 MiB | Keeps running | Get messages and calls when *App* is closed |
| `calendar` | Event sync and reminders | 64 / 128 MiB | Keeps running | Get event reminders when *App* is closed |
| `alarms` | Alarms and timers | 64 / 128 MiB | Keeps running | Alarms and timers go off when *App* is closed |
| `mail` | New mail | 128 / 256 MiB | Paused | Get new mail when *App* is closed |
| `sync` | Keeping data in step | 64 / 128 MiB | Paused | Keep *App* up to date when it is closed |
| `widget-data` | Values for Live Extensions | 64 / 128 MiB | Paused | Keep *App*'s live information current when it is closed |
| `other` | Anything else | 64 / 128 MiB | Paused | Let *App* work when it is closed |

Every agent also gets CPU weight 20 and IO weight 20, and is restarted after a
crash with a growing delay (2 seconds, up to 5 minutes). "Paused" means the
agent's processes are frozen while Power Saver is on and thawed when it ends;
wakes that arrive meanwhile are delivered then. Memory above *high* is
reclaimed and the agent slowed; an agent that reaches *max* is stopped by the
kernel and restarted like a crash. Swap is limited to the *high* value. An
agent stalled at *high* long enough to cause memory pressure is stopped
before anything the person is using.

Who is on by default:

- **A few system-installed Luma apps** (messaging, phone, calendar, clock,
  mail and Luma Connect) are on. Settings shows them as **Always On**; people
  can still turn them off and are told what they will miss.
- **Everything else** asks once, the first time the app requests background
  activity: "Let *App* work in the background?" with the category sentence,
  **Allow** or **Don't Allow**. The answer is remembered and can be changed in
  Settings or the dock menu.

Only apps the system installed can be on by default, and an app cannot make
itself Always On.

## Writing an agent in Python

`luma_appkit.background` does not import GTK, so your agent process stays
small because it never loads your interface.

```python
# SPDX-License-Identifier: Apache-2.0
import sys
from datetime import timedelta

from luma_appkit.background import Agent, LiveExtensionBinding, from_agent, run_agent


class UsageAgent(Agent):
    app_id = "com.example.Usage"
    agent_id = "com.example.Usage.Agent"

    def on_start(self):
        # A Live Extension fed by this agent's own values. The agent publishes
        # it, so it stays current with every window closed.
        self.remaining = LiveExtensionBinding(
            self,
            extension_id="com.example.Usage.Remaining",
            category="generic",
            title="Usage",
            subtitle=lambda v: f"{round(v['remaining'] * 100)}% left",
            progress=from_agent(self.agent_id, "remaining"),
            expires_in=timedelta(minutes=30),
        )

    def on_wake(self, wake):
        # wake.reason: "login", "network", "resume", "schedule" or "request"
        usage = fetch_usage()                  # your code
        self.publish("remaining", usage.fraction_left)
        self.publish("resets-at", usage.resets_at.isoformat())
        if usage.fraction_left < 0.1:
            self.notify("usage-low", "Usage is almost used up",
                        f"It resets at {usage.resets_at:%H:%M}.")


if __name__ == "__main__":
    status = run_agent(UsageAgent)             # None unless --agent was passed
    if status is not None:
        sys.exit(status)
    from usage_meter.app import main           # import GTK only for the UI
    sys.exit(main())
```

### `class Agent`

| Member | What it does |
| --- | --- |
| `app_id`, `agent_id` | Class attributes. `agent_id` must start with `app_id + "."`. |
| `on_start()` | Called once, after the agent owns its bus name and before the first wake. |
| `on_wake(wake)` | Called for every wake. `wake.reason` is `login`, `network`, `resume`, `schedule` or `request`; `wake.schedule` names the schedule; `wake.missed` is `True` when a scheduled time passed while the computer was off or asleep. Wakes that arrive while one is running are queued, never run at the same time. An exception in one wake is logged and does not end the agent. |
| `on_stop()` | Called when the agent stops. Keep it short: the process is killed after 10 seconds. |
| `publish(name, value)` | Sets a published value: `bool`, `int`, `float`, `str`, a list of `str`, or a `dict` of those. Changes are announced once per main-loop turn. |
| `set_badge(badge)` | Sets the [`badge` value](#unread-badges): a count (`int`, 0 or more), `BADGE_DOT`, or `None` to stop publishing it. Raises `ValueError` for anything else. |
| `unpublish(name)` | Removes a value. |
| `values` | A read-only mapping of what is published. |
| `schedule(name, *, at=None, every=None)` | Asks the system to wake the agent at a timezone-aware `datetime` (`at`, one-shot, at most 400 days ahead, caught up after sleep) or on a repeating `timedelta` (`every`, at least 5 minutes). Needs `schedule` in your declaration's `wake`. Replaces an existing schedule of that name. Names are up to 40 lowercase letters, digits and `-`; `interval` is reserved. At most 64 schedules per app. |
| `unschedule(name)` | Removes a schedule. |
| `notify(id, title, body="", *, priority="normal", open_app=True)` | Sends a notification that belongs to your app; `priority` is `low`, `normal`, `high` or `urgent`. Activating it opens your app. Uses the notification portal inside Flatpak. |
| `open_app()` | Launches or raises your app. |
| `quit(status=0)` | Ends the agent cleanly. A clean exit is not a crash: the agent starts again only at its next wake. Use it when there is nothing left to do, for example a clock with no alarms. |
| `run(argv=None)` | Registers on the session bus and runs the main loop. Returns an exit status. |
| `start()`, `stop()` | What `run()` does before and after its loop, for an agent that shares a main loop you already run. |

`run_agent(AgentClass, argv=None)` returns `None` unless `--agent` is in
`argv` (default `sys.argv`), so the same executable serves the app and its
agent, as the declaration's `exec` expects.

Agents are started by `luma-background`, not by your app. Running
`usage-meter --agent` by hand works for development; the agent then logs that
it is unmanaged and receives no wakes.

### Asking for background activity

```python
from luma_appkit.background import request_background

def on_first_account_added(window):
    request_background(
        "com.example.Usage",
        reason="Show your remaining usage on the shelf",
        autostart=True,
        callback=lambda result: print(result.allowed, result.autostart),
    )
```

`request_background(app_id, *, reason, autostart=True, commandline=None,
parent_window="", callback=None)` uses the standard Background portal
(`org.freedesktop.portal.Background.RequestBackground`, with `--agent` appended
to your command when `commandline` is `None`) when the app is sandboxed, and
`org.projectluma.Background1.RequestBackground` when it is not. Both reach the
same decision. `reason` is at most 256 characters. The callback receives a
`BackgroundResult` with `allowed`, `autostart` and `response` (`0` allowed,
`1` declined, `2` no answer).

Ask at a moment the person will understand, such as after they add an account.
Asking at launch is rejected at review. An app that is already allowed gets an
immediate answer and no prompt. An app that was declined, or whose prompt was
dismissed, gets `allowed=False` at once and no second prompt. People change
their mind in Settings.

### Live Extensions from agent values

`from_agent(bus_name, value_name)` is a binding: it reads a value an agent
publishes and follows its changes. `LiveExtensionBinding` turns bindings into a
[Live Extension](semantics-and-actions.md#live-extensions) and keeps it
published through the Semantic Broker:

```python
LiveExtensionBinding(
    owner,                 # an Agent, or a registered Gio.Application
    extension_id="com.example.Usage.Remaining",
    category="generic",    # a Live Extension category
    title="Usage",         # a str, a binding, or a callable taking the values dict
    subtitle=lambda v: f"{round(v['remaining'] * 100)}% left",
    progress=from_agent("com.example.Usage.Agent", "remaining"),
    privacy="private",
    expires_in=timedelta(minutes=30),
    visible=lambda v: "remaining" in v,
)
```

- Inside the agent, a binding to the agent's own name reads the value directly.
  Anywhere else it reads the agent's `Values` property over D-Bus, follows its
  changes, and reports the value as missing while the agent is not running. A
  binding never starts an agent.
- `visible` (default: every bound value is present) decides whether the
  extension is published at all. When it becomes false the extension is
  withdrawn, so a stopped agent never leaves stale information on screen.
- The extension is republished before `expires_in` runs out while it stays
  visible. `expires_in` is at least one minute.
- The Semantic Broker attributes an extension published by an agent to the
  agent's app, because the agent runs in `app-<app-id>-agent.service`.

### Unread badges

An agent that knows something waits for the person (unread messages, missed
calls) shows it as a badge on the app's dock icon by publishing the standard
value **`badge`**:

| Value | Type | The dock shows |
| --- | --- | --- |
| A count | `u` (any integer type is read) | The count, from 1 to 99, then "99+". Screen readers hear the real number. |
| `"dot"` | `s` | A dot: something new without a number. |
| `0`, or anything else | | Nothing. |

```python
from luma_appkit.background import BADGE_DOT

self.set_badge(unread)        # publishes badge = unread, as an unsigned count
self.set_badge(BADGE_DOT)     # something new, no number
self.set_badge(None)          # stop publishing a badge
```

- **Declare it.** List `badge` in `publishes`. The dock listens only to agents
  that declare it, and never starts an agent to read it.
- **Only what waits for the person.** A badge is never for running, syncing or
  updating.
- **The agent owns the badge.** While your agent publishes `badge`, including
  `0`, the dock ignores other badge sources for your app, such as
  `com.canonical.Unity.LauncherEntry` updates. When the agent stops or
  unpublishes the value, the badge goes away.
- **People decide.** Settings › Notifications › *App* › **Badge app icon**
  turns an app's badge off. Do Not Disturb does not hide badges.
- In C, publish it like any value:
  `luma_background_agent_publish (agent, "badge", g_variant_new_uint32 (3), NULL)`.

## Writing an agent in C

`LumaBackgroundAgent` in `LumaSemantics-1` (`pkg-config luma-semantics-1`) is
the C and introspection equivalent of the Python class.

```c
#include <luma-semantics.h>

static void
on_wake (LumaBackgroundAgent *agent, const char *reason, GVariant *details, gpointer data)
{
  luma_background_agent_publish (agent, "unread", g_variant_new_uint32 (3), NULL);
}

int
main (int argc, char **argv)
{
  g_autoptr (LumaBackgroundAgent) agent =
    luma_background_agent_new ("org.example.Chat", "org.example.Chat.Agent");
  g_signal_connect (agent, "wake", G_CALLBACK (on_wake), NULL);
  return luma_background_agent_run (agent, argc, argv);
}
```

`LumaBackgroundAgent` provides `publish`, `unpublish`, `dup_values`,
`schedule_at`, `schedule_every`, `unschedule`, `register` (for an agent that
shares a main loop you already run), `run`, `quit`, the `start`, `wake` and
`stop` signals, and `luma_background_agent_is_requested (argc, argv)` for the
`--agent` switch. `wake` receives the reason and an `a{sv}` with optional
`schedule` (s) and `missed` (b). Errors are in the
`LUMA_BACKGROUND_AGENT_ERROR` domain: `INVALID`, `NOT_RUNNING` and `REFUSED`.

## The D-Bus contract

Use the Python or C API when you can. This is what those APIs use, and what
another toolkit would implement.

### `org.projectluma.BackgroundAgent1`

Implemented by every agent on its own bus name, object
`/org/projectluma/BackgroundAgent1`.

```xml
<interface name="org.projectluma.BackgroundAgent1">
  <method name="Wake">
    <arg name="reason" type="s" direction="in"/>
    <arg name="details" type="a{sv}" direction="in"/>
  </method>
  <property name="AppId" type="s" access="read"/>
  <property name="Values" type="a{sv}" access="read"/>
</interface>
```

`Wake` is accepted only from the owner of `org.projectluma.Background1`;
`details` may contain `schedule` (s) and `missed` (b). `Values` changes are
announced with `org.freedesktop.DBus.Properties.PropertiesChanged`. A client
reads a single value `v` as `Values[v]`.

### `org.projectluma.Background1`

The service: bus name and interface `org.projectluma.Background1`, object
`/org/projectluma/Background1`, on the session bus. Callers are identified
from their D-Bus credentials (the process's systemd unit or Flatpak sandbox),
never from an argument. The methods an app uses:

| Method | Who may call | Behaviour |
| --- | --- | --- |
| `RequestBackground(s parent_window, a{sv} options) → (u response, a{sv} results)` | A native app | Options `reason` (s, at most 256 characters) and `autostart` (b). Results `background` (b) and `autostart` (b). Response 0 allowed, 1 declined, 2 no answer. If allowed with `autostart`, the agent is started with wake `request`. Sandboxed apps must use the Background portal instead. |
| `Schedule(s name, a{sv} options)` | The agent or its app | `at` (x, Unix seconds) *or* `every` (u, seconds, at least 300), and optional `accuracy` (u, seconds; default 1 for `alarms`, `calendar` and `communication`, 60 otherwise). |
| `Unschedule(s name)` | The agent or its app | Removes a schedule. |
| `StopNow(s app_id)` | Settings, the dock, or the app itself | Stops the running agent until its next wake. |
| `SetAllowed(s app_id, b allowed)` | Settings and the dock; an app may pass `false` for itself | Records the person's decision and starts or stops the agent. |

`ListAgents`, `GetAgent`, `Wake`, the login-item methods and the
`AgentsChanged` and `AgentChanged` signals are for Settings and the dock. Use
`luma-background status <app-id> --json` to see the agent record they read.
Errors are `org.projectluma.Background1.Error.NotFound`, `.NotAuthorized` and
`.InvalidArgument`.

### The Background portal

Flatpak apps use the standard `org.freedesktop.portal.Background`. On Luma,
`luma-background` answers that portal, so sandboxed apps get the same
decision, prompt and Settings switch as everything else:

- A change made through the portal's own permission tools is taken as the
  person's decision.
- An autostart the portal records for an app is run as that app's agent,
  inside its sandbox, under the same limits and switch. It is not run a second
  time by the session's autostart.

### Autostart entries while the agent is managed

An app may keep an autostart entry for sessions without `luma-background`.
Name the agent in it so the service can tell it apart:

```ini
[Desktop Entry]
Type=Application
Name=Usage Meter
Exec=usage-meter --agent --autostart
NoDisplay=true
X-Luma-Background-Agent=com.example.Usage.Agent
```

While the service runs an app's agent, every autostart entry that would start
the app a second time is masked: the portal's entry for the app and any entry
naming the app's agent with `X-Luma-Background-Agent`. The entry's file name
must use only letters, digits, `_`, `.` and `-` to be masked. A native app that
is managed should not ask the Background portal for autostart.

### Waking the computer for an alarm

A session process cannot wake a suspended computer. `luma-background` includes
`org.projectluma.BackgroundWake1` (object `/org/projectluma/BackgroundWake1`)
on the **system** bus, which can, for the active local session:

```xml
<interface name="org.projectluma.BackgroundWake1">
  <method name="SetWakeup">
    <arg name="name" type="s" direction="in"/>
    <arg name="time" type="x" direction="in"/>
  </method>
  <method name="ClearWakeup">
    <arg name="name" type="s" direction="in"/>
    <arg name="cleared" type="b" direction="out"/>
  </method>
  <method name="ListWakeups">
    <arg name="wakeups" type="a(sx)" direction="out"/>
  </method>
  <property name="Version" type="u" access="read"/>
</interface>
```

| Method | Behaviour |
| --- | --- |
| `SetWakeup` | Wakes the computer at `time` (Unix seconds, 5 seconds to 31 days ahead). Use your application ID as `name`; setting it again moves that wake-up. Errors `…Error.NotAuthorized`, `…Error.InvalidArgument`, `…Error.LimitReached` (eight per person). |
| `ClearWakeup` | Removes the wake-up of that name. |
| `ListWakeups` | The caller's own wake-ups. |

The polkit action `org.projectluma.background.wake-system` allows this for
the active local session without asking, and refuses it otherwise. Wake-ups
do not survive a reboot. Waking is all it does: ring from your own wall-clock
timer, and hold sleep off while you ring (logind `Inhibit` with `sleep:idle`
and `block`). Ask for a wake-up only for something the person set to go off,
a little before it is due, and clear it when there is nothing left.

## Testing an agent

```sh
luma lint luma-app.toml                      # checks [background]
luma-background list                         # what the service sees
luma-background status com.example.Usage     # one agent; add --json for every field
journalctl --user LUMA_APP_ID=com.example.Usage
```

The `luma-background` command also has `wake`, `allow`, `deny` and `stop`,
but they call methods that only Settings and the dock may use, so from a
terminal they are refused. To start your agent with a `request` wake, turn its
switch off and on in Settings › Apps › Background Activity or the dock menu.
A declared `interval` gives you repeating `schedule` wakes. The service logs
every start, wake, crash, limit and decision with the journal fields
`LUMA_APP_ID` and `LUMA_BACKGROUND_EVENT`.

## Review rules

- Declare only what the agent does. An agent in `communication`, `calendar` or
  `alarms` that does not deliver those things is rejected.
- Do not keep a window, a GTK context or a WebView in the agent.
- Do not ask at launch, and never ask twice.
- Handle `on_wake` in well under a minute; long transfers must be resumable.
- Publish values, not interface. Live Extensions are drawn by the system.
