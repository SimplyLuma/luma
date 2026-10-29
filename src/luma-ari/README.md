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

Ari's shipped action tools currently cover opening applications and personal
settings, including the existing settings catalogue and media controls. Opening
an app has a dedicated permission and does not require approval for each launch.
Changing files, general in-app actions, building applications, and installing or
removing applications are not implemented Ari tools. Their future policy tiers
remain disabled; enabling a tier alone would not implement those operations.

The shared Semantic Broker already offers discoverable application surfaces and
`InvokeAction`; the SDK supplies application scaffolding and contract checks,
and the installer supplies reviewed install/removal workflows. Connecting those
services to Ari is further implementation work. Each capability must use its
actual backend and return a verified result rather than merely claiming success.

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
```

The native test additionally needs the Luma platform libraries/typelibs, GTK,
libadwaita, Xvfb, a private-bus-capable gnome-keyring-daemon, Pillow for captures,
and the shared AppKit dependencies. `ARI_TEST_PYTHON` and
`ARI_TEST_KEYRING_DAEMON` can select test installations. The runner creates
isolated XDG directories and a new D-Bus session, exercises real Secret Service,
GTK and loopback OAuth, and substitutes only the remote provider. Test provider
replies do not establish live OpenAI account eligibility or inference success.
