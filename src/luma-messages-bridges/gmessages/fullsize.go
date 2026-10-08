// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Asking the phone for a received picture's full-size file.
//
// Some received pictures reach paired clients with only a thumbnail: the phone
// publishes the full-size file when a client asks for it (GetFullSizeImage).
// Helpers 0.6 to 0.8 asked on their own, for every picture that did not
// download, including the person's own sent pictures; after a contact received
// a picture again that was removed (ece2f983). This brings back one narrow use,
// approved by Nick on 2026-09-22 ("received photos only"):
//
//   - only for a message someone else sent (not from this account, on any of
//     its devices, and with an incoming status);
//   - only for a part that has a thumbnail and no full-size media id yet;
//   - only when a person presses Try Again (media.fetch with "ask_phone"),
//     never from a retry, a re-read, a reconnect or a restart;
//   - one request per press, and none while one for that part is in flight.
//
// This file and libgmNetwork.requestFullSize are the only places that may
// name it; outbound_guard_test.go enforces that.
package main

import (
	"context"
	"errors"
	"time"

	"go.mau.fi/mautrix-gmessages/pkg/libgm/gmproto"
)

// A press that arrives before this run has seen its message is honoured when a
// lookup finds the message, if that happens within this long.
const phoneAskWindow = 2 * time.Minute

var errNotSignedIn = errors.New("not signed in")

// fullSizeRequester asks the phone to publish one received picture's file.
type fullSizeRequester interface {
	requestFullSize(ctx context.Context, messageID, actionMessageID string) error
}

// isIncoming is true only for a message someone else sent: not written by this
// account on any device, and with a status in the incoming range. Either test
// failing, or not knowing, counts as "ours".
func (q *mediaQueue) isIncoming(msg *gmproto.Message) bool {
	if q.fromSelf == nil || q.fromSelf(msg) {
		return false
	}
	status := msg.GetMessageStatus().GetStatus()
	return status >= 100 && status < 200
}

// askable says whether a person's press may ask the phone for this part.
func askable(e *mediaEntry) bool {
	p := e.part
	return p.incoming && p.index >= 0 && p.media != nil && p.actionID != "" && p.media.GetMediaID() == "" &&
		e.state != "done" && (e.class == "waiting_for_phone" || e.class == "no_full_size")
}

// askPhone is a person's Try Again on one part of a received message. It
// reports what it did: "asked", "in_flight", "not_askable", or "later" when the
// message has not been seen this run and is being looked up.
func (q *mediaQueue) askPhone(message, part string) string {
	q.mu.Lock()
	defer q.mu.Unlock()
	if part == "" || part == mmsPart {
		return "not_askable"
	}
	key := entryKey(message, part)
	e := q.entries[key]
	if e == nil {
		q.phoneAsks[key] = time.Now()
		return "later"
	}
	return q.askPhoneLocked(key, e)
}

// pressedBeforeLookupLocked honours a press made before its message was seen.
func (q *mediaQueue) pressedBeforeLookupLocked(key string, e *mediaEntry) {
	pressed, ok := q.phoneAsks[key]
	if !ok {
		return
	}
	delete(q.phoneAsks, key)
	if time.Since(pressed) <= phoneAskWindow {
		q.askPhoneLocked(key, e)
	}
}

func (q *mediaQueue) askPhoneLocked(key string, e *mediaEntry) string {
	if e.askingPhone {
		return "in_flight"
	}
	if q.phone == nil || q.closed || !askable(e) {
		return "not_askable"
	}
	e.askingPhone = true
	part := e.part
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		err := q.phone.requestFullSize(ctx, part.message, part.actionID)
		cancel()
		if err != nil {
			q.log.Warn().Str("message_id", part.message).Str("part", part.id()).Str("error", errorText(err)).
				Msg("asking the phone for a received picture's full-size file failed")
		} else {
			q.log.Info().Str("message_id", part.message).Str("part", part.id()).
				Msg("asked the phone for a received picture's full-size file (a person's Try Again)")
		}
		q.mu.Lock()
		if current := q.entries[key]; current != nil {
			current.askingPhone = false
			// Look again shortly for the media id the phone publishes.
			current.waits = 0
			if current.timer == nil && !current.queued && !current.running && current.state != "done" {
				q.waitForMediaIDLocked(key, current)
			}
		}
		q.mu.Unlock()
	}()
	return "asked"
}

// requestFullSize is the one call site of GetFullSizeImage.
func (n *libgmNetwork) requestFullSize(ctx context.Context, messageID, actionMessageID string) error {
	client := n.h.currentClient()
	if client == nil {
		return errNotSignedIn
	}
	_, err := client.GetFullSizeImage(ctx, messageID, actionMessageID)
	return err
}
