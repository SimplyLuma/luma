// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"context"
	"sync"
	"testing"
	"time"

	"go.mau.fi/mautrix-gmessages/pkg/libgm/gmproto"
)

// fakePhone counts full-size requests; a closed or nil gate answers at once.
type fakePhone struct {
	mu    sync.Mutex
	asked []string
	gate  chan struct{}
}

func (p *fakePhone) requestFullSize(_ context.Context, messageID, actionMessageID string) error {
	p.mu.Lock()
	p.asked = append(p.asked, messageID+"/"+actionMessageID)
	gate := p.gate
	p.mu.Unlock()
	if gate != nil {
		<-gate
	}
	return nil
}

func (p *fakePhone) count() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return len(p.asked)
}

// phoneHarness is a queue that treats participant "7" as this account.
func phoneHarness(t *testing.T) (*harness, *fakePhone) {
	h := newHarness(t)
	phone := &fakePhone{}
	h.q.phone = phone
	h.q.fromSelf = func(msg *gmproto.Message) bool { return msg.GetParticipantID() == "7" }
	return h, phone
}

func received(id string) *gmproto.Message {
	msg := photoWaitingForItsFile(id)
	msg.ParticipantID = "9"
	return msg
}

func TestIncomingTryAgainAsksThePhoneForTheFullFile(t *testing.T) {
	h, phone := phoneHarness(t)
	h.receive(received("r1"))
	h.next("pending")
	if got := h.q.askPhone("r1", "0"); got != "asked" {
		t.Fatalf("a press on a received picture: %s", got)
	}
	waitFor(t, func() bool { return phone.count() == 1 }, "the request")
	if phone.asked[0] != "r1/action-r1" {
		t.Fatalf("asked for %v", phone.asked)
	}
}

func TestOutgoingPicturesNeverAskThePhone(t *testing.T) {
	h, phone := phoneHarness(t)
	mine := photoWaitingForItsFile("s1")
	mine.ParticipantID = "7" // written on this account's phone, with an incoming status
	h.receive(mine)
	sent := received("s2")
	sent.MessageStatus.Status = gmproto.MessageStatusType_OUTGOING_COMPLETE
	h.receive(sent)
	for _, id := range []string{"s1", "s2"} {
		if got := h.q.askPhone(id, "0"); got != "not_askable" {
			t.Fatalf("%s: a press on an outgoing picture: %s", id, got)
		}
	}
	unknown := newHarness(t) // no fromSelf: not knowing counts as ours
	unknown.q.phone = phone
	unknown.receive(received("s3"))
	if got := unknown.q.askPhone("s3", "0"); got != "not_askable" {
		t.Fatalf("an unknown sender: %s", got)
	}
	time.Sleep(20 * time.Millisecond)
	if phone.count() != 0 {
		t.Fatalf("the phone was asked for an outgoing picture: %v", phone.asked)
	}
}

func TestAutomaticPathsNeverAskThePhone(t *testing.T) {
	h, phone := phoneHarness(t)
	h.receive(received("a1"))
	// Every re-read the queue makes on its own, to the plain failure at the end.
	waitFor(t, func() bool {
		h.q.mu.Lock()
		defer h.q.mu.Unlock()
		e := h.q.entries[entryKey("a1", "0")]
		return e != nil && e.class == "no_full_size"
	}, "the re-reads run out")
	h.q.kick()                          // a reconnect
	h.q.fetch("conv", "a1", "0", false) // Messages asking again on its own
	h.q.fetch("conv", "a1", "0", true)  // a tap without ask_phone (older Messages)
	h.receive(received("a1"))           // the message read again, as after a restart
	restarted, _ := phoneHarness(t)
	restarted.q.phone = phone
	restarted.receive(received("a1"))
	time.Sleep(50 * time.Millisecond)
	if phone.count() != 0 {
		t.Fatalf("the phone was asked without a press: %v", phone.asked)
	}
}

func TestOnePressIsOneRequestAndRepeatsWaitForIt(t *testing.T) {
	h, phone := phoneHarness(t)
	phone.gate = make(chan struct{})
	h.receive(received("p1"))
	results := []string{h.q.askPhone("p1", "0"), h.q.askPhone("p1", "0"), h.q.askPhone("p1", "0")}
	if results[0] != "asked" || results[1] != "in_flight" || results[2] != "in_flight" {
		t.Fatalf("presses while one is in flight: %v", results)
	}
	waitFor(t, func() bool { return phone.count() == 1 }, "the first request")
	close(phone.gate)
	waitFor(t, func() bool {
		h.q.mu.Lock()
		defer h.q.mu.Unlock()
		return !h.q.entries[entryKey("p1", "0")].askingPhone
	}, "the request to finish")
	if got := h.q.askPhone("p1", "0"); got != "asked" {
		t.Fatalf("a later press: %s", got)
	}
	waitFor(t, func() bool { return phone.count() == 2 }, "the second press's request")
	time.Sleep(20 * time.Millisecond)
	if phone.count() != 2 {
		t.Fatalf("two presses made %d requests", phone.count())
	}
}

func TestAPressBeforeTheMessageIsSeenIsHonouredOnceWhenItIsFound(t *testing.T) {
	h, phone := phoneHarness(t)
	if got := h.q.askPhone("l1", "0"); got != "later" {
		t.Fatalf("a press on an unseen message: %s", got)
	}
	h.receive(received("l1"))
	waitFor(t, func() bool { return phone.count() == 1 }, "the request")
	h.receive(received("l1"))
	time.Sleep(20 * time.Millisecond)
	if phone.count() != 1 {
		t.Fatalf("one press made %d requests", phone.count())
	}
}
