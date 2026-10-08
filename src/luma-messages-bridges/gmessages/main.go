// SPDX-License-Identifier: AGPL-3.0-or-later
//
// luma-messages gmessages: the Google Messages helper for Luma Messages
// (ADR-023). It speaks luma-messages-bridge/1 on stdin/stdout
// (docs/research/messages-bridge-protocol.md) and uses mautrix-gmessages'
// libgm, which is AGPL-3.0-or-later, for the Google Messages for web protocol.
//
// The session (Google cookies and pairing keys) is never written to disk: it is
// sent to Messages as a "session" event for the keyring and loaded back with
// session.load. Only downloaded media and its thumbnails live in the data
// directory (media.go).
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"mime"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/rs/zerolog"
	"go.mau.fi/util/exhttp"

	"go.mau.fi/mautrix-gmessages/pkg/libgm"
	"go.mau.fi/mautrix-gmessages/pkg/libgm/events"
	"go.mau.fi/mautrix-gmessages/pkg/libgm/gmproto"
)

const (
	protocol      = "luma-messages-bridge/1"
	helperVersion = "0.2.0"
	maxMedia      = 25 * 1024 * 1024
	signInURL     = "https://accounts.google.com/AccountChooser?continue=https://messages.google.com/web/config"
)

var safeName = regexp.MustCompile(`[^A-Za-z0-9._-]+`)
var clientID = regexp.MustCompile(`^[0-9a-f]{32}$`)
var networkID = regexp.MustCompile(`^[A-Za-z0-9._:@+=/-]{1,256}$`)

type request struct {
	ID   string          `json:"id"`
	Cmd  string          `json:"cmd"`
	Args json.RawMessage `json:"args"`
}

type failure struct {
	Code      string `json:"code"`
	Message   string `json:"message"`
	Retryable bool   `json:"retryable,omitempty"`
}

func (f *failure) Error() string { return f.Code + ": " + f.Message }

type helper struct {
	dataDir string
	log     zerolog.Logger

	outMu sync.Mutex
	out   *bufio.Writer

	mu          sync.Mutex
	auth        *libgm.AuthData
	client      *libgm.Client
	state       string
	sims        map[string]*gmproto.SIMCard
	convs       map[string]*gmproto.Conversation
	pending     map[string]string // libgm TmpID -> Messages client_id
	self        map[string]bool   // participant ids that are this account (IsMe participants, SIMs)
	pairCancel  context.CancelFunc
	events      chan any
	stopSession chan struct{}
	media       *mediaQueue
	outbound    *outbound
}

func main() {
	dataDir := ""
	for i, arg := range os.Args {
		if arg == "--data-dir" && i+1 < len(os.Args) {
			dataDir = os.Args[i+1]
		}
	}
	if dataDir == "" {
		fmt.Fprintln(os.Stderr, "usage: gmessages --data-dir DIR")
		os.Exit(2)
	}
	level := zerolog.WarnLevel
	if os.Getenv("LUMA_MESSAGES_HELPER_DEBUG") != "" {
		level = zerolog.DebugLevel
	}
	h := &helper{
		dataDir: dataDir,
		log:     zerolog.New(os.Stderr).Level(level).With().Timestamp().Logger(),
		out:     bufio.NewWriter(os.Stdout),
		state:   "needs_login",
		sims:    map[string]*gmproto.SIMCard{},
		convs:   map[string]*gmproto.Conversation{},
		pending: map[string]string{},
		self:    map[string]bool{},
		events:  make(chan any, 4096),
	}
	h.outbound = newOutbound(dataDir, h.log, h.event)
	network := newLibgmNetwork(h, exhttp.SensibleClientSettings.Compile())
	h.media = newMediaQueue(filepath.Join(dataDir, "media"), h.log, network, h.event, h.emitMessage)
	h.media.fromSelf = func(msg *gmproto.Message) bool {
		h.mu.Lock()
		defer h.mu.Unlock()
		return h.fromSelfLocked(msg)
	}
	h.media.phone = network
	go h.dispatchEvents()
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 64*1024), 16*1024*1024)
	for scanner.Scan() {
		var req request
		if err := json.Unmarshal(scanner.Bytes(), &req); err != nil || req.ID == "" {
			continue
		}
		if req.Cmd == "shutdown" {
			h.shutdown()
			h.reply(req.ID, map[string]any{}, nil)
			return
		}
		// Slow network commands never block the next command.
		go func(req request) {
			result, err := h.handle(req.Cmd, req.Args)
			h.reply(req.ID, result, err)
		}(req)
	}
	h.shutdown()
}

func (h *helper) write(value any) {
	data, err := json.Marshal(value)
	if err != nil {
		return
	}
	h.outMu.Lock()
	defer h.outMu.Unlock()
	h.out.Write(data)
	h.out.WriteByte('\n')
	h.out.Flush()
}

func (h *helper) reply(id string, result any, err error) {
	if err != nil {
		var f *failure
		if !errors.As(err, &f) {
			f = &failure{Code: "error", Message: err.Error(), Retryable: true}
		}
		h.write(map[string]any{"id": id, "ok": false, "error": f})
		return
	}
	h.write(map[string]any{"id": id, "ok": true, "result": result})
}

func (h *helper) event(name string, data any) {
	h.write(map[string]any{"event": name, "data": data})
}

func (h *helper) setState(state, detail string) {
	h.mu.Lock()
	changed := h.state != state
	h.state = state
	h.mu.Unlock()
	if changed {
		h.event("status", map[string]any{"state": state, "detail": detail})
	}
}

func (h *helper) handle(cmd string, raw json.RawMessage) (any, error) {
	args := map[string]any{}
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &args); err != nil {
			return nil, &failure{Code: "invalid", Message: "arguments are not an object"}
		}
	}
	str := func(key string) string { v, _ := args[key].(string); return v }
	num := func(key string, fallback int) int {
		if v, ok := args[key].(float64); ok && v > 0 {
			return int(v)
		}
		return fallback
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	switch cmd {
	case "hello":
		return map[string]any{"protocol": protocol, "network": "gmessages", "helper_version": helperVersion,
			"capabilities": map[string]any{"login": []string{"cookies"}, "media": true, "max_media_bytes": maxMedia,
				"reactions": true, "reaction_emoji": reactionEmoji, "replies": true, "typing": false,
				"read_receipts": true, "groups": true, "create_conversations": true, "unofficial": true,
				"media_fetch": true, "user_tokens": true}}, nil
	case "session.load":
		auth := &libgm.AuthData{}
		if err := json.Unmarshal([]byte(str("session")), auth); err != nil {
			return nil, &failure{Code: "invalid", Message: "the saved session could not be read"}
		}
		h.useAuth(auth)
		return map[string]any{}, nil
	case "connect":
		go h.connect()
		return map[string]any{}, nil
	case "login.start":
		if method := str("method"); method != "cookies" {
			return nil, &failure{Code: "unsupported", Message: "Google Messages signs in with a Google account"}
		}
		h.event("login.browser", map[string]any{
			"url":              signInURL,
			"cookies":          []string{"SID", "HSID", "SSID", "APISID", "SAPISID", "OSID"},
			"optional_cookies": []string{"__Secure-1PSIDTS"},
			"domains":          []string{"google.com", "messages.google.com"},
		})
		return map[string]any{}, nil
	case "login.submit":
		if str("field") != "cookies" {
			return nil, &failure{Code: "invalid", Message: "unexpected field"}
		}
		cookies := map[string]string{}
		if err := json.Unmarshal([]byte(str("value")), &cookies); err != nil {
			return nil, &failure{Code: "invalid", Message: "cookies are not a JSON object"}
		}
		for _, name := range []string{"SID", "HSID", "SSID", "APISID", "SAPISID", "OSID"} {
			if cookies[name] == "" {
				h.event("login.needs", map[string]any{"field": "cookies", "hint": "The " + name + " cookie is missing. Sign in again, or paste every cookie for messages.google.com."})
				return map[string]any{}, nil
			}
		}
		go h.pair(cookies)
		return map[string]any{}, nil
	case "login.cancel":
		h.mu.Lock()
		if h.pairCancel != nil {
			h.pairCancel()
		}
		h.mu.Unlock()
		return map[string]any{}, nil
	case "status":
		h.mu.Lock()
		defer h.mu.Unlock()
		return map[string]any{"state": h.state}, nil
	}

	switch cmd {
	case "conversations.list", "messages.list", "message.send", "media.send", "message.read", "message.react", "conversation.create", "logout", "media.fetch":
	default:
		return nil, &failure{Code: "unsupported", Message: cmd}
	}
	// A delivery is a person's request or nothing: without its token it is
	// refused before anything else happens, and sending is turned off.
	var action userAction
	if cmd == "message.send" || cmd == "media.send" || cmd == "message.react" {
		var ok bool
		if action, ok = parseUserAction(cmd, args); !ok {
			return nil, h.outbound.Block(cmd, "no user token")
		}
	}
	client := h.currentClient()
	if client == nil {
		return nil, &failure{Code: "not_connected", Message: "Google Messages isn't signed in", Retryable: true}
	}
	switch cmd {
	case "conversations.list":
		resp, err := client.ListConversations(ctx, &gmproto.ListConversationsRequest{
			Count: int64(num("limit", 50)), Folder: gmproto.ListConversationsRequest_INBOX})
		if err != nil {
			return nil, retryable(err)
		}
		list := []any{}
		for _, conv := range resp.GetConversations() {
			h.remember(conv)
			list = append(list, h.conversation(conv))
		}
		return map[string]any{"conversations": list}, nil
	case "messages.list":
		conv := str("conversation")
		resp, err := client.FetchMessages(ctx, conv, int64(num("limit", 30)), nil)
		if err != nil {
			return nil, retryable(err)
		}
		list := []any{}
		starts := []func(){}
		for _, msg := range resp.GetMessages() {
			if mapped, start := h.message(msg); mapped != nil {
				list = append(list, mapped)
				starts = append(starts, start)
			}
		}
		// Downloads report on messages Messages is about to store; it applies
		// events only after it has handled this answer.
		for _, start := range starts {
			start()
		}
		return map[string]any{"messages": list, "more": resp.GetCursor() != nil}, nil
	case "media.fetch":
		conv, message := str("conversation"), str("message")
		if !networkID.MatchString(conv) || !networkID.MatchString(message) || len(str("part")) > 16 {
			return nil, &failure{Code: "invalid", Message: "media.fetch needs a conversation and a message"}
		}
		// "reread" asks for the conversation to be read again. The old
		// "force", which meant asking the phone to send the file again, is
		// deliberately not read: nothing here can reach the phone.
		reread, _ := args["reread"].(bool)
		// "ask_phone" is a person's Try Again on a received picture that has
		// only a thumbnail: the one case the phone is asked for its file
		// (fullsize.go). Messages sends it only from a tap, never on its own.
		askPhone, _ := args["ask_phone"].(bool)
		result := map[string]any{}
		if askPhone && reread {
			result["phone"] = h.media.askPhone(message, str("part"))
		}
		h.media.fetch(conv, message, str("part"), reread)
		return result, nil
	case "message.send", "media.send":
		return h.send(ctx, client, action, cmd, args)
	case "message.read":
		if err := client.MarkRead(ctx, str("conversation"), str("message")); err != nil {
			return nil, retryable(err)
		}
		return map[string]any{}, nil
	case "message.react":
		return h.react(ctx, client, action, args)
	case "conversation.create":
		numbers := []*gmproto.ContactNumber{}
		if list, ok := args["participants"].([]any); ok {
			for _, item := range list {
				if phone, ok := item.(string); ok && phone != "" {
					numbers = append(numbers, &gmproto.ContactNumber{MysteriousInt: 2, Number: phone, Number2: phone})
				}
			}
		}
		if len(numbers) == 0 {
			return nil, &failure{Code: "invalid", Message: "no phone numbers"}
		}
		req := &gmproto.GetOrCreateConversationRequest{Numbers: numbers}
		resp, err := client.GetOrCreateConversation(ctx, req)
		if err == nil && resp.GetStatus() == gmproto.GetOrCreateConversationResponse_CREATE_RCS {
			name, create := "", true
			req.RCSGroupName, req.CreateRCSGroup = &name, &create
			resp, err = client.GetOrCreateConversation(ctx, req)
		}
		if err != nil {
			return nil, retryable(err)
		}
		if resp.GetConversation().GetConversationID() == "" {
			return nil, &failure{Code: "failed", Message: "Google Messages didn't create the conversation"}
		}
		h.remember(resp.GetConversation())
		return map[string]any{"conversation": h.conversation(resp.GetConversation())}, nil
	case "logout":
		if remote, _ := args["remote"].(bool); remote {
			if err := client.Unpair(ctx); err != nil {
				h.log.Warn().Err(err).Msg("unpair failed")
			}
		}
		client.Disconnect()
		h.mu.Lock()
		h.client, h.auth = nil, nil
		h.mu.Unlock()
		h.setState("needs_login", "")
		return map[string]any{}, nil
	}
	return nil, &failure{Code: "unsupported", Message: cmd}
}

func retryable(err error) error {
	if errors.Is(err, libgm.ErrPhoneNotResponding) {
		return &failure{Code: "phone_offline", Message: "Your phone didn't respond", Retryable: true}
	}
	return &failure{Code: "not_connected", Message: err.Error(), Retryable: true}
}

func (h *helper) currentClient() *libgm.Client {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.client
}

func (h *helper) useAuth(auth *libgm.AuthData) *libgm.Client {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.client != nil {
		h.client.Disconnect()
	}
	h.auth = auth
	h.client = libgm.NewClient(auth, nil, h.log, exhttp.SensibleClientSettings)
	// libgm requires a handler that never blocks; events are processed in order elsewhere.
	h.client.SetEventHandler(func(evt any) {
		select {
		case h.events <- evt:
		default:
			h.log.Warn().Msg("event queue full; dropping an event")
		}
	})
	return h.client
}

func (h *helper) saveSession() {
	h.mu.Lock()
	auth := h.auth
	h.mu.Unlock()
	if auth == nil {
		return
	}
	data, err := json.Marshal(auth)
	if err == nil {
		h.event("session", map[string]any{"session": string(data)})
	}
}

func (h *helper) connect() {
	client := h.currentClient()
	if client == nil {
		h.setState("needs_login", "")
		return
	}
	h.setState("connecting", "")
	configCtx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	err := client.FetchConfig(configCtx)
	cancel()
	if err == nil && client.Config.GetDeviceInfo().GetEmail() == "" {
		h.setState("needs_login", "Google Messages signed this device out.")
		return
	}
	// libgm runs its long poll on this context for as long as the client is connected,
	// so it must not be one that is cancelled when Connect returns.
	if err := client.Connect(context.Background()); err != nil {
		if errors.Is(err, events.ErrRequestedEntityNotFound) || errors.Is(err, events.ErrInvalidCredentials) {
			h.setState("needs_login", "")
		} else {
			h.log.Warn().Err(err).Msg("connect failed")
			h.setState("error", "")
		}
		return
	}
	h.setState("connected", "")
	h.media.kick()
	h.startSessionSaver()
}

// Cookies and tokens change silently while connected; Messages keeps the latest copy.
func (h *helper) startSessionSaver() {
	h.mu.Lock()
	if h.stopSession != nil {
		h.mu.Unlock()
		return
	}
	stop := make(chan struct{})
	h.stopSession = stop
	h.mu.Unlock()
	go func() {
		ticker := time.NewTicker(30 * time.Minute)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				h.saveSession()
			case <-stop:
				return
			}
		}
	}()
}

func (h *helper) pair(cookies map[string]string) {
	auth := libgm.NewAuthData()
	auth.SetCookies(cookies)
	client := h.useAuth(auth)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	h.mu.Lock()
	h.pairCancel = cancel
	h.mu.Unlock()
	defer cancel()
	err := client.DoGaiaPairing(ctx, func(emoji string) {
		h.event("login.code", map[string]any{"code": emoji, "kind": "emoji"})
	})
	if err != nil {
		code, message := "failed", "Google Messages couldn't link this device. Try again."
		switch {
		case errors.Is(err, libgm.ErrNoDevicesFound):
			code, message = "no_phone", "Google Messages didn't find your phone. On your phone, open Google Messages, tap your profile picture, then Device pairing, and try again."
		case errors.Is(err, libgm.ErrIncorrectEmoji):
			code, message = "wrong_emoji", "The emoji you tapped didn't match. Try again."
		case errors.Is(err, libgm.ErrPairingCancelled):
			code, message = "cancelled", "Linking was cancelled on your phone."
		case errors.Is(err, libgm.ErrPairingTimeout), errors.Is(err, libgm.ErrPairingInitTimeout), errors.Is(err, context.DeadlineExceeded):
			code, message = "timeout", "Your phone didn't answer in time. Make sure it's on and online, then try again."
		case errors.Is(err, context.Canceled):
			return
		case errors.Is(err, events.ErrInvalidCredentials):
			code, message = "bad_cookies", "Google didn't accept that sign-in. Sign in again."
		}
		h.log.Warn().Err(err).Msg("pairing failed")
		h.event("login.error", map[string]any{"code": code, "message": message})
		return
	}
	h.saveSession()
	name := ""
	if err := client.FetchConfig(ctx); err == nil {
		name = client.Config.GetDeviceInfo().GetEmail()
	}
	h.event("login.done", map[string]any{"account": map[string]any{"name": name, "handle": ""}})
}

func (h *helper) shutdown() {
	h.mu.Lock()
	client := h.client
	if h.stopSession != nil {
		close(h.stopSession)
		h.stopSession = nil
	}
	h.mu.Unlock()
	h.media.close()
	if client != nil {
		h.saveSession()
		client.Disconnect()
	}
}

func (h *helper) dispatchEvents() {
	for raw := range h.events {
		switch evt := raw.(type) {
		case *events.ClientReady:
			for _, conv := range evt.Conversations {
				h.remember(conv)
				h.event("conversation", h.conversation(conv))
			}
		case *gmproto.Conversation:
			h.remember(evt)
			h.event("conversation", h.conversation(evt))
		case *libgm.WrappedMessage:
			h.emitMessage(evt.Message)
		case *gmproto.Settings:
			h.mu.Lock()
			for _, sim := range evt.GetSIMCards() {
				h.sims[sim.GetSIMParticipant().GetID()] = sim
				h.self[sim.GetSIMParticipant().GetID()] = true
			}
			h.mu.Unlock()
		case *events.AuthTokenRefreshed, *events.PairSuccessful:
			h.saveSession()
		case *events.ListenFatalError:
			if errors.Is(evt.Error, events.ErrInvalidCredentials) || evt.Error.Error() == "http 401 while polling" {
				h.setState("needs_login", "")
			} else {
				h.setState("error", "")
			}
		case *events.ListenTemporaryError:
			h.setState("error", "")
		case *events.ListenRecovered, *events.PhoneRespondingAgain:
			h.setState("connected", "")
			h.media.kick()
		case *events.PhoneNotResponding:
			h.setState("phone_offline", "")
		case *events.PingFailed:
			if errors.Is(evt.Error, events.ErrRequestedEntityNotFound) {
				h.setState("needs_login", "")
			}
		case *gmproto.RevokePairData, *events.GaiaLoggedOut:
			h.setState("needs_login", "")
		case *events.BrowserActive:
			h.setState("elsewhere", "")
		}
	}
}

func (h *helper) remember(conv *gmproto.Conversation) {
	if conv == nil || conv.GetConversationID() == "" {
		return
	}
	h.mu.Lock()
	h.convs[conv.GetConversationID()] = conv
	for _, p := range conv.GetParticipants() {
		if p.GetIsMe() && p.GetID().GetParticipantID() != "" {
			h.self[p.GetID().GetParticipantID()] = true
		}
	}
	h.mu.Unlock()
}

// fromSelf says whether this account wrote a message, on any of its devices.
// The status alone is not enough: a message sent from the phone is reported
// to paired clients with statuses that are not always in the outgoing range,
// and those were stored as incoming and unread. mautrix-gmessages decides by
// the sender the same way: participant "1", or one the account knows is itself.
func (h *helper) fromSelfLocked(msg *gmproto.Message) bool {
	status := msg.GetMessageStatus().GetStatus()
	if status > 0 && status < 100 {
		return true
	}
	if msg.GetSenderParticipant().GetIsMe() {
		return true
	}
	id := msg.GetParticipantID()
	return id != "" && (id == "1" || h.self[id])
}

func micros(value int64) int64 {
	if value > 1e14 {
		return value / 1e6
	}
	if value > 1e11 {
		return value / 1e3
	}
	return value
}

func (h *helper) conversation(conv *gmproto.Conversation) map[string]any {
	participants := []any{}
	for _, p := range conv.GetParticipants() {
		if p.GetIsMe() {
			continue
		}
		participants = append(participants, map[string]any{
			"id": p.GetID().GetParticipantID(), "name": firstNonEmpty(p.GetFullName(), p.GetFormattedNumber(), p.GetID().GetNumber()),
			"phone": p.GetID().GetNumber()})
	}
	kind := "direct"
	if conv.GetIsGroupChat() {
		kind = "group"
	}
	unread := 0
	if conv.GetUnread() {
		unread = 1
	}
	status := conv.GetStatus()
	return map[string]any{
		"id": conv.GetConversationID(), "name": conv.GetName(), "kind": kind, "participants": participants,
		"updated": micros(conv.GetLastMessageTimestamp()), "unread": unread,
		"preview":  conv.GetLatestMessage().GetDisplayContent(),
		"archived": status == gmproto.ConversationStatus_ARCHIVED || status == gmproto.ConversationStatus_KEEP_ARCHIVED,
	}
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}

func failedStatus(status gmproto.MessageStatusType) bool {
	switch status {
	case gmproto.MessageStatusType_OUTGOING_FAILED_GENERIC, gmproto.MessageStatusType_OUTGOING_FAILED_EMERGENCY_NUMBER,
		gmproto.MessageStatusType_OUTGOING_CANCELED, gmproto.MessageStatusType_OUTGOING_FAILED_TOO_LARGE,
		gmproto.MessageStatusType_OUTGOING_FAILED_RECIPIENT_LOST_RCS, gmproto.MessageStatusType_OUTGOING_FAILED_NO_RETRY_NO_FALLBACK,
		gmproto.MessageStatusType_OUTGOING_FAILED_RECIPIENT_DID_NOT_DECRYPT, gmproto.MessageStatusType_OUTGOING_FAILED_RECIPIENT_LOST_ENCRYPTION,
		gmproto.MessageStatusType_OUTGOING_FAILED_RECIPIENT_DID_NOT_DECRYPT_NO_MORE_RETRY,
		gmproto.MessageStatusType_OUTGOING_FAILED_RECIPIENT_NEGATIVE_DELIVERY, gmproto.MessageStatusType_OUTGOING_RESTRICTED,
		gmproto.MessageStatusType_OUTGOING_FAILED_TO_ENCRYPT:
		return true
	}
	return false
}

// emitMessage sends a message event, then starts the downloads its media needs.
func (h *helper) emitMessage(msg *gmproto.Message) {
	if mapped, start := h.message(msg); mapped != nil {
		h.event("message", mapped)
		start()
	}
}

func (h *helper) message(msg *gmproto.Message) (map[string]any, func()) {
	if msg == nil || msg.GetMessageID() == "" {
		return nil, nil
	}
	status := msg.GetMessageStatus().GetStatus()
	if status >= 200 && status < 300 {
		return nil, nil // conversation notices ("RCS chat started"), not messages
	}
	deleted := status == gmproto.MessageStatusType_MESSAGE_DELETED || status == gmproto.MessageStatusType_OUTGOING_DELETED ||
		status == gmproto.MessageStatusType_INCOMING_DELETED
	h.mu.Lock()
	outgoing := h.fromSelfLocked(msg)
	h.mu.Unlock()
	state := "received"
	switch {
	case outgoing && failedStatus(status):
		state = "failed"
	case outgoing && status == gmproto.MessageStatusType_OUTGOING_DISPLAYED:
		state = "read"
	case outgoing && status == gmproto.MessageStatusType_OUTGOING_DELIVERED:
		state = "delivered"
	case outgoing && status == gmproto.MessageStatusType_OUTGOING_COMPLETE:
		state = "sent"
	case outgoing && status >= 100:
		state = "sent" // written by this account elsewhere; it was sent
	case outgoing && (status == gmproto.MessageStatusType_STATUS_UNKNOWN || status == gmproto.MessageStatusType_OUTGOING_NOT_DELIVERED_YET):
		state = "sent"
	case outgoing:
		state = "sending"
	case status == gmproto.MessageStatusType_INCOMING_DISPLAYED:
		state = "read"
	}
	texts := []string{}
	for _, info := range msg.GetMessageInfo() {
		if content := info.GetMessageContent(); content != nil && content.GetContent() != "" {
			texts = append(texts, content.GetContent())
		}
	}
	// Media parts are reported with their state; their files follow as media events.
	attachments, start := h.media.observe(msg)
	h.mu.Lock()
	client_id := h.pending[msg.GetTmpID()]
	if client_id != "" && state != "sending" {
		delete(h.pending, msg.GetTmpID())
	}
	if client_id == "" && outgoing {
		// A message this helper sent carries its client id as its TmpID, so it is
		// recognised after a restart too, when the pending map is gone; Messages
		// then confirms its own row instead of storing a second copy.
		if candidate := strings.ReplaceAll(msg.GetTmpID(), "-", ""); clientID.MatchString(candidate) {
			client_id = candidate
		}
	}
	conv := h.convs[msg.GetConversationID()]
	h.mu.Unlock()
	var sender any
	if !outgoing {
		name := msg.GetSenderParticipant().GetFullName()
		if name == "" && conv != nil {
			for _, p := range conv.GetParticipants() {
				if p.GetID().GetParticipantID() == msg.GetParticipantID() {
					name = firstNonEmpty(p.GetFullName(), p.GetFormattedNumber())
				}
			}
		}
		sender = map[string]any{"id": msg.GetParticipantID(), "name": name}
	}
	mapped := map[string]any{
		"id": msg.GetMessageID(), "conversation": msg.GetConversationID(), "sender": sender, "outgoing": outgoing,
		"text": strings.Join(texts, "\n"), "time": micros(msg.GetTimestamp()), "state": state, "attachments": attachments,
		"deleted": deleted,
	}
	if client_id != "" {
		mapped["client_id"] = client_id
	}
	if reply := msg.GetReplyMessage().GetMessageID(); reply != "" {
		mapped["reply_to"] = reply
	}
	// Every message carries its complete set of reactions, so a message seen
	// again (a conversation reopened, a reaction added later) replaces them.
	mapped["reactions"] = h.reactions(conv, msg)
	return mapped, start
}

// reactions maps Google Messages reactions to the bridge protocol: one entry per
// person and emoji, with the person's own reactions sent by "self".
func (h *helper) reactions(conv *gmproto.Conversation, msg *gmproto.Message) []any {
	me := map[string]bool{}
	for _, p := range conv.GetParticipants() {
		if p.GetIsMe() {
			me[p.GetID().GetParticipantID()] = true
		}
	}
	list := []any{}
	for _, reaction := range msg.GetReactions() {
		var emoji string
		switch reaction.GetData().GetType() {
		case gmproto.EmojiType_EMOTIFY:
			continue // animated stickers have no text form
		case gmproto.EmojiType_CUSTOM:
			emoji = reaction.GetData().GetUnicode()
		default:
			emoji = reaction.GetData().GetType().Unicode()
		}
		if emoji == "" {
			continue
		}
		for _, participant := range reaction.GetParticipantIDs() {
			sender := participant
			if me[participant] {
				sender = "self"
			}
			list = append(list, map[string]any{"sender": sender, "emoji": emoji})
		}
	}
	return list
}

// The reactions Google Messages offers under a message, in its order.
var reactionEmoji = []string{"😍", "😂", "😮", "😥", "😠", "👍", "👎"}

// react adds, changes or removes this account's reaction to a message. Google
// Messages keeps one reaction per person, so a different emoji replaces the
// one given before ("previous"), and no emoji removes it.
func (h *helper) react(ctx context.Context, client *libgm.Client, action userAction, args map[string]any) (any, error) {
	messageID, _ := args["message"].(string)
	convID, _ := args["conversation"].(string)
	emoji, _ := args["emoji"].(string)
	previous, _ := args["previous"].(string)
	if messageID == "" || len(emoji) > 64 || len(previous) > 64 {
		return nil, &failure{Code: "invalid", Message: "a reaction needs a message and one emoji"}
	}
	req := &gmproto.SendReactionRequest{MessageID: messageID}
	switch {
	case emoji == "" && previous == "":
		return map[string]any{}, nil
	case emoji == "":
		req.ReactionData, req.Action = gmproto.MakeReactionData(previous), gmproto.SendReactionRequest_REMOVE
	case previous != "" && previous != emoji:
		req.ReactionData, req.Action = gmproto.MakeReactionData(emoji), gmproto.SendReactionRequest_SWITCH
	default:
		req.ReactionData, req.Action = gmproto.MakeReactionData(emoji), gmproto.SendReactionRequest_ADD
	}
	h.mu.Lock()
	if conv := h.convs[convID]; conv != nil {
		req.SIMPayload = h.sims[conv.GetDefaultOutgoingID()].GetSIMData().GetSIMPayload()
	}
	h.mu.Unlock()
	resp, err := h.outbound.sendReaction(ctx, client, action, req)
	if err != nil {
		return nil, err
	}
	if !resp.GetSuccess() {
		return nil, &failure{Code: "failed", Message: "Google Messages didn't accept the reaction"}
	}
	return map[string]any{}, nil
}

// Preferred file extensions; mime.ExtensionsByType lists alternatives alphabetically.
var extensions = map[string]string{"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
	"image/heic": ".heic", "image/heif": ".heif", "video/mp4": ".mp4", "video/3gpp": ".3gp", "audio/mpeg": ".mp3",
	"audio/mp4": ".m4a", "audio/aac": ".aac", "audio/amr": ".amr", "text/vcard": ".vcf", "text/x-vcard": ".vcf"}

func extensionFor(mimeType string) string {
	base := strings.ToLower(strings.TrimSpace(strings.SplitN(mimeType, ";", 2)[0]))
	if ext, ok := extensions[base]; ok {
		return ext
	}
	if exts, _ := mime.ExtensionsByType(base); len(exts) > 0 {
		return exts[0]
	}
	return ""
}

func tmpID(id string) string {
	if clientID.MatchString(id) {
		return fmt.Sprintf("%s-%s-%s-%s-%s", id[:8], id[8:12], id[12:16], id[16:20], id[20:])
	}
	return uuid.NewString()
}

// send delivers a person's message. Everything that can fail before delivery
// answers not_attempted, which Messages may try again later; once the call is
// made, a failure is uncertain and is only ever retried by the person.
func (h *helper) send(ctx context.Context, client *libgm.Client, action userAction, cmd string, args map[string]any) (any, error) {
	convID, _ := args["conversation"].(string)
	id, _ := args["client_id"].(string)
	if err := h.outbound.check(action); err != nil {
		return nil, err
	}
	h.mu.Lock()
	conv := h.convs[convID]
	h.mu.Unlock()
	if conv == nil {
		fetched, err := client.GetConversation(ctx, convID)
		if err != nil {
			return nil, &failure{Code: "not_attempted", Message: errorText(err), Retryable: true}
		}
		h.remember(fetched)
		conv = fetched
	}
	tmp := tmpID(id)
	info := []*gmproto.MessageInfo{}
	if cmd == "media.send" {
		path, _ := args["path"].(string)
		mime, _ := args["mime"].(string)
		name, _ := args["name"].(string)
		data, err := os.ReadFile(path)
		if err != nil || len(data) > maxMedia {
			return nil, &failure{Code: "invalid", Message: "the attachment can't be read or is too large"}
		}
		media, err := h.outbound.upload(client, action, data, name, mime)
		if err != nil {
			var refused *failure
			if errors.As(err, &refused) {
				return nil, refused
			}
			return nil, &failure{Code: "not_attempted", Message: errorText(err), Retryable: true}
		}
		info = append(info, &gmproto.MessageInfo{Data: &gmproto.MessageInfo_MediaContent{MediaContent: media}})
		if caption, _ := args["caption"].(string); caption != "" {
			info = append(info, &gmproto.MessageInfo{Data: &gmproto.MessageInfo_MessageContent{MessageContent: &gmproto.MessageContent{Content: caption}}})
		}
	} else {
		text, _ := args["text"].(string)
		info = append(info, &gmproto.MessageInfo{Data: &gmproto.MessageInfo_MessageContent{MessageContent: &gmproto.MessageContent{Content: text}}})
	}
	h.mu.Lock()
	sim := h.sims[conv.GetDefaultOutgoingID()]
	h.pending[tmp] = id
	h.mu.Unlock()
	req := &gmproto.SendMessageRequest{
		ConversationID: convID,
		MessagePayload: &gmproto.MessagePayload{
			TmpID: tmp, ConversationID: convID, ParticipantID: conv.GetDefaultOutgoingID(), TmpID2: tmp, MessageInfo: info,
		},
		SIMPayload: sim.GetSIMData().GetSIMPayload(),
		TmpID:      tmp,
	}
	if reply, _ := args["reply_to"].(string); reply != "" {
		req.Reply = &gmproto.ReplyPayload{MessageID: reply}
	}
	if err := h.outbound.sendMessage(ctx, client, action, req); err != nil {
		var refused *failure
		if errors.As(err, &refused) && refused.Code != "uncertain" {
			h.mu.Lock()
			delete(h.pending, tmp)
			h.mu.Unlock()
		}
		return nil, err
	}
	// Delivery arrives as a message event carrying this client_id.
	return map[string]any{}, nil
}
