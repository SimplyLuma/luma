// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/rs/zerolog"

	"go.mau.fi/mautrix-gmessages/pkg/libgm/gmproto"
)

type fakeDelivery struct {
	mu        sync.Mutex
	messages  []string // TmpIDs
	reactions int
	uploads   int
	fail      error
}

func (f *fakeDelivery) SendMessage(ctx context.Context, req *gmproto.SendMessageRequest) (*gmproto.SendMessageResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.messages = append(f.messages, req.GetTmpID())
	return &gmproto.SendMessageResponse{}, f.fail
}

func (f *fakeDelivery) SendReaction(ctx context.Context, req *gmproto.SendReactionRequest) (*gmproto.SendReactionResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.reactions++
	return &gmproto.SendReactionResponse{Success: true}, f.fail
}

func (f *fakeDelivery) UploadMedia(data []byte, name, mime string) (*gmproto.MediaContent, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.uploads++
	return &gmproto.MediaContent{MediaID: "uploaded"}, nil
}

func accountDir(t *testing.T) string {
	dir := filepath.Join(t.TempDir(), "accounts", "gmessages-test")
	os.MkdirAll(dir, 0o700)
	return dir
}

const tokenA = "0123456789abcdef0123456789abcdef"
const tokenB = "fedcba9876543210fedcba9876543210"

func action(kind, token string) userAction {
	a, _ := parseUserAction(kind, map[string]any{"user_token": token})
	return a
}

func code(err error) string {
	var f *failure
	if errors.As(err, &f) {
		return f.Code
	}
	return ""
}

func TestADeliveryHappensOncePerRequestAcrossRestarts(t *testing.T) {
	dir := accountDir(t)
	client := &fakeDelivery{}
	o := newOutbound(dir, zerolog.Nop(), nil)
	req := &gmproto.SendMessageRequest{TmpID: "a"}
	if err := o.sendMessage(context.Background(), client, action("message.send", tokenA), req); err != nil {
		t.Fatal(err)
	}
	if err := o.sendMessage(context.Background(), client, action("message.send", tokenA), req); code(err) != "already_attempted" {
		t.Fatalf("a second delivery of the same request: %v", err)
	}
	// The helper restarts: the ledger on disk still knows.
	restarted := newOutbound(dir, zerolog.Nop(), nil)
	if err := restarted.sendMessage(context.Background(), client, action("message.send", tokenA), req); code(err) != "already_attempted" {
		t.Fatalf("after a restart: %v", err)
	}
	if len(client.messages) != 1 {
		t.Fatalf("delivered %d times, want 1", len(client.messages))
	}
}

func TestAnUnconfirmedDeliveryIsUncertainAndNotRepeated(t *testing.T) {
	dir := accountDir(t)
	client := &fakeDelivery{fail: errors.New("context deadline exceeded")}
	o := newOutbound(dir, zerolog.Nop(), nil)
	err := o.sendMessage(context.Background(), client, action("message.send", tokenA), &gmproto.SendMessageRequest{})
	if code(err) != "uncertain" {
		t.Fatalf("a failed call after it was made is uncertain, got %v", err)
	}
	client.fail = nil
	if err := o.sendMessage(context.Background(), client, action("message.send", tokenA), &gmproto.SendMessageRequest{}); code(err) != "already_attempted" {
		t.Fatalf("and never repeated for the same request: %v", err)
	}
	if len(client.messages) != 1 {
		t.Fatalf("%d calls", len(client.messages))
	}
}

func TestADeliveryWithoutAPersonsTokenIsBlockedAndTurnsSendingOff(t *testing.T) {
	dir := accountDir(t)
	var events []string
	o := newOutbound(dir, zerolog.Nop(), func(name string, _ any) { events = append(events, name) })
	client := &fakeDelivery{}
	for _, token := range []string{"", "not-a-token", "0123"} {
		if err := o.sendMessage(context.Background(), client, action("message.send", token), &gmproto.SendMessageRequest{}); code(err) != "blocked" {
			t.Fatalf("token %q: %v", token, err)
		}
	}
	if len(client.messages) != 0 {
		t.Fatal("a delivery without a token was made")
	}
	if off, _ := o.Disabled(); !off || len(events) == 0 || events[0] != "outbound_disabled" {
		t.Fatalf("the kill switch was not tripped: off=%v events=%v", off, events)
	}
	// With the switch on, nothing goes out even with a valid token.
	if err := o.sendMessage(context.Background(), client, action("message.send", tokenB), &gmproto.SendMessageRequest{}); code(err) != "outbound_disabled" {
		t.Fatalf("got %v", err)
	}
	if _, blocked := o.counts(); blocked != 3 || len(client.messages) != 0 {
		t.Fatalf("blocked=%d messages=%d", blocked, len(client.messages))
	}
}

func TestTheKillSwitchFileAndEnvironmentStopEverything(t *testing.T) {
	dir := accountDir(t)
	o := newOutbound(dir, zerolog.Nop(), nil)
	client := &fakeDelivery{}
	os.WriteFile(filepath.Join(filepath.Dir(dir), outboundKillName), []byte("vitals: anomaly"), 0o600)
	if _, err := o.sendReaction(context.Background(), client, action("message.react", tokenA), &gmproto.SendReactionRequest{}); code(err) != "outbound_disabled" {
		t.Fatalf("file switch: %v", err)
	}
	if _, err := o.upload(client, action("media.send", tokenA), []byte("x"), "a", "image/png"); code(err) != "outbound_disabled" {
		t.Fatalf("upload with the switch on: %v", err)
	}
	os.Remove(filepath.Join(filepath.Dir(dir), outboundKillName))
	t.Setenv("LUMA_MESSAGES_OUTBOUND", "off")
	if err := o.sendMessage(context.Background(), client, action("message.send", tokenA), &gmproto.SendMessageRequest{}); code(err) != "outbound_disabled" {
		t.Fatalf("environment switch: %v", err)
	}
	if client.reactions+client.uploads+len(client.messages) != 0 {
		t.Fatal("something was delivered")
	}
}

func TestMoreDeliveriesThanAPersonMakesTripsTheSwitch(t *testing.T) {
	dir := accountDir(t)
	o := newOutbound(dir, zerolog.Nop(), nil)
	now := time.Unix(1_800_000_000, 0)
	o.now = func() time.Time { return now }
	client := &fakeDelivery{}
	for i := 0; i < outboundBurst+5; i++ {
		token := []byte(tokenA)
		token[0], token[1] = "0123456789abcdef"[i%16], "0123456789abcdef"[(i/16)%16]
		o.sendMessage(context.Background(), client, action("message.send", string(token)), &gmproto.SendMessageRequest{})
	}
	if len(client.messages) != outboundBurst {
		t.Fatalf("%d delivered, want the burst limit %d", len(client.messages), outboundBurst)
	}
	if off, reason := o.Disabled(); !off || reason == "" {
		t.Fatal("the burst did not trip the switch")
	}
}

func TestTheHelperRefusesADeliveryCommandWithoutAToken(t *testing.T) {
	dir := accountDir(t)
	h := &helper{dataDir: dir, log: zerolog.Nop(), convs: map[string]*gmproto.Conversation{}, pending: map[string]string{},
		sims: map[string]*gmproto.SIMCard{}, self: map[string]bool{}}
	h.outbound = newOutbound(dir, zerolog.Nop(), nil)
	for _, cmd := range []string{"message.send", "media.send", "message.react"} {
		raw, _ := json.Marshal(map[string]any{"conversation": "c1", "client_id": tokenA, "text": "hi", "message": "1", "emoji": "👍"})
		if _, err := h.handle(cmd, raw); code(err) != "blocked" {
			t.Fatalf("%s without a token: %v", cmd, err)
		}
	}
	if _, blocked := h.outbound.counts(); blocked != 3 {
		t.Fatalf("blocked %d", blocked)
	}
}
