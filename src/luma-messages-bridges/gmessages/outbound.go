// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Outbound: the only code in this helper that can make the phone deliver
// anything to another person.
//
// A message sent twice, a picture re-delivered, a reaction nobody chose: these
// reach real people and cannot be taken back. So every Google Messages call
// that can cause delivery goes through this file, and only here:
//
//   - SendMessage, SendReaction and UploadMedia are reachable only through
//     outbound.sendMessage, sendReaction and upload, which need a userAction. A userAction is made only
//     from a message.send, media.send or message.react command that carries
//     a user token: a 32-hex value Messages creates when a person presses Send
//     or chooses a reaction, and records before it asks.
//   - Each token is written to a ledger on disk (fsync'd) before the call is
//     made, and a token already in the ledger is refused: a request can be
//     delivered at most once, whatever restarts, reconnects or retries happen.
//   - GetFullSizeImage, ResendMessage and every other call that asks the phone
//     to act are not used anywhere. outbound_guard_test.go fails the build if
//     any file other than this one names a delivery call, or any file names a
//     forbidden one.
//   - A kill switch stops all of it: the file "outbound-disabled" beside the
//     account directories (Vitals or a person can create it), or
//     LUMA_MESSAGES_OUTBOUND=off. It is tripped automatically, with an error in
//     the journal and an event to Messages, by a request without a valid token
//     or by more deliveries in a minute than a person makes.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"sync"
	"time"

	"github.com/rs/zerolog"

	"go.mau.fi/mautrix-gmessages/pkg/libgm/gmproto"
)

// Deliveries a person could plausibly make in a minute; more trips the switch.
const (
	outboundBurst       = 12
	outboundBurstWindow = time.Minute
	outboundLedgerName  = "outbound-ledger.jsonl"
	outboundKillName    = "outbound-disabled"
)

var userTokenPattern = regexp.MustCompile(`^[0-9a-f]{32}$`)

// deliveryClient is the part of libgm that can cause delivery. Only outbound
// holds one; everything else in the helper is given the client as a reader.
type deliveryClient interface {
	SendMessage(ctx context.Context, req *gmproto.SendMessageRequest) (*gmproto.SendMessageResponse, error)
	SendReaction(ctx context.Context, req *gmproto.SendReactionRequest) (*gmproto.SendReactionResponse, error)
	UploadMedia(data []byte, fileName, mime string) (*gmproto.MediaContent, error)
}

// userAction is a person's request to deliver something, identified by the
// token Messages created for it. The ledger key adds the part for a message
// with several attachments, each its own delivery.
type userAction struct {
	kind  string
	token string
	part  int
}

func (a userAction) key() string { return a.kind + ":" + a.token + ":" + strconv.Itoa(a.part) }

// parseUserAction reads the token from a delivery command's arguments.
func parseUserAction(kind string, args map[string]any) (userAction, bool) {
	token, _ := args["user_token"].(string)
	part := 0
	if value, ok := args["part_index"].(float64); ok && value >= 0 && value < 64 {
		part = int(value)
	}
	return userAction{kind: kind, token: token, part: part}, userTokenPattern.MatchString(token)
}

var (
	errOutboundDisabled = &failure{Code: "outbound_disabled", Message: "Sending is turned off for Messages"}
	errAlreadyDelivered = &failure{Code: "already_attempted", Message: "This was already sent once and is not sent again"}
	errNoUserToken      = &failure{Code: "blocked", Message: "A delivery without a person's request was refused"}
)

type outbound struct {
	mu       sync.Mutex
	ledger   string
	kill     string
	used     map[string]bool
	recent   []time.Time
	log      zerolog.Logger
	emit     func(string, any)
	now      func() time.Time
	attempts int
	blocked  int
}

func newOutbound(dataDir string, log zerolog.Logger, emit func(string, any)) *outbound {
	o := &outbound{
		ledger: filepath.Join(dataDir, outboundLedgerName),
		kill:   filepath.Join(filepath.Dir(dataDir), outboundKillName),
		used:   map[string]bool{},
		log:    log,
		emit:   emit,
		now:    time.Now,
	}
	if file, err := os.Open(o.ledger); err == nil {
		scanner := bufio.NewScanner(file)
		for scanner.Scan() {
			var entry struct {
				Key string `json:"key"`
			}
			if json.Unmarshal(scanner.Bytes(), &entry) == nil && entry.Key != "" {
				o.used[entry.Key] = true
			}
		}
		file.Close()
	}
	return o
}

// Disabled says whether the kill switch is on, and why.
func (o *outbound) Disabled() (bool, string) {
	if os.Getenv("LUMA_MESSAGES_OUTBOUND") == "off" {
		return true, "LUMA_MESSAGES_OUTBOUND=off"
	}
	if data, err := os.ReadFile(o.kill); err == nil {
		return true, string(data)
	}
	return false, ""
}

// Trip turns sending off for every account until a person turns it back on.
func (o *outbound) Trip(reason string) {
	o.log.Error().Str("reason", reason).Msg("outbound delivery disabled: kill switch tripped")
	if err := os.WriteFile(o.kill, []byte(reason+"\n"), 0o600); err != nil {
		o.log.Error().Err(err).Msg("could not write the outbound kill switch")
	}
	if o.emit != nil {
		o.emit("outbound_disabled", map[string]any{"reason": reason})
	}
}

// Block records a delivery attempt that did not come from a person, refuses
// it and turns sending off.
func (o *outbound) Block(kind, why string) error {
	o.mu.Lock()
	o.blocked++
	o.mu.Unlock()
	o.log.Error().Str("kind", kind).Str("why", why).Msg("outbound delivery blocked: not a person's request")
	o.Trip("blocked " + kind + ": " + why)
	return errNoUserToken
}

// claim writes the action to the ledger before anything is delivered. It
// fails if sending is off, the token was used, or deliveries arrive faster
// than a person makes them.
func (o *outbound) claim(action userAction) error {
	if !userTokenPattern.MatchString(action.token) {
		return o.Block(action.kind, "no user token")
	}
	if off, _ := o.Disabled(); off {
		return errOutboundDisabled
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	key := action.key()
	if o.used[key] {
		o.log.Warn().Str("kind", action.kind).Str("token", action.token[:8]).Int("part", action.part).
			Msg("outbound delivery refused: this request was already attempted")
		return errAlreadyDelivered
	}
	now := o.now()
	cutoff := now.Add(-outboundBurstWindow)
	kept := o.recent[:0]
	for _, at := range o.recent {
		if at.After(cutoff) {
			kept = append(kept, at)
		}
	}
	o.recent = kept
	if len(o.recent) >= outboundBurst {
		o.Trip("more than " + strconv.Itoa(outboundBurst) + " deliveries in a minute")
		return errOutboundDisabled
	}
	line, _ := json.Marshal(map[string]any{"key": key, "at": now.Unix()})
	file, err := os.OpenFile(o.ledger, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return &failure{Code: "not_attempted", Message: "the delivery ledger can't be written", Retryable: true}
	}
	_, writeErr := file.Write(append(line, '\n'))
	syncErr := file.Sync()
	closeErr := file.Close()
	if err := errors.Join(writeErr, syncErr, closeErr); err != nil {
		return &failure{Code: "not_attempted", Message: "the delivery ledger can't be written", Retryable: true}
	}
	o.used[key] = true
	o.recent = append(o.recent, now)
	o.attempts++
	return nil
}

// uncertain is the answer when a delivery call was made and did not clearly
// succeed: it may have been delivered, so it is never retried automatically.
func uncertain(err error) error {
	return &failure{Code: "uncertain", Message: "Google Messages didn't confirm this: " + errorText(err)}
}

// check refuses early a request that could not be delivered anyway, so nothing
// is uploaded for it.
func (o *outbound) check(action userAction) error {
	if !userTokenPattern.MatchString(action.token) {
		return o.Block(action.kind, "no user token")
	}
	if off, _ := o.Disabled(); off {
		return errOutboundDisabled
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.used[action.key()] {
		return errAlreadyDelivered
	}
	return nil
}

// upload sends an attachment's bytes ahead of its message. Uploading alone
// delivers nothing, but it is only done for a person's claimed request.
func (o *outbound) upload(client deliveryClient, action userAction, data []byte, name, mime string) (*gmproto.MediaContent, error) {
	if err := o.check(action); err != nil {
		return nil, err
	}
	return client.UploadMedia(data, name, mime)
}

// sendMessage delivers one message for one claimed person's request.
func (o *outbound) sendMessage(ctx context.Context, client deliveryClient, action userAction, req *gmproto.SendMessageRequest) error {
	if err := o.claim(action); err != nil {
		return err
	}
	if _, err := client.SendMessage(ctx, req); err != nil {
		return uncertain(err)
	}
	return nil
}

// sendReaction delivers one reaction for one claimed person's request.
func (o *outbound) sendReaction(ctx context.Context, client deliveryClient, action userAction, req *gmproto.SendReactionRequest) (*gmproto.SendReactionResponse, error) {
	if err := o.claim(action); err != nil {
		return nil, err
	}
	resp, err := client.SendReaction(ctx, req)
	if err != nil {
		return nil, uncertain(err)
	}
	return resp, nil
}

// counts is for tests: calls issued and requests blocked this run.
func (o *outbound) counts() (int, int) {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.attempts, o.blocked
}
