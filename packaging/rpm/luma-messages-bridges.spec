# SPDX-License-Identifier: Apache-2.0
# Network account helpers for Luma Messages (ADR-023). Each helper is its own program
# under its own licence; Messages runs them as separate processes.
%global debug_package %{nil}

Name:           luma-messages-bridges
Version:        0.1.0
Release:        0.11.experiment%{?dist}
Summary:        Google Messages and WhatsApp accounts for Luma Messages
# gmessages: AGPL-3.0-or-later (mautrix libgm); whatsapp: GPL-3.0-or-later (whatsmeow
# MPL-2.0 with go.mau.fi/libsignal GPL-3.0). Vendored modules add the rest.
License:        AGPL-3.0-or-later AND GPL-3.0-or-later AND MPL-2.0 AND Apache-2.0 AND MIT AND BSD-3-Clause AND BSD-2-Clause AND ISC
URL:            https://project-luma.local/messages
ExclusiveArch:  x86_64 aarch64
# The source archive carries each helper's pinned Go modules (go mod vendor), so the
# build needs no network. scripts/packages/build-luma-messages-bridges-source.sh makes it.
Source0:        luma-messages-bridges.tar.gz

BuildRequires:  golang >= 1.26
BuildRequires:  python3
# Every delivery needs a person's token, which Messages sends from connect.39.
Requires:       prairie-core-apps >= 0.1.0-1.luma.74.connect.39~preview.20260916.1
# Google Messages sign-in opens a temporary Firefox profile; pasting cookies works without it.
Recommends:     firefox

%description
Helpers that let Luma Messages show and send chats from Google Messages (paired
with the Google Messages app on an Android phone) and WhatsApp (linked as a
companion device). They run on this device only and speak luma-messages-bridge/1.
Neither network offers this officially; Messages says so before an account is added.

%prep
%autosetup -n luma-messages-bridges

%build
export GOTOOLCHAIN=local CGO_ENABLED=0 GOFLAGS="-mod=vendor -trimpath"
mkdir -p bin
for helper in gmessages whatsapp; do
  (cd "$helper" && go build -ldflags "-s -w" -o "../bin/$helper" .)
done

%install
install -d %{buildroot}%{_libexecdir}/luma-messages
install -m 0755 bin/gmessages bin/whatsapp %{buildroot}%{_libexecdir}/luma-messages/
# Licence texts of every vendored module, by module path.
for helper in gmessages whatsapp; do
  install -D -m 0644 "$helper/LICENSE" "licenses/$helper/LICENSE"
  (cd "$helper/vendor" && find . -type f \( -iname 'LICENSE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' \) -print0 |
    while IFS= read -r -d '' file; do install -D -m 0644 "$file" "../../licenses/$helper/vendor/$file"; done)
done

%check
export GOTOOLCHAIN=local CGO_ENABLED=0 GOFLAGS="-mod=vendor -trimpath"
(cd gmessages && go vet . && go test -count=1 .)
python3 tests/conformance.py bin/gmessages gmessages
python3 tests/conformance.py bin/whatsapp whatsapp code

%files
%license licenses
%doc README.md
%dir %{_libexecdir}/luma-messages
%{_libexecdir}/luma-messages/gmessages
%{_libexecdir}/luma-messages/whatsapp

%changelog
* Tue Sep 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.11.experiment
- Google Messages: a person's Try Again on a received picture that has only a thumbnail asks the phone for the full-size file (GetFullSizeImage), as approved by the owner on 2026-09-22 ("received photos only"). Only for messages someone else sent (not this account on any device, incoming status), only on media.fetch with ask_phone from a tap, one request per press, none while one is in flight; never on a retry, re-read, reconnect or restart. Each request is logged with the message id and part only. The source guard now allows the call in exactly that chain
* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.10.experiment
- Google Messages: an attachment whose full-size media id has not arrived is re-read on a backoff until it does, and then reported as a failure the person can retry, instead of being parked with no timer and no re-read. That park is what left incoming pictures waiting for a message update that never came
- Google Messages: re-reading a message for fresh media ids is allowed again for a part that is waiting on the phone. It is listMessages, a read of the conversation, and was excluded only because the 0.9 delivery guard treated every phone-side case alike
- Google Messages: reconnecting re-reads the parts that are still waiting, rather than letting them sit out their backoff
- Google Messages: media.fetch reads "reread" for a person's own tap. The old "force", documented up to 0.8 as asking the phone to send the file again, is no longer read at all
- Tests: an attachment with no media id downloads once its id appears on a re-read, one whose id never appears ends in a failure rather than an endless wait, and a reconnect brings in a waiting attachment
* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.9.experiment
- Google Messages: nothing asks the phone to act on a message any more. 0.6 to 0.8 called GetFullSizeImage automatically for attachments that did not download, including your own sent pictures; that call is gone, and a picture Google no longer holds is simply no longer available
- Google Messages: outbound.go is the only code that can deliver (SendMessage, SendReaction, UploadMedia). A delivery needs the person's token Messages sends with it, is written to an fsync'd ledger before the call and is refused if that token was ever used, so nothing is delivered twice across restarts, reconnects or retries. A failure after the call is "uncertain" and never retried by the helper
- Google Messages: a kill switch (the outbound-disabled file beside the accounts, or LUMA_MESSAGES_OUTBOUND=off) stops all delivery; a delivery without a token or more than 12 in a minute trips it with an error in the journal and an event to Messages
- Google Messages: a message this helper sent is recognised by its TmpID after a restart, so Messages confirms it instead of storing a copy
- Tests: a source guard fails the build if any delivery call appears outside outbound.go or GetFullSizeImage/ResendMessage appear at all; ledger, kill switch and burst tests; the real "gone" sequence replayed; a 4000-step randomized session with reconnects, restarts, timeouts and media failures delivering exactly the person's sends once each

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.8.experiment
- Google Messages: an attachment Google no longer holds (200 OK with only the two-byte encrypted header, what its media service returns for files older than about six months) is reported as gone instead of empty, the phone is asked once to send it again, it is tried once more after 45 seconds and then left for a tap; its thumbnail is tried once per run, not after every attempt

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.7.experiment
- Google Messages: a message this account wrote on any device is outgoing, decided by its sender (participant 1, a SIM, or a conversation participant marked as you) and not only by status; messages sent from the phone showed as unread
- Google Messages: a message's inline preview bytes or thumbnail are its preview, never its file; the full picture downloads when the phone's update carries its media id, and the phone is asked once for it. Pictures were saved as the kilobyte preview and never replaced
- Google Messages: a downloaded file records the media id it came from; a kilobyte file saved by 0.6 for a much larger picture is fetched again

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.6.experiment
- Google Messages: attachments download on a queue of three with retries, backoff and jitter instead of inline, one at a time, once; messages no longer wait for their media
- Google Messages: check the HTTP status and count the bytes of every download, so a failure says what it was (expired, empty, cut off, not decryptable, network) instead of "unexpected EOF"
- Google Messages: after a failure a retry can't fix, re-read the message for fresh media ids, show its thumbnail, and ask the phone once to upload the full file again
- Google Messages: report each attachment's state (pending, downloading, done, failed) and answer media.fetch; never replace a picture with text
- Google Messages: an incoming MMS the phone is still fetching is reported as waiting for the phone, and a picture still uploading as pending
- Google Messages: helper unit tests run in the package check

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.5.experiment
- Google Messages: react to a message, change the reaction or take it back, and list the reactions Google Messages offers

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.4.experiment
- Google Messages: each message carries its reactions, with your own marked as yours

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.3.experiment
- Google Messages: name downloaded media with the extension for its type, log why a download failed, and say when a photo or video is too large to download

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.2.experiment
- Google Messages: keep the long poll running after connecting; it stopped as soon as connect returned, so no conversations or messages ever arrived

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.1.experiment
- Google Messages helper on mautrix libgm e6cc2997 and WhatsApp helper on whatsmeow b25a56d63729 (ADR-023)
