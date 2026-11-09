---
title: Semantics and actions
summary: Stable action IDs, risk and privacy classes, semantic objects, publishing to the Semantic Broker, how other apps get access, and Live Extensions.
order: 6
section: Kit
---

# Semantics and actions

People reach your app through more than its pixels: keyboard shortcuts, screen
readers, the shell, and tools they approve. Luma Semantics gives all of them
one description of what your app contains and what it can do, with privacy and
risk attached.

> **Status: preview.** `LumaSemantics-1` and the Semantic Broker are version
> 0.1, and the wire contract can change. Semantics exist only on Luma; your app
> must work normally when the broker is absent.

## The model

- **GTK and AT-SPI stay the source of truth for visible controls.** Give every
  control an accessible name and role first.
- A **semantic object** is a domain thing that labels and pixels do not
  reliably describe: a note, a conversation, a calendar event, a selection.
- A **semantic action** is something that can be done to an object: create,
  save, send, move to Trash. Actions carry no presentation.
- Each meaningful operation has **one stable action ID**. The button, the menu
  item, the shortcut, the Live Extension control and the semantic action all
  refer to it.

## Action IDs

Action IDs are dotted ASCII identifiers such as `note.new` or `playback.pause`:
a letter first, then letters, digits, `_` or `-`, with at least one dot. Treat
them like public API: never rename or reuse a shipped ID, because shortcuts,
automation and granted permissions refer to it.

In `luma_appkit`, `Command.id` is the action ID. See
[commands and menus](commands-and-menus.md).

## Risk

Every action has a risk class. Choose the highest one that honestly applies.

| Risk | Meaning | Examples | Confirmation |
| --- | --- | --- | --- |
| `passive` | Reads or navigates only | Show, search, scroll | No |
| `low` (default) | Easily reversed, affects only the app | Create a note, play, select | No |
| `consequential` | Hard to reverse or visible to others | Send a message, publish, place a call, purchase | A system confirmation every time |
| `destructive` | Deletes or overwrites data | Delete, move to Trash, discard changes | A system confirmation every time |
| `security-sensitive` | Changes access, credentials or trust | Share access, change a password, approve a device | A system confirmation every time |

`luma_action_get_requires_confirmation()` returns true for the last three.
Holding a grant never skips that confirmation.

## Privacy

Every object has a privacy class.

| Privacy | Meaning | Examples |
| --- | --- | --- |
| `public` | Fine to show to an approved observer | The app itself, its top-level sections |
| `private` (default) | Personal content | A note, an event, a contact |
| `sensitive` | Content with extra harm if exposed | Health data, messages, location history |
| `secret` | Must never leave the app | Passwords, tokens, one-time codes |

Secret objects are never exported, and secret children are dropped even when
their parent is visible. While the session is locked, the broker shows no
surfaces at all.

## Building objects and actions

### C

```c
#include <luma-semantics.h>   /* pkg-config: luma-semantics-1 */

g_autoptr(LumaSemanticObject) root =
    luma_semantic_object_new ("notes", "application", "Notes");
luma_semantic_object_set_privacy (root, LUMA_PRIVACY_PUBLIC);
g_autoptr(LumaAction) create = luma_action_new ("notes.create", "Create note");
luma_semantic_object_add_action (root, create);

g_autoptr(LumaSemanticObject) note =
    luma_semantic_object_new ("note:42", "document", "Groceries");
g_autoptr(LumaAction) trash = luma_action_new ("notes.delete", "Move note to Trash");
luma_action_set_risk (trash, LUMA_ACTION_RISK_DESTRUCTIVE);
luma_semantic_object_add_action (note, trash);
luma_semantic_object_add_child (root, note);

g_autoptr(GVariant) surface = luma_semantic_object_to_variant (root);
```

### Python, through introspection

```python
gi.require_version("LumaSemantics", "1")
from gi.repository import LumaSemantics

root = LumaSemantics.SemanticObject.new("notes", "application", "Notes")
root.set_privacy(LumaSemantics.Privacy.PUBLIC)
root.add_action(LumaSemantics.Action.new("notes.create", "Create note"))

note = LumaSemantics.SemanticObject.new("note:42", "document", "Groceries")
trash = LumaSemantics.Action.new("notes.delete", "Move note to Trash")
trash.set_risk(LumaSemantics.ActionRisk.DESTRUCTIVE)
note.add_action(trash)
root.add_child(note)
```

`to_variant()` on that note produces:

```text
{'schema_version': <'0.1'>, 'id': <'note:42'>, 'kind': <'document'>,
 'name': <'Groceries'>, 'privacy': <'private'>,
 'actions': <[{'id': <'notes.delete'>, 'label': <'Move note to Trash'>,
               'enabled': <true>, 'risk': <'destructive'>}]>,
 'children': <@aa{sv} []>}
```

### API summary

| `LumaAction` | |
| --- | --- |
| `luma_action_new (id, label)` | Construct. `id` and `label` are construct-only properties. |
| `_get/_set_description` | Optional longer text |
| `_get/_set_parameter_type` | A GVariant type string for the parameter, or none. The broker accepts `b`, `d`, `s`, `x` and `a{sv}`. |
| `_get/_set_risk` | `LumaActionRisk`, default `LUMA_ACTION_RISK_LOW` |
| `_get/_set_enabled` | Default true |
| `_get_requires_confirmation` | True for consequential, destructive and security-sensitive |
| `_to_variant` | `a{sv}` with `id`, `label`, `enabled`, `risk`, and optional `description` and `parameter_type` |

| `LumaSemanticObject` | |
| --- | --- |
| `luma_semantic_object_new (id, kind, name)` | `id` and `kind` are construct-only; `name` can change |
| `_get/_set_privacy` | `LumaPrivacy`, default `LUMA_PRIVACY_PRIVATE` |
| `_add_action`, `_get_actions` | Actions as a `GListModel` |
| `_add_child`, `_get_children` | Child objects as a `GListModel` |
| `_to_variant` | The object tree as `a{sv}`, with `schema_version` `0.1` |

String helpers: `luma_action_risk_to_string()`, `luma_privacy_to_string()`,
`luma_live_category_to_string()`.

## Publishing to the Semantic Broker

The per-user **Semantic Broker** (`org.projectluma.SemanticBroker1` on the
session bus) is the only way for anything outside your app to discover or
invoke your semantics. Your app registers a surface and exposes one provider
method, `org.projectluma.SemanticProvider1.InvokeAction`, which only the broker
may call.

Python apps can use `SemanticPublisher` from `luma_semantic_broker.client`,
which ships with LumaUI:

```python
try:
    from luma_semantic_broker.client import SemanticPublisher
except (ImportError, ValueError):
    SemanticPublisher = None   # not on Luma, or broker not installed

def publish_semantics(self, application):
    if SemanticPublisher is None:
        return None
    try:
        return SemanticPublisher(
            application,                 # a registered Gio.Application
            "com.example.Notebook",      # your app ID
            "notes",                     # a stable surface ID
            lambda: self.semantic_root,  # returns a LumaSemantics object tree
            {
                "notes.create": self.semantic_create,   # (object_id, parameter) -> result
                "notes.delete": self.semantic_delete,
            },
        )
    except (GLib.Error, OSError, RuntimeError, TypeError, ValueError):
        return None
```

- Call `publisher.update()` after your content changes. Publishing never
  blocks your main loop: updates are queued and coalesced to the latest state,
  and the surface is published again if the broker restarts.
  `publication_id` is set once registration succeeds.
- Action handlers receive the object ID and the parameter, run the same code
  your command runs, and return a small JSON-like result. Raise `ValueError`
  for a bad target; the caller is told the action failed. The surface is
  republished after each action.
- Call `publisher.close()` when the window closes.
- The broker identifies your app from its Flatpak sandbox or its systemd
  launch scope, and the app ID you pass must match. Flatpak apps need
  `--talk-name=org.projectluma.SemanticBroker1`. That lets your app reach the
  broker; it does not grant anyone access to your app.

Limits: 16 surfaces per app, 256 KiB per surface, 512 KiB per message, 512
objects and 16 levels per surface, 32 actions per object, and 120 broker calls
a minute per caller. The broker validates everything before accepting it.

## How access is granted

Nothing gets ambient access to your app. A client asks the broker for scopes
on one app that has a published surface, and the broker shows a system consent
dialog:

| Scope | Allows |
| --- | --- |
| `observe-public` | Reading public objects |
| `observe-private` | Reading private and sensitive objects (secret objects are never exported) |
| `invoke-low-risk` | Invoking passive and low-risk actions |
| `invoke-consequential` | Invoking consequential actions, each with confirmation |
| `invoke-destructive` | Invoking destructive actions, each with confirmation |
| `invoke-security-sensitive` | Invoking security-sensitive actions, each with confirmation |

Invoking an action also requires the observe scope for the target's privacy
class. A grant lasts one hour unless the client asks for longer, up to 30
days. Only sandboxed clients can keep a grant beyond the current session. The
broker identifies callers from the session bus and their Flatpak or systemd
metadata, never from a name the caller supplies. It keeps a local audit of
decisions and IDs, capped at 1 MiB, never of payloads, parameters or results.

## Inspecting your app

The SDK's `luma semantic` command is an ordinary broker client with no bypass.
It runs under its own identity, `org.projectluma.SemanticInspector`, so its
grants last for your session:

```sh
luma semantic request com.example.Notebook \
  --scope observe-public --scope observe-private --scope invoke-low-risk
luma semantic list
luma semantic get PUBLICATION_ID
luma semantic invoke PUBLICATION_ID notes notes.create --parameter '{}'
luma semantic audit
luma semantic revoke com.example.Notebook
```

`request` takes `--duration` in seconds; `audit` takes `--limit`.

## Live Extensions

A Live Extension is a short-lived, system-drawn view of something happening
now: a call, media playback, a timer, navigation, an upcoming event, a
transfer, a recording or an installation. You supply data; the shell draws it
in the top bar. Your code never renders in the shell.

| Field | Rule |
| --- | --- |
| `id`, `app_id` | Stable IDs; `app_id` must be the publishing app |
| `category` | `call`, `media`, `timer`, `navigation`, `event`, `transfer`, `recording`, `installation`, `generic` |
| `title`, `subtitle` | Short text: at most 256 and 512 bytes |
| `progress` | `-1` for none, or 0 to 1 |
| `starts_at`, `expires_at` | ISO 8601 with a time zone; an expiry is required, at most 7 days out |
| `privacy` | While the session is locked, a non-public extension shows as "Ongoing activity", with no subtitle and no actions |
| actions | At most three |

```python
ext = LumaSemantics.LiveExtension.new("timer-1", "com.example.Timer",
                                      LumaSemantics.LiveCategory.TIMER, "Tea")
ext.set_expires_at("2026-10-08T18:05:00-05:00")
ext.set_progress(0.4)
ext.add_action(LumaSemantics.Action.new("timer.pause", "Pause"))
```

A fourth `add_action` fails with "A Live Extension may expose at most three
actions". The broker allows four Live Extensions per app.

To publish, Python apps use `LiveExtensionPublisher` from
`luma_semantic_broker.client`: give it your registered `Gio.Application`, your
app ID, the extension ID and a function that returns the extension (or `None`
to withdraw it), then call `sync()` when it changes and `close()` when it
ends. An app whose information should stay current with its windows closed
can publish from a [background agent](background-agents.md#live-extensions-from-agent-values)
with `LiveExtensionBinding`, which handles this for you. When someone presses
a control on your extension, the broker calls
`org.projectluma.SemanticProvider1.InvokeAction` at the provider path you
registered with; `LiveExtensionPublisher` does not answer that call, so an
extension with actions needs your own provider object.

Check a payload before you publish it with `luma live-preview activity.json`.
