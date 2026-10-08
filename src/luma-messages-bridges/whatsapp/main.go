// SPDX-License-Identifier: GPL-3.0-or-later
//
// luma-messages whatsapp: the WhatsApp helper for Luma Messages (ADR-023). It
// speaks luma-messages-bridge/1 on stdin/stdout and links Luma as a WhatsApp
// companion device with whatsmeow (MPL-2.0, which depends on the GPL-3.0
// go.mau.fi/libsignal, so this program is GPL-3.0-or-later).
//
// whatsmeow keeps the device keys in whatsapp.db and this helper keeps a small
// chat index in index.db, both in the account's 0700 data directory.
package main

import (
	"bufio"
	"context"
	"database/sql"
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

	"github.com/rs/zerolog"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
	_ "modernc.org/sqlite"
)

const (
	protocol      = "luma-messages-bridge/1"
	helperVersion = "0.1.0"
	maxMedia      = 25 * 1024 * 1024
	historyPerChat = 50
)

var safeName = regexp.MustCompile(`[^A-Za-z0-9._-]+`)
var digits = regexp.MustCompile(`\D`)

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

	mu        sync.Mutex
	container *sqlstore.Container
	client    *whatsmeow.Client
	index     *sql.DB
	state     string
	loginDone bool
	cancel    context.CancelFunc
	pending   map[string]string // WhatsApp message id -> Messages client_id
}

func main() {
	dataDir := ""
	for i, arg := range os.Args {
		if arg == "--data-dir" && i+1 < len(os.Args) {
			dataDir = os.Args[i+1]
		}
	}
	if dataDir == "" {
		fmt.Fprintln(os.Stderr, "usage: whatsapp --data-dir DIR")
		os.Exit(2)
	}
	level := zerolog.WarnLevel
	if os.Getenv("LUMA_MESSAGES_HELPER_DEBUG") != "" {
		level = zerolog.DebugLevel
	}
	h := &helper{dataDir: dataDir, log: zerolog.New(os.Stderr).Level(level).With().Timestamp().Logger(),
		out: bufio.NewWriter(os.Stdout), state: "needs_login", pending: map[string]string{}}
	if err := h.open(); err != nil {
		h.log.Error().Err(err).Msg("opening the stores failed")
	}
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
		go func(req request) {
			result, err := h.handle(req.Cmd, req.Args)
			h.reply(req.ID, result, err)
		}(req)
	}
	h.shutdown()
}

func (h *helper) open() error {
	address := func(name string) string {
		return "file:" + filepath.Join(h.dataDir, name) + "?_pragma=foreign_keys(1)&_pragma=busy_timeout(10000)&_pragma=journal_mode(WAL)"
	}
	db, err := sql.Open("sqlite", address("whatsapp.db"))
	if err != nil {
		return err
	}
	container := sqlstore.NewWithDB(db, "sqlite3", waLog.Zerolog(h.log.With().Str("component", "whatsmeow").Logger()))
	if err := container.Upgrade(context.Background()); err != nil {
		return err
	}
	index, err := sql.Open("sqlite", address("index.db"))
	if err != nil {
		return err
	}
	if _, err := index.Exec(`CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
		updated INTEGER NOT NULL, preview TEXT NOT NULL DEFAULT '', unread INTEGER NOT NULL DEFAULT 0);
		CREATE TABLE IF NOT EXISTS messages(chat TEXT NOT NULL, id TEXT NOT NULL, sender TEXT NOT NULL, sender_name TEXT NOT NULL,
		from_me INTEGER NOT NULL, text TEXT NOT NULL, time INTEGER NOT NULL, state TEXT NOT NULL, attachments TEXT NOT NULL,
		reply_to TEXT NOT NULL DEFAULT '', PRIMARY KEY(chat, id));
		CREATE INDEX IF NOT EXISTS messages_time ON messages(chat, time);`); err != nil {
		return err
	}
	for _, name := range []string{"whatsapp.db", "index.db"} {
		os.Chmod(filepath.Join(h.dataDir, name), 0o600)
	}
	h.container, h.index = container, index
	return nil
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

func (h *helper) event(name string, data any) { h.write(map[string]any{"event": name, "data": data}) }

func (h *helper) setState(state, detail string) {
	h.mu.Lock()
	changed := h.state != state
	h.state = state
	h.mu.Unlock()
	if changed {
		h.event("status", map[string]any{"state": state, "detail": detail})
	}
}

// ensureClient returns the client for the one device in the store, creating it if needed.
func (h *helper) ensureClient() (*whatsmeow.Client, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.client != nil {
		return h.client, nil
	}
	if h.container == nil {
		return nil, &failure{Code: "error", Message: "the WhatsApp store couldn't be opened"}
	}
	device, err := h.container.GetFirstDevice(context.Background())
	if err != nil {
		return nil, err
	}
	client := whatsmeow.NewClient(device, waLog.Zerolog(h.log.With().Str("component", "client").Logger()))
	client.AddEventHandler(h.onEvent)
	h.client = client
	return client, nil
}

func (h *helper) linked() bool {
	client, err := h.ensureClient()
	return err == nil && client.Store.ID != nil
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
		return map[string]any{"protocol": protocol, "network": "whatsapp", "helper_version": helperVersion,
			"capabilities": map[string]any{"login": []string{"qr", "code"}, "media": true, "max_media_bytes": maxMedia,
				"reactions": false, "replies": true, "typing": false, "read_receipts": true, "groups": true,
				"create_conversations": true, "unofficial": true}}, nil
	case "session.load":
		return map[string]any{}, nil // keys live in whatsapp.db
	case "status":
		h.mu.Lock()
		defer h.mu.Unlock()
		return map[string]any{"state": h.state}, nil
	case "connect":
		client, err := h.ensureClient()
		if err != nil {
			return nil, err
		}
		if client.Store.ID == nil {
			h.setState("needs_login", "")
			return map[string]any{}, nil
		}
		h.setState("connecting", "")
		go func() {
			if err := client.Connect(); err != nil {
				h.log.Warn().Err(err).Msg("connect failed")
				h.setState("error", "")
			}
		}()
		return map[string]any{}, nil
	case "login.start":
		return h.loginStart(str("method"), str("phone"))
	case "login.submit":
		if str("field") != "phone" {
			return nil, &failure{Code: "invalid", Message: "unexpected field"}
		}
		go h.pairWithCode(str("value"))
		return map[string]any{}, nil
	case "login.cancel":
		h.mu.Lock()
		if h.cancel != nil {
			h.cancel()
		}
		client := h.client
		h.mu.Unlock()
		if client != nil && client.Store.ID == nil {
			client.Disconnect()
		}
		return map[string]any{}, nil
	}
	switch cmd {
	case "conversations.list", "messages.list", "message.send", "media.send", "message.read", "conversation.create", "logout":
	default:
		return nil, &failure{Code: "unsupported", Message: cmd}
	}
	if !h.linked() {
		return nil, &failure{Code: "not_connected", Message: "WhatsApp isn't linked", Retryable: true}
	}
	client, _ := h.ensureClient()
	switch cmd {
	case "conversations.list":
		return h.listChats(num("limit", 50))
	case "messages.list":
		return h.listMessages(str("conversation"), num("limit", 30))
	case "message.send", "media.send":
		return h.send(ctx, client, cmd, args)
	case "message.read":
		chat, err := types.ParseJID(str("conversation"))
		if err != nil {
			return nil, &failure{Code: "invalid", Message: "conversation"}
		}
		var sender string
		h.index.QueryRow(`SELECT sender FROM messages WHERE chat=? AND id=?`, chat.String(), str("message")).Scan(&sender)
		senderJID, _ := types.ParseJID(sender)
		if err := client.MarkRead(ctx, []types.MessageID{str("message")}, time.Now(), chat, senderJID); err != nil {
			return nil, &failure{Code: "not_connected", Message: err.Error(), Retryable: true}
		}
		return map[string]any{}, nil
	case "conversation.create":
		list, _ := args["participants"].([]any)
		if len(list) != 1 {
			return nil, &failure{Code: "unsupported", Message: "new WhatsApp groups are created on your phone"}
		}
		phone, _ := list[0].(string)
		number := digits.ReplaceAllString(phone, "")
		if len(number) < 7 {
			return nil, &failure{Code: "invalid", Message: "that isn't a phone number"}
		}
		found, err := client.IsOnWhatsApp(ctx, []string{"+" + number})
		if err != nil {
			return nil, &failure{Code: "not_connected", Message: err.Error(), Retryable: true}
		}
		if len(found) == 0 || !found[0].IsIn {
			return nil, &failure{Code: "not_on_network", Message: phone + " isn't on WhatsApp"}
		}
		jid := found[0].JID
		name := h.contactName(ctx, client, jid)
		h.upsertChat(jid.String(), name, "direct", time.Now().Unix(), "")
		return map[string]any{"conversation": map[string]any{"id": jid.String(), "name": name, "kind": "direct",
			"participants": []any{map[string]any{"id": jid.String(), "name": name, "phone": "+" + jid.User}},
			"updated": time.Now().Unix(), "unread": 0}}, nil
	case "logout":
		if remote, _ := args["remote"].(bool); remote {
			if err := client.Logout(ctx); err != nil {
				h.log.Warn().Err(err).Msg("logout failed")
			}
		}
		client.Disconnect()
		h.setState("needs_login", "")
		return map[string]any{}, nil
	}
	return nil, &failure{Code: "unsupported", Message: cmd}
}

func (h *helper) loginStart(method, phone string) (any, error) {
	client, err := h.ensureClient()
	if err != nil {
		return nil, err
	}
	if client.Store.ID != nil {
		return nil, &failure{Code: "invalid", Message: "this account is already linked"}
	}
	switch method {
	case "qr":
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
		h.mu.Lock()
		h.cancel = cancel
		h.mu.Unlock()
		channel, err := client.GetQRChannel(ctx)
		if err != nil {
			cancel()
			return nil, err
		}
		if err := client.Connect(); err != nil {
			cancel()
			return nil, &failure{Code: "not_connected", Message: "WhatsApp couldn't be reached", Retryable: true}
		}
		go func() {
			defer cancel()
			for item := range channel {
				switch item.Event {
				case whatsmeow.QRChannelEventCode:
					h.event("login.qr", map[string]any{"data": item.Code, "expires": time.Now().Add(item.Timeout).Unix()})
				case "success":
					return // PairSuccess and Connected finish the sign-in
				case "timeout":
					h.event("login.error", map[string]any{"code": "timeout", "message": "The code expired before it was scanned. Try again."})
					return
				case whatsmeow.QRChannelEventPasskeyRequest:
					h.event("login.error", map[string]any{"code": "passkey", "message": "WhatsApp asked for a passkey, which Luma doesn't support yet. Link with a code instead."})
					return
				case "err-client-outdated":
					h.event("login.error", map[string]any{"code": "outdated", "message": "WhatsApp needs a newer version of Luma."})
					return
				case "err-scanned-without-multidevice":
					h.event("login.error", map[string]any{"code": "multidevice", "message": "Update WhatsApp on your phone, then try again."})
					return
				case whatsmeow.QRChannelEventError:
					if !errors.Is(item.Error, context.Canceled) {
						h.event("login.error", map[string]any{"code": "failed", "message": "WhatsApp couldn't link this device. Try again."})
					}
					return
				}
			}
		}()
		return map[string]any{}, nil
	case "code":
		if phone == "" {
			h.event("login.needs", map[string]any{"field": "phone", "hint": "Enter the phone number of your WhatsApp account."})
			return map[string]any{}, nil
		}
		go h.pairWithCode(phone)
		return map[string]any{}, nil
	}
	return nil, &failure{Code: "unsupported", Message: "sign-in method"}
}

func (h *helper) pairWithCode(phone string) {
	client, err := h.ensureClient()
	if err != nil {
		h.event("login.error", map[string]any{"code": "failed", "message": "WhatsApp couldn't start."})
		return
	}
	if !client.IsConnected() {
		if err := client.Connect(); err != nil {
			h.event("login.error", map[string]any{"code": "not_connected", "message": "WhatsApp couldn't be reached. Check your connection."})
			return
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	for !client.IsConnected() && ctx.Err() == nil {
		time.Sleep(200 * time.Millisecond)
	}
	code, err := client.PairPhone(ctx, phone, true, whatsmeow.PairClientChrome, "Chrome (Linux)")
	if err != nil {
		h.log.Warn().Err(err).Msg("pair phone failed")
		h.event("login.needs", map[string]any{"field": "phone", "hint": "WhatsApp didn't accept that number. Include the country code."})
		return
	}
	h.event("login.code", map[string]any{"code": code, "kind": "pairing"})
}

func (h *helper) shutdown() {
	h.mu.Lock()
	client := h.client
	h.mu.Unlock()
	if client != nil {
		client.Disconnect()
	}
	if h.container != nil {
		h.container.Close()
	}
	if h.index != nil {
		h.index.Close()
	}
}

func (h *helper) onEvent(raw any) {
	switch evt := raw.(type) {
	case *events.PairSuccess:
		h.mu.Lock()
		h.loginDone = true
		h.mu.Unlock()
	case *events.Connected:
		h.setState("connected", "")
		h.mu.Lock()
		finish := h.loginDone
		h.loginDone = false
		client := h.client
		h.mu.Unlock()
		if finish && client != nil && client.Store.ID != nil {
			h.event("login.done", map[string]any{"account": map[string]any{"name": client.Store.PushName, "handle": "+" + client.Store.ID.User}})
		}
	case *events.Disconnected:
		h.setState("connecting", "")
	case *events.KeepAliveTimeout:
		h.setState("error", "")
	case *events.KeepAliveRestored:
		h.setState("connected", "")
	case *events.LoggedOut:
		h.setState("needs_login", "")
	case *events.StreamReplaced:
		h.setState("elsewhere", "")
	case *events.ClientOutdated:
		h.setState("outdated", "")
	case *events.TemporaryBan:
		h.setState("error", evt.String())
	case *events.Message:
		h.incoming(evt, true)
	case *events.HistorySync:
		h.history(evt)
	case *events.Receipt:
		state := ""
		switch evt.Type {
		case types.ReceiptTypeRead, types.ReceiptTypeReadSelf:
			state = "read"
		case types.ReceiptTypeDelivered:
			state = "delivered"
		}
		if state != "" && evt.IsFromMe == false {
			for _, id := range evt.MessageIDs {
				h.event("receipt", map[string]any{"conversation": evt.Chat.String(), "message": id, "state": state})
			}
		}
	}
}

func (h *helper) contactName(ctx context.Context, client *whatsmeow.Client, jid types.JID) string {
	if jid.Server == types.GroupServer {
		if info, err := client.GetGroupInfo(ctx, jid); err == nil && info.Name != "" {
			return info.Name
		}
		return "WhatsApp group"
	}
	if contact, err := client.Store.Contacts.GetContact(ctx, jid); err == nil && contact.Found {
		for _, name := range []string{contact.FullName, contact.FirstName, contact.BusinessName, contact.PushName} {
			if strings.TrimSpace(name) != "" {
				return name
			}
		}
	}
	return "+" + jid.User
}

func (h *helper) upsertChat(id, name, kind string, updated int64, preview string) bool {
	var existing string
	var existingUpdated int64
	err := h.index.QueryRow(`SELECT name, updated FROM chats WHERE id=?`, id).Scan(&existing, &existingUpdated)
	if err == sql.ErrNoRows {
		h.index.Exec(`INSERT INTO chats(id, name, kind, updated, preview) VALUES(?,?,?,?,?)`, id, name, kind, updated, preview)
		return true
	}
	if updated >= existingUpdated {
		h.index.Exec(`UPDATE chats SET name=?, updated=?, preview=? WHERE id=?`, name, updated, preview, id)
	}
	return existing != name
}

func (h *helper) chatJSON(id string) map[string]any {
	var name, kind, preview string
	var updated, unread int64
	if err := h.index.QueryRow(`SELECT name, kind, updated, preview, unread FROM chats WHERE id=?`, id).Scan(&name, &kind, &updated, &preview, &unread); err != nil {
		return nil
	}
	return map[string]any{"id": id, "name": name, "kind": kind, "participants": []any{}, "updated": updated, "unread": unread, "preview": preview}
}

func (h *helper) listChats(limit int) (any, error) {
	rows, err := h.index.Query(`SELECT id FROM chats ORDER BY updated DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	ids := []string{}
	for rows.Next() {
		var id string
		rows.Scan(&id)
		ids = append(ids, id)
	}
	rows.Close()
	list := []any{}
	for _, id := range ids {
		if chat := h.chatJSON(id); chat != nil {
			list = append(list, chat)
		}
	}
	return map[string]any{"conversations": list}, nil
}

func (h *helper) listMessages(chat string, limit int) (any, error) {
	rows, err := h.index.Query(`SELECT id, sender, sender_name, from_me, text, time, state, attachments, reply_to FROM messages
		WHERE chat=? ORDER BY time DESC LIMIT ?`, chat, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	list := []any{}
	for rows.Next() {
		var id, sender, senderName, text, state, attachments, replyTo string
		var fromMe int
		var at int64
		rows.Scan(&id, &sender, &senderName, &fromMe, &text, &at, &state, &attachments, &replyTo)
		list = append([]any{messageJSON(chat, id, sender, senderName, fromMe == 1, text, at, state, attachments, replyTo, "")}, list...)
	}
	return map[string]any{"messages": list, "more": len(list) == limit}, nil
}

func messageJSON(chat, id, sender, senderName string, fromMe bool, text string, at int64, state, attachments, replyTo, clientID string) map[string]any {
	parts := []any{}
	json.Unmarshal([]byte(attachments), &parts)
	var from any
	if !fromMe {
		from = map[string]any{"id": sender, "name": senderName}
	}
	message := map[string]any{"id": id, "conversation": chat, "sender": from, "outgoing": fromMe, "text": text,
		"time": at, "state": state, "attachments": parts}
	if replyTo != "" {
		message["reply_to"] = replyTo
	}
	if clientID != "" {
		message["client_id"] = clientID
	}
	return message
}

func (h *helper) incoming(evt *events.Message, notify bool) {
	client, err := h.ensureClient()
	if err != nil {
		return
	}
	msg := evt.Message
	if msg == nil {
		return
	}
	chat := evt.Info.Chat
	if chat.Server == types.BroadcastServer {
		return // status updates and broadcast lists are not conversations
	}
	if revoke := msg.GetProtocolMessage(); revoke != nil {
		if revoke.GetType() == waE2E.ProtocolMessage_REVOKE && revoke.GetKey().GetID() != "" {
			h.index.Exec(`DELETE FROM messages WHERE chat=? AND id=?`, chat.String(), revoke.GetKey().GetID())
			if notify {
				h.event("message", map[string]any{"id": revoke.GetKey().GetID(), "conversation": chat.String(), "deleted": true,
					"outgoing": false, "text": "", "time": evt.Info.Timestamp.Unix(), "state": "received", "attachments": []any{}})
			}
		}
		return
	}
	text, replyTo := messageText(msg)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	attachments := h.saveMedia(ctx, client, evt.Info.ID, msg)
	if text == "" && len(attachments) == 0 {
		return // reactions, polls, calls and other kinds Messages can't show yet
	}
	kind := "direct"
	if evt.Info.IsGroup {
		kind = "group"
	}
	name := h.contactName(ctx, client, chat)
	preview := text
	if preview == "" {
		preview = "Attachment"
	}
	changed := h.upsertChat(chat.String(), name, kind, evt.Info.Timestamp.Unix(), preview)
	senderName := evt.Info.PushName
	if senderName == "" && !evt.Info.IsFromMe {
		senderName = h.contactName(ctx, client, evt.Info.Sender)
	}
	state := "received"
	if evt.Info.IsFromMe {
		state = "sent"
	}
	encoded, _ := json.Marshal(attachments)
	h.index.Exec(`INSERT OR REPLACE INTO messages(chat, id, sender, sender_name, from_me, text, time, state, attachments, reply_to)
		VALUES(?,?,?,?,?,?,?,?,?,?)`, chat.String(), evt.Info.ID, evt.Info.Sender.String(), senderName, evt.Info.IsFromMe,
		text, evt.Info.Timestamp.Unix(), state, string(encoded), replyTo)
	if !notify {
		return
	}
	if changed {
		if payload := h.chatJSON(chat.String()); payload != nil {
			h.event("conversation", payload)
		}
	}
	h.mu.Lock()
	clientID := h.pending[evt.Info.ID]
	h.mu.Unlock()
	h.event("message", messageJSON(chat.String(), evt.Info.ID, evt.Info.Sender.String(), senderName, evt.Info.IsFromMe, text,
		evt.Info.Timestamp.Unix(), state, string(encoded), replyTo, clientID))
}

func messageText(msg *waE2E.Message) (string, string) {
	switch {
	case msg.GetConversation() != "":
		return msg.GetConversation(), ""
	case msg.GetExtendedTextMessage() != nil:
		return msg.GetExtendedTextMessage().GetText(), msg.GetExtendedTextMessage().GetContextInfo().GetStanzaID()
	case msg.GetImageMessage() != nil:
		return msg.GetImageMessage().GetCaption(), msg.GetImageMessage().GetContextInfo().GetStanzaID()
	case msg.GetVideoMessage() != nil:
		return msg.GetVideoMessage().GetCaption(), msg.GetVideoMessage().GetContextInfo().GetStanzaID()
	case msg.GetDocumentMessage() != nil:
		return msg.GetDocumentMessage().GetCaption(), msg.GetDocumentMessage().GetContextInfo().GetStanzaID()
	}
	return "", ""
}

func (h *helper) saveMedia(ctx context.Context, client *whatsmeow.Client, id string, msg *waE2E.Message) []any {
	type media struct {
		download whatsmeow.DownloadableMessage
		mime     string
		name     string
		size     uint64
	}
	var item *media
	switch {
	case msg.GetImageMessage() != nil:
		m := msg.GetImageMessage()
		item = &media{m, m.GetMimetype(), "image", m.GetFileLength()}
	case msg.GetVideoMessage() != nil:
		m := msg.GetVideoMessage()
		item = &media{m, m.GetMimetype(), "video", m.GetFileLength()}
	case msg.GetAudioMessage() != nil:
		m := msg.GetAudioMessage()
		item = &media{m, m.GetMimetype(), "voice message", m.GetFileLength()}
	case msg.GetDocumentMessage() != nil:
		m := msg.GetDocumentMessage()
		item = &media{m, m.GetMimetype(), m.GetFileName(), m.GetFileLength()}
	case msg.GetStickerMessage() != nil:
		m := msg.GetStickerMessage()
		item = &media{m, m.GetMimetype(), "sticker", m.GetFileLength()}
	}
	if item == nil || item.size > maxMedia {
		return []any{}
	}
	dir := filepath.Join(h.dataDir, "media")
	os.MkdirAll(dir, 0o700)
	extension := ""
	if extensions, _ := mime.ExtensionsByType(strings.Split(item.mime, ";")[0]); len(extensions) > 0 {
		extension = extensions[0]
	}
	name := safeName.ReplaceAllString(item.name, "_")
	if filepath.Ext(name) == "" {
		name += extension
	}
	path := filepath.Join(dir, safeName.ReplaceAllString(id, "_")+"-"+name)
	if info, err := os.Stat(path); err == nil {
		return []any{map[string]any{"path": path, "mime": item.mime, "name": name, "size": info.Size()}}
	}
	data, err := client.Download(ctx, item.download)
	if err != nil || len(data) > maxMedia {
		h.log.Warn().Err(err).Msg("media download failed")
		return []any{}
	}
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return []any{}
	}
	return []any{map[string]any{"path": path, "mime": item.mime, "name": name, "size": len(data)}}
}

func (h *helper) history(evt *events.HistorySync) {
	client, err := h.ensureClient()
	if err != nil || evt.Data == nil {
		return
	}
	for _, conv := range evt.Data.GetConversations() {
		chat, err := types.ParseJID(conv.GetID())
		if err != nil || chat.Server == types.BroadcastServer {
			continue
		}
		messages := conv.GetMessages()
		if len(messages) > historyPerChat {
			messages = messages[:historyPerChat]
		}
		for _, item := range messages {
			parsed, err := client.ParseWebMessage(chat, item.GetMessage())
			if err == nil {
				h.incoming(parsed, false)
			}
		}
		name := conv.GetDisplayName()
		if name == "" {
			name = conv.GetName()
		}
		if name != "" {
			h.index.Exec(`UPDATE chats SET name=? WHERE id=?`, name, chat.String())
		}
		h.index.Exec(`UPDATE chats SET unread=? WHERE id=?`, conv.GetUnreadCount(), chat.String())
		if payload := h.chatJSON(chat.String()); payload != nil {
			h.event("conversation", payload)
			if list, err := h.listMessages(chat.String(), historyPerChat); err == nil {
				for _, message := range list.(map[string]any)["messages"].([]any) {
					h.event("message", message)
				}
			}
		}
	}
}

func (h *helper) send(ctx context.Context, client *whatsmeow.Client, cmd string, args map[string]any) (any, error) {
	convID, _ := args["conversation"].(string)
	clientID, _ := args["client_id"].(string)
	chat, err := types.ParseJID(convID)
	if err != nil {
		return nil, &failure{Code: "invalid", Message: "conversation"}
	}
	var message *waE2E.Message
	var context_ *waE2E.ContextInfo
	if reply, _ := args["reply_to"].(string); reply != "" {
		var sender string
		h.index.QueryRow(`SELECT sender FROM messages WHERE chat=? AND id=?`, chat.String(), reply).Scan(&sender)
		context_ = &waE2E.ContextInfo{StanzaID: proto.String(reply), Participant: proto.String(sender)}
	}
	if cmd == "media.send" {
		path, _ := args["path"].(string)
		mimeType, _ := args["mime"].(string)
		name, _ := args["name"].(string)
		caption, _ := args["caption"].(string)
		data, err := os.ReadFile(path)
		if err != nil || len(data) > maxMedia {
			return nil, &failure{Code: "invalid", Message: "the attachment can't be read or is too large"}
		}
		kind := whatsmeow.MediaDocument
		switch {
		case strings.HasPrefix(mimeType, "image/"):
			kind = whatsmeow.MediaImage
		case strings.HasPrefix(mimeType, "video/"):
			kind = whatsmeow.MediaVideo
		case strings.HasPrefix(mimeType, "audio/"):
			kind = whatsmeow.MediaAudio
		}
		upload, err := client.Upload(ctx, data, kind)
		if err != nil {
			return nil, &failure{Code: "not_connected", Message: err.Error(), Retryable: true}
		}
		size := uint64(len(data))
		switch kind {
		case whatsmeow.MediaImage:
			message = &waE2E.Message{ImageMessage: &waE2E.ImageMessage{Caption: proto.String(caption), Mimetype: proto.String(mimeType),
				URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey, FileEncSHA256: upload.FileEncSHA256,
				FileSHA256: upload.FileSHA256, FileLength: &size, ContextInfo: context_}}
		case whatsmeow.MediaVideo:
			message = &waE2E.Message{VideoMessage: &waE2E.VideoMessage{Caption: proto.String(caption), Mimetype: proto.String(mimeType),
				URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey, FileEncSHA256: upload.FileEncSHA256,
				FileSHA256: upload.FileSHA256, FileLength: &size, ContextInfo: context_}}
		case whatsmeow.MediaAudio:
			message = &waE2E.Message{AudioMessage: &waE2E.AudioMessage{Mimetype: proto.String(mimeType),
				URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey, FileEncSHA256: upload.FileEncSHA256,
				FileSHA256: upload.FileSHA256, FileLength: &size, ContextInfo: context_}}
		default:
			message = &waE2E.Message{DocumentMessage: &waE2E.DocumentMessage{Caption: proto.String(caption), Mimetype: proto.String(mimeType),
				FileName: proto.String(name), URL: &upload.URL, DirectPath: &upload.DirectPath, MediaKey: upload.MediaKey,
				FileEncSHA256: upload.FileEncSHA256, FileSHA256: upload.FileSHA256, FileLength: &size, ContextInfo: context_}}
		}
	} else {
		text, _ := args["text"].(string)
		if context_ != nil {
			message = &waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{Text: proto.String(text), ContextInfo: context_}}
		} else {
			message = &waE2E.Message{Conversation: proto.String(text)}
		}
	}
	id := client.GenerateMessageID()
	h.mu.Lock()
	h.pending[id] = clientID
	h.mu.Unlock()
	response, err := client.SendMessage(ctx, chat, message, whatsmeow.SendRequestExtra{ID: id})
	h.mu.Lock()
	delete(h.pending, id)
	h.mu.Unlock()
	if err != nil {
		return nil, &failure{Code: "not_connected", Message: err.Error(), Retryable: true}
	}
	text, replyTo := messageText(message)
	h.upsertChat(chat.String(), h.contactName(ctx, client, chat), map[bool]string{true: "group", false: "direct"}[chat.Server == types.GroupServer], response.Timestamp.Unix(), text)
	h.index.Exec(`INSERT OR REPLACE INTO messages(chat, id, sender, sender_name, from_me, text, time, state, attachments, reply_to)
		VALUES(?,?,?,?,1,?,?,'sent','[]',?)`, chat.String(), id, client.Store.ID.String(), client.Store.PushName, text, response.Timestamp.Unix(), replyTo)
	h.event("message", messageJSON(chat.String(), id, client.Store.ID.String(), "", true, text, response.Timestamp.Unix(), "sent", "[]", replyTo, clientID))
	return map[string]any{"message": id}, nil
}
