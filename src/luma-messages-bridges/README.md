# Luma Messages network helpers

One helper program per network, each speaking `luma-messages-bridge/1`.
The [Google Messages implementation](gmessages/main.go),
[WhatsApp implementation](whatsapp/main.go), and
[conformance checks](tests/conformance.py) define and exercise that protocol.
Messages (Apache-2.0) runs them as separate processes from `/usr/libexec/luma-messages/<network>`.

| Directory | Network | Built on | Licence of the helper |
|---|---|---|---|
| `gmessages/` | Google Messages | mautrix `pkg/libgm`, pinned by commit in `go.mod` | AGPL-3.0-or-later |
| `whatsapp/` | WhatsApp | whatsmeow, pinned by pseudo-version in `go.mod` | GPL-3.0-or-later |

Each helper directory carries its own licence notice; the corresponding source is this
directory plus the modules pinned in its `go.sum`.

Build (Fedora 44, Go 1.26; the builder downloads the pinned modules):

    GOTOOLCHAIN=local CGO_ENABLED=0 go build -trimpath -ldflags=-s -o gmessages ./gmessages

Check a built helper without any account:

    python3 tests/conformance.py ./gmessages gmessages
