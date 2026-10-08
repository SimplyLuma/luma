// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"context"
	"math/rand/v2"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/rs/zerolog"

	"go.mau.fi/mautrix-gmessages/pkg/libgm/gmproto"
)

// Nick's real sequence (2026-09-16): attachments Google no longer held, reported
// as empty or gone, then reconnects, Messages asking again and taps. The helper
// must end with every one of them no longer available and nothing delivered.
func TestReplayOfTheRealGoneSequenceDeliversNothing(t *testing.T) {
	h := newHarness(t)
	dir := accountDir(t)
	out := newOutbound(dir, zerolog.Nop(), nil)
	ids := []string{"25443", "25508", "25431", "25592", "3438", "3439", "797", "26867", "26866", "27282", "27283", "241992"}
	for _, id := range ids {
		h.net.always["m"+id] = "gone"
		h.net.always["t"+id] = "gone"
		msg := photo(id, "m"+id)
		msg.MessageInfo[0].GetMediaContent().ThumbnailMediaID = "t" + id
		msg.MessageInfo[0].GetMediaContent().ThumbnailDecryptionKey = testKey
		if id == "27282" || id == "27283" || id == "241992" {
			msg.MessageStatus = &gmproto.MessageStatus{Status: gmproto.MessageStatusType_OUTGOING_COMPLETE}
		}
		h.receive(msg)
	}
	for range ids {
		h.next("failed")
	}
	for round := 0; round < 3; round++ {
		h.q.kick()
		for _, id := range ids {
			h.q.fetch("conv", id, "", round == 2)
		}
	}
	time.Sleep(100 * time.Millisecond)
	if attempts, blocked := out.counts(); attempts != 0 || blocked != 0 {
		t.Fatalf("media recovery reached delivery: attempts=%d blocked=%d", attempts, blocked)
	}
}

// A long simulated session: reconnects, restarts, timeouts, media failures,
// backfill lookups and person's sends, in random order. Delivery calls happen
// only for the person's sends, exactly once each, however often a send is
// repeated by a flaky connection, a restart or a retry loop.
func TestALongFlakySessionDeliversExactlyThePersonsSends(t *testing.T) {
	seed := uint64(time.Now().UnixNano())
	random := rand.New(rand.NewPCG(seed, 42))
	h := newHarness(t)
	stop := make(chan struct{})
	defer close(stop)
	go func() { // the media events of a long session, read as Messages reads them
		for {
			select {
			case <-h.events:
			case <-stop:
				return
			}
		}
	}()
	dir := accountDir(t)
	client := &fakeDelivery{}
	out := newOutbound(dir, zerolog.Nop(), nil)
	now := time.Unix(1_800_000_000, 0)
	out.now = func() time.Time { return now }
	sent := map[string]bool{}
	classes := []string{"network", "server", "truncated", "gone", "empty", "unauthorized", "expired", "not_connected"}
	for step := 0; step < 4000; step++ {
		now = now.Add(time.Duration(1+random.IntN(20)) * time.Second)
		switch random.IntN(9) {
		case 0: // a message with media arrives, sometimes failing
			id := string(rune('a'+random.IntN(26))) + string(rune('a'+random.IntN(26)))
			if random.IntN(2) == 0 {
				h.net.mu.Lock()
				h.net.failures["m"+id] = []string{classes[random.IntN(len(classes))]}
				h.net.mu.Unlock()
			}
			h.receive(photo(id, "m"+id))
		case 1: // reconnect
			h.q.kick()
		case 2: // Messages asks again, or a person taps
			h.q.fetch("conv", string(rune('a'+random.IntN(26)))+string(rune('a'+random.IntN(26))), "", random.IntN(2) == 0)
		case 3: // the helper restarts: a new outbound reads the same ledger
			out = newOutbound(dir, zerolog.Nop(), nil)
			out.now = func() time.Time { return now }
		case 4, 5: // a person sends; the connection may time out after the call
			token := randomToken(random)
			if random.IntN(4) == 0 {
				client.fail = context.DeadlineExceeded
			}
			if err := out.sendMessage(context.Background(), client, action("message.send", token), &gmproto.SendMessageRequest{TmpID: token}); err == nil || code(err) == "uncertain" {
				sent[token] = true
			}
			client.fail = nil
		case 6: // a flaky layer repeats an earlier send, possibly after a restart
			for token := range sent {
				if random.IntN(3) == 0 {
					out.sendMessage(context.Background(), client, action("message.send", token), &gmproto.SendMessageRequest{TmpID: token})
				}
				break
			}
		case 7: // backfill reads
			h.net.listMessages(context.Background(), "conv", nil)
		case 8: // something tries to deliver with no person behind it
			if random.IntN(50) == 0 {
				other := newOutbound(accountDir(t), zerolog.Nop(), nil)
				if err := other.sendMessage(context.Background(), client, action("message.send", ""), &gmproto.SendMessageRequest{}); code(err) != "blocked" {
					t.Fatalf("seed %d: an untokened delivery was not blocked: %v", seed, err)
				}
			}
		}
	}
	client.mu.Lock()
	defer client.mu.Unlock()
	counts := map[string]int{}
	for _, tmp := range client.messages {
		counts[tmp]++
	}
	for token, n := range counts {
		if n != 1 || !sent[token] {
			t.Fatalf("seed %d: request %s delivered %d times (person's send: %v)", seed, token[:8], n, sent[token])
		}
	}
	if len(counts) != len(sent) {
		t.Fatalf("seed %d: %d deliveries for %d sends", seed, len(counts), len(sent))
	}
	if _, err := os.Stat(filepath.Join(filepath.Dir(dir), outboundKillName)); err == nil {
		t.Fatalf("seed %d: the person's own pace tripped the switch", seed)
	}
}

func randomToken(random *rand.Rand) string {
	const hex = "0123456789abcdef"
	token := make([]byte, 32)
	for i := range token {
		token[i] = hex[random.IntN(16)]
	}
	return string(token)
}
