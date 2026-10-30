# Ari

Ari's native window and CLI use the same session-bus daemon. The daemon owns
model connections, conversations, tool permissions, approvals, activity and undo.

## Model connections

- Local: download a pinned Qwen model to run inference on the computer.
- ChatGPT: choose **Use my ChatGPT plan**, complete **Continue with ChatGPT** in
  the system browser, then select an available model and **Use ChatGPT plan**.
- OpenRouter: configure an API key and select a cloud model.

ChatGPT does not require a local model download or an OpenAI API key. Eligible
requests use the signed-in user's plan allowance, subject to account/workspace
availability and usage limits. The conversation and tool results are sent to
OpenAI. Use **ChatGPT account and usage** in the model picker to switch accounts,
sign out or open ChatGPT's usage controls. Credit spending is controlled in
ChatGPT settings; Ari never switches to an API key or another billing path when
a subscription request fails.

This integration implements OpenAI's documented [local/open-source flow](https://developers.openai.com/siwc/token-sharing-open-source).
Paid or remotely hosted offerings require reviewing the separate
[commercial integration process](https://developers.openai.com/siwc/request-client-id).
This sign-in permission does not import ChatGPT conversations or memory.

## Search, effort and current action scope

The ChatGPT route requests hosted `web_search`, subject to the account,
workspace and model accepting it. Ari does not offer its local search/page
fetchers on that route or retry a rejected request through Wikipedia. Search
progress and completed URL citations reach the native reply renderer. Turning
off Answer tools also removes hosted search. On other routes, `web_search` is
advertised only when a real search backend is configured; the separate
`wikipedia` tool is explicitly an encyclopedia lookup.

Effort choices come from the model list's advertised capabilities, are saved
per model and account, and are validated before use. No model-name lookup table
invents reasoning levels. Local models explicitly marked as supporting thinking
have a separate on/off choice. Replies record the selected setting.

Ari opens applications and changes personal settings through the existing
settings catalogue and media controls. App opening has a separate permission
and opens directly in the normal autonomy mode.

**Permissions and autonomy** is available in Ari's app menu and model picker,
and with `ari --settings permissions`. Choose **Ask every time**, **Ask for
important actions**, or **Let Ari act**. The normal mode approves an application
task once for its steps in that app; Let Ari act skips Ari's own confirmations.
Application/account controls, operating-system permissions and administrator
locks still apply. Advanced Allow/Ask/Block choices override the mode's defaults;
disabled capabilities remain blocked in every mode. A remembered permission
requires selecting **Remember this choice** when approving or declining. These
choices stay in Ari's own settings and can be reviewed or removed in Advanced.

Application actions use the shared Semantic Broker: request the needed access,
read the app's published objects/actions, and invoke their actual IDs. The
broker owns consent, redaction, risk confirmation and provider dispatch.

Routine Calendar, Notes, Contacts, Tasks and Weather work also has a direct,
structured application-model path. `query_application` exposes supported
operations, resources and revision-bound record targets. `change_application`
calls the same installed `prairie_apps` model APIs as those apps and reads back
the saved result. Calendar creation takes separate title, ISO start/end and IANA
time-zone fields. Notes uses its transactional store and conflict checks;
Contacts preserves fields outside the requested change; Tasks uses its owning
completion behavior; Weather uses its location database and saved-place model.
No editor or application window is needed. Calendar writes retain the app's
durable backups, and Notes removal uses Trash. Existing records need an expiring
query target; repeated verified operations in a turn do not repeat their write.
These tools use Ari's read/action tiers, autonomy modes and app-specific rules.
Query results are bounded previews; unrequested fields remain intact.

The bridge resolves the launcher’s application owner before accessing records.
When that owner is a Flatpak, Notes and Weather use that application's data
directory rather than creating a second native library. A signed OS role that
cannot resolve fails before accessing a fallback store. Query targets bind to
the resolved destination. Calendar deletion checks the exact stored series is
absent; an empty date-range query alone is insufficient. Receipts distinguish
saved-record verification from verification of the visible window.

This path requires the corresponding application model to be installed.
Recurring/all-day edits, invitations, permanent contact deletion, messaging,
calls and timers need their supported owner interfaces; this adapter does not
invent those operations or bypass owner authorization. Installed Calendar and
Weather windows may need refresh/reopening to display changes from another
client; a successful result means the saved provider record was verified.

If an app has no usable structured action, Ari can find its visible Linux accessibility
controls and use the app's own editable-text and button-action interfaces.
Controls must come from a fresh inspection of an application named by the
person. Protected fields are excluded; stale controls and concurrent text
changes are refused. Changes require Ari's normal action permission and preview.
Ari returns compact, paginated controls after each change to verify the outcome.
Layout and forecast prose cannot crowd action targets out of the result budget.
A unique application match includes its initial snapshot. Native GApplication
commands exported on `org.gtk.Actions` are also discovered and preferred over
menu navigation. These invoke the application's existing command handlers,
with advertised scalar parameter types, through its unique session-bus owner.
Targets bind the process start time, command availability and current
document/selection; a changed selection or expired target requires inspection
again. Native commands use the same action permissions and approval modes as
accessible controls. An acknowledgement is reported as a request, and the
resulting state must verify completion. No synthetic mouse/keyboard input or
direct writes to application databases are used. Apps must publish a suitable
broker action, native command or accessible control for the requested operation.

`discover_tools` lets the model find and load additional installed tools during
a turn, so the initial shortlist does not define the limit of Ari's capability.
Discovery does not execute actions or grant permissions. Named music requests
include application search/control tools, and resuming playback is explicitly
insufficient proof that a requested track and artist are playing.

Chat timestamps use the local 12-hour clock. Streaming updates retain unchanged
message widgets and the composer. The thread follows its latest content until
the person scrolls away; sending a request or navigating to the end resumes it.
Following uses the real scroll extent including the space below the final
message, without a recurring paint alignment that fights wheel gestures.

Process tools find exact same-user processes by name/PID, retain short-lived
identities and use pidfds with start-time revalidation for end/kill. The person
must identify the target; an ambiguous “this process” is not guessed. Kill must
be explicitly requested. Results distinguish a sent signal from verified exit.

Development tools create native SDK projects under `~/Projects/Ari`, list/read
and edit their files, and run checks/build commands. Replacing an existing file
requires the checksum from its last read. Builds run in Bubblewrap without
network, the session bus, host credentials, other home files or system writes.
Only the current project is writable. Dependencies must already be available;
build failures are reported, and scaffolding alone is not a completed app.

Software tools search Depot's catalogue and resolve supported Flatpak entries
to their actual signature-checked remote, commit and sandbox permissions. A
short-lived review is required to install; the owning backend rechecks the
source and dependencies, and Ari verifies the final installed ref. Other
catalogue backends remain available through Depot's own interface.

Ari can also inspect a local package and retain a short-lived review of its
publisher, permissions and warnings before installing through Valet. Valet
rechecks package bytes and publishes the actual installation record. Removal
uses the owning workflow, protects essential components and keeps personal
settings/data. Staged system changes explicitly report the required restart.
Optional applications supplied by Luma's system image use the existing
polkit-guarded removal/restore helper. That helper rechecks its own trusted
catalogue, essential-package floor, dependencies and staged deployment; Ari
cannot supply an arbitrary privileged command or remove a protected component.
There is no unrestricted root shell or automatic download of arbitrary code.

These adapters have separate permission switches and successful-action
receipts. Unknown tools remain denied; undo is offered only when a backend
supplies a real undo operation. The SDK package and Bubblewrap are required.

## Credentials and transport

The daemon needs libsecret with its GObject typelib, an unlocked default Secret
Service collection, PyJWT and cryptography. Ari stores its registrations and
tokens in its own keyring entry. It does not inspect browser cookies or Codex
credentials. Access/refresh tokens never cross the UI/CLI D-Bus interface or
enter the conversation database, ordinary configuration or activity log.

OAuth uses a loopback callback bound to `127.0.0.1`, fresh state/nonce/PKCE, and
verified OpenID Connect signatures, issuer and audience. Each account/workspace
registration keeps its own issued client ID. Refresh is serialized across
processes and saves the replacement token set together. Signing out attempts
remote revocation, clears local tokens and retains the registration for later
sign-in; an unconfirmed remote revocation is reported explicitly.

Responses requests use the public `/v1/responses` route with `store: false` and
`stream: true`. Ari retains the required context locally. Function calls are
released only after `response.completed` and still pass through Ari's existing
tool policy and approval checks. Incomplete/failed streams do not execute tools.
Current [preview restrictions](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
and model/account capabilities still apply; connecting a ChatGPT plan does not
by itself enable every hosted tool or voice API.

## CLI

```sh
ari chatgpt connect
ari chatgpt status
ari chatgpt use MODEL_ID
ari chatgpt account SAVED_ACCOUNT_ID
ari chatgpt connect SAVED_ACCOUNT_ID
ari chatgpt signout SAVED_ACCOUNT_ID
```

## Validation

From the repository root, with the Python dependencies installed:

```sh
PYTHONPATH=src/luma-ari python3 -m unittest discover -s tests/luma-ari -p 'test_*.py'
bash tests/luma-ari/run-chatgpt-native.sh
bash tests/luma-ari/run-actions-native.sh
```

The native test additionally needs the Luma platform libraries/typelibs, GTK,
libadwaita, Xvfb, a private-bus-capable gnome-keyring-daemon, Pillow for captures,
and the shared AppKit dependencies. `ARI_TEST_PYTHON` and
`ARI_TEST_KEYRING_DAEMON` can select test installations. The runner creates
isolated XDG directories and a new D-Bus session, exercises real Secret Service,
GTK and loopback OAuth, and substitutes only the remote provider. Test provider
replies do not establish live OpenAI account eligibility or inference success.
The action runner uses a disposable home and private broker with fixture consent,
then exercises actual SDK generation, isolated Python checks, reviewed portable
installation, removal and preservation of private app data. It does not install
or remove any of the developer's real applications.
