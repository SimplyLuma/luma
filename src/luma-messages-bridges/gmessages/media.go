// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The Google Messages attachment pipeline.
//
// Attachments are never downloaded while a message is being mapped. Every media
// part of a message gets a state (pending, downloading, done, failed) and a
// bounded pool of workers downloads it, retries it with backoff and jitter,
// shows the thumbnail when the full file isn't there, and re-reads the message
// for fresh media ids. Each change reaches Messages as a "media" event;
// Messages keeps the durable state and asks again with media.fetch after a
// restart, a reconnect, or a tap.
//
// A part whose full-size media id hasn't arrived is re-read on a backoff until
// it does, and then reported as a plain failure rather than waiting for ever.
// Every step of that is a read of the conversation: the phone is never asked to
// upload or send anything, so nothing on the receive path can be taken for a
// delivery (see outbound.go).
package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"math/rand/v2"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/rs/zerolog"
	"google.golang.org/protobuf/proto"

	"go.mau.fi/mautrix-gmessages/pkg/libgm"
	"go.mau.fi/mautrix-gmessages/pkg/libgm/crypto"
	"go.mau.fi/mautrix-gmessages/pkg/libgm/gmproto"
	"go.mau.fi/mautrix-gmessages/pkg/libgm/util"
)

const (
	mediaWorkers     = 3
	mediaRunAttempts = 6
	previewLimit     = 4 * 1024 * 1024
	lookupPageSize   = 50
	// A message Messages asks about but this run hasn't seen is looked for this far back.
	lookupMaxPages = 40
	// Fresh media ids for a message seen this run are looked for this far back.
	refreshMaxPages = 10
	mmsPart         = "mms"
)

// Delays before the second to sixth attempt in one run, each with ±25% jitter.
var mediaBackoff = []time.Duration{2 * time.Second, 10 * time.Second, 30 * time.Second, 2 * time.Minute, 5 * time.Minute}

// Delays between read-only re-reads of a message whose full-size media id
// hasn't appeared yet, each with ±25% jitter.
var mediaWaitBackoff = []time.Duration{5 * time.Second, 15 * time.Second, 45 * time.Second, 2 * time.Minute,
	5 * time.Minute, 10 * time.Minute, 15 * time.Minute}

// Re-reads of such a message before it is reported as a plain failure rather
// than an endless wait. Eight re-reads span a little over half an hour.
const mediaWaitAttempts = 8

// mediaError says why a download failed, in words Messages and the journal can act on.
type mediaError struct {
	Class  string // network, server, unauthorized, expired, gone, http, empty, truncated, decrypt, io, too_large, not_connected, waiting_for_phone, no_full_size, phone_download_failed
	Status int
	Length int64
	Bytes  int64
	Err    error
}

func (e *mediaError) Error() string {
	if e.Err != nil {
		return e.Class + ": " + e.Err.Error()
	}
	return e.Class
}

func (e *mediaError) Unwrap() error { return e.Err }

// phoneSide is true when another try at the same media id cannot help: Google's
// servers don't have the file (any more), or what they have isn't the file.
func (e *mediaError) phoneSide() bool {
	switch e.Class {
	case "expired", "gone", "empty", "decrypt", "http", "waiting_for_phone", "no_full_size", "phone_download_failed":
		return true
	}
	return false
}

func asMediaError(err error) *mediaError {
	var found *mediaError
	if errors.As(err, &found) {
		return found
	}
	return &mediaError{Class: "network", Err: err}
}

func statusClass(code int) string {
	switch {
	case code == http.StatusUnauthorized || code == http.StatusForbidden:
		return "unauthorized"
	case code == http.StatusNotFound || code == http.StatusGone:
		return "expired"
	case code == http.StatusRequestTimeout || code == http.StatusTooManyRequests || code >= 500:
		return "server"
	}
	return "http"
}

// ── Downloading one file ────────────────────────────────────────────────────

// downloader fetches and decrypts one media file. libgm's DownloadMedia never
// looks at the HTTP status and hands back a decrypting stream over whatever
// body arrived, so an error page, an empty file and a cut-off transfer all read
// as "unexpected EOF". This checks the status and counts what arrived.
type downloader struct {
	client *http.Client
	url    string
	auth   func() *gmproto.AuthMessage
}

type countingReader struct {
	r    io.Reader
	n    int64
	err  error
	head []byte // the first two bytes, to recognise an empty encrypted object
}

func (c *countingReader) Read(p []byte) (int, error) {
	n, err := c.r.Read(p)
	if len(c.head) < 2 {
		c.head = append(c.head, p[:min(n, 2-len(c.head))]...)
	}
	c.n += int64(n)
	if err != nil && err != io.EOF {
		c.err = err
	}
	return n, err
}

type recordingWriter struct {
	w   io.Writer
	err error
}

func (r *recordingWriter) Write(p []byte) (int, error) {
	n, err := r.w.Write(p)
	if err != nil {
		r.err = err
	}
	return n, err
}

func isDecryptError(err error) bool {
	text := err.Error()
	for _, marker := range []string{"failed to decrypt", "invalid first-byte header", "chunk size too large", "invalid encrypted data length", "message authentication failed"} {
		if strings.Contains(text, marker) {
			return true
		}
	}
	return false
}

func (d *downloader) download(ctx context.Context, mediaID string, key []byte, dst string, limit int64) (int64, error) {
	if len(key) != 32 {
		return 0, &mediaError{Class: "decrypt", Err: fmt.Errorf("the media key is %d bytes", len(key))}
	}
	cryptor, err := crypto.NewAESGCMHelper(key)
	if err != nil {
		return 0, &mediaError{Class: "decrypt", Err: err}
	}
	auth := d.auth()
	if auth == nil {
		return 0, &mediaError{Class: "not_connected", Err: errors.New("not signed in")}
	}
	auth.RequestID = uuid.NewString()
	metadata, err := proto.Marshal(&gmproto.DownloadAttachmentRequest{
		Info:     &gmproto.AttachmentInfo{AttachmentID: mediaID, Encrypted: true},
		AuthData: auth,
	})
	if err != nil {
		return 0, &mediaError{Class: "io", Err: err}
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, d.url, nil)
	if err != nil {
		return 0, &mediaError{Class: "io", Err: err}
	}
	util.BuildUploadHeaders(req, base64.StdEncoding.EncodeToString(metadata))
	// libgm names the encodings by hand, which stops Go from decompressing the body.
	req.Header.Del("accept-encoding")
	res, err := d.client.Do(req)
	if err != nil {
		return 0, &mediaError{Class: "network", Err: err}
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK {
		n, _ := io.Copy(io.Discard, io.LimitReader(res.Body, 64*1024))
		return 0, &mediaError{Class: statusClass(res.StatusCode), Status: res.StatusCode, Length: res.ContentLength, Bytes: n,
			Err: fmt.Errorf("http %d", res.StatusCode)}
	}
	body := &countingReader{r: res.Body}
	temporary := dst + ".part"
	file, err := os.OpenFile(temporary, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return 0, &mediaError{Class: "io", Err: err}
	}
	writer := &recordingWriter{w: file}
	size, copyErr := io.Copy(writer, io.LimitReader(cryptor.DecryptStream(io.NopCloser(body)), limit+1))
	if syncErr := file.Sync(); copyErr == nil && syncErr != nil {
		copyErr, writer.err = syncErr, syncErr
	}
	if closeErr := file.Close(); copyErr == nil && closeErr != nil {
		copyErr, writer.err = closeErr, closeErr
	}
	failure := func(class string, err error) (int64, error) {
		os.Remove(temporary)
		return 0, &mediaError{Class: class, Status: res.StatusCode, Length: res.ContentLength, Bytes: body.n, Err: err}
	}
	// What Google's media service sends for a file it no longer holds: 200 OK
	// and an encrypted object with no content, which is only the two-byte
	// chunk header (0, log2 of the chunk size). Seen on a real account
	// (2026-09-16) for every attachment older than about six months, pictures,
	// thumbnails and PDFs alike, while every newer one downloaded. It is not a
	// network fault and trying the same id again cannot help.
	emptyObject := res.StatusCode == http.StatusOK && body.n == 2 && len(body.head) == 2 && body.head[0] == 0 &&
		body.head[1] >= 10 && body.head[1] <= 24
	switch {
	case copyErr == nil && size > limit:
		return failure("too_large", fmt.Errorf("more than %d bytes", limit))
	case emptyObject:
		return failure("gone", errors.New("Google Messages no longer holds this file"))
	case copyErr == nil && size == 0:
		return failure("empty", errors.New("no data"))
	case copyErr == nil:
	case writer.err != nil:
		return failure("io", copyErr)
	case body.err != nil && !errors.Is(body.err, io.ErrUnexpectedEOF):
		return failure("network", copyErr)
	case res.ContentLength >= 0 && body.n < res.ContentLength:
		return failure("truncated", copyErr)
	case body.n < 3:
		return failure("empty", copyErr)
	case errors.Is(copyErr, io.ErrUnexpectedEOF):
		return failure("truncated", copyErr)
	case isDecryptError(copyErr):
		return failure("decrypt", copyErr)
	default:
		return failure("truncated", copyErr)
	}
	if err := os.Rename(temporary, dst); err != nil {
		return failure("io", err)
	}
	return size, nil
}

// ── The queue ───────────────────────────────────────────────────────────────

// mediaNetwork is what the queue needs from Google Messages; tests replace it.
type mediaNetwork interface {
	ready() bool
	download(ctx context.Context, mediaID string, key []byte, dst string, limit int64) (int64, error)
	listMessages(ctx context.Context, conversation string, cursor *gmproto.Cursor) (*gmproto.ListMessagesResponse, error)
}

type mediaPart struct {
	conversation string
	message      string
	actionID     string
	index        int // -1 for a message whose MMS the phone hasn't downloaded yet
	media        *gmproto.MediaContent
	status       gmproto.MessageStatusType
	incoming     bool // someone else sent it; see isIncoming
}

func (p mediaPart) id() string {
	if p.index < 0 {
		return mmsPart
	}
	return strconv.Itoa(p.index)
}

type mediaEntry struct {
	part         mediaPart
	state        string // pending | downloading | done | failed
	class        string
	attempt      int // failed attempts since Messages last asked
	queued       bool
	running      bool
	timer        *time.Timer
	refresh      bool
	waits        int // read-only re-reads spent waiting for a full-size media id
	previewTried bool
	askingPhone  bool // a person's full-size request is in flight (fullsize.go)
	tried        string
	size         int64
	preview      string
}

type mediaQueue struct {
	dir    string
	log    zerolog.Logger
	net    mediaNetwork
	emit   func(name string, data any)
	found  func(msg *gmproto.Message) // a message media.fetch looked up
	after  func(time.Duration, func()) *time.Timer
	jitter func(time.Duration) time.Duration

	// fromSelf says whether this account wrote a message; nil means "treat as
	// ours", so nothing can be asked of the phone. phone is the only way to ask
	// for a full-size file, and only askPhone uses it (fullsize.go).
	fromSelf func(msg *gmproto.Message) bool
	phone    fullSizeRequester

	mu        sync.Mutex
	cond      *sync.Cond
	entries   map[string]*mediaEntry
	phoneAsks map[string]time.Time // presses waiting for their message to be looked up
	order     []string
	lookups   map[string]map[string]bool
	closed    bool
}

func newMediaQueue(dir string, log zerolog.Logger, network mediaNetwork, emit func(string, any), found func(*gmproto.Message)) *mediaQueue {
	q := &mediaQueue{
		dir: dir, log: log, net: network, emit: emit, found: found,
		after: time.AfterFunc,
		jitter: func(d time.Duration) time.Duration {
			return time.Duration(float64(d) * (0.75 + rand.Float64()*0.5))
		},
		entries:   map[string]*mediaEntry{},
		lookups:   map[string]map[string]bool{},
		phoneAsks: map[string]time.Time{},
	}
	q.cond = sync.NewCond(&q.mu)
	for i := 0; i < mediaWorkers; i++ {
		go q.work()
	}
	return q
}

func (q *mediaQueue) close() {
	q.mu.Lock()
	q.closed = true
	for _, e := range q.entries {
		if e.timer != nil {
			e.timer.Stop()
		}
	}
	q.mu.Unlock()
	q.cond.Broadcast()
}

func entryKey(message, part string) string { return message + "/" + part }

// Incoming MMS the phone is still fetching from the carrier.
func phoneFetching(status gmproto.MessageStatusType) bool {
	switch status {
	case gmproto.MessageStatusType_INCOMING_AUTO_DOWNLOADING, gmproto.MessageStatusType_INCOMING_RETRYING_AUTO_DOWNLOAD,
		gmproto.MessageStatusType_INCOMING_MANUAL_DOWNLOADING, gmproto.MessageStatusType_INCOMING_RETRYING_MANUAL_DOWNLOAD,
		gmproto.MessageStatusType_INCOMING_AWAITING_AUTO_DOWNLOAD:
		return true
	}
	return false
}

// Incoming MMS the phone didn't or couldn't fetch; only the phone can change that.
func phoneFailed(status gmproto.MessageStatusType) string {
	switch status {
	case gmproto.MessageStatusType_INCOMING_YET_TO_MANUAL_DOWNLOAD:
		return "phone_manual_download"
	case gmproto.MessageStatusType_INCOMING_DOWNLOAD_FAILED, gmproto.MessageStatusType_INCOMING_DOWNLOAD_CANCELED,
		gmproto.MessageStatusType_INCOMING_EXPIRED_OR_NOT_AVAILABLE, gmproto.MessageStatusType_INCOMING_DOWNLOAD_RESTRICTED,
		gmproto.MessageStatusType_INCOMING_DOWNLOAD_FAILED_SIM_HAS_NO_DATA:
		return "phone_download_failed"
	case gmproto.MessageStatusType_INCOMING_DOWNLOAD_FAILED_TOO_LARGE:
		return "too_large"
	}
	return ""
}

func mimeOf(media *gmproto.MediaContent) string {
	if value := strings.TrimSpace(media.GetMimeType()); value != "" {
		return value
	}
	if format := libgm.FormatToMediaType[media.GetFormat()].Format; format != "" {
		return format
	}
	return "application/octet-stream"
}

func fileName(media *gmproto.MediaContent) string {
	name := safeName.ReplaceAllString(firstNonEmpty(media.GetMediaName(), "attachment"), "_")
	if filepath.Ext(name) == "" {
		// Messages decides how to show a file by its name; Google often sends bare numbers.
		name += extensionFor(mimeOf(media))
	}
	return name
}

// path is where a part's file is kept; the name is the one earlier helpers used,
// so files they downloaded still count.
func (q *mediaQueue) path(p mediaPart) string {
	if p.index < 0 || p.media == nil {
		return ""
	}
	return filepath.Join(q.dir, fmt.Sprintf("%s-%d-%s", safeName.ReplaceAllString(p.message, "_"), p.index, fileName(p.media)))
}

// A part's file is the full attachment only when it came from the part's
// media id. Earlier helpers also saved a message's inline preview bytes (a
// blurry image of about a kilobyte, sent while the phone is still uploading)
// under the same name and called that done, so the full picture was never
// fetched. The id a file was downloaded from is kept beside it.
const sourceSuffix = ".source"

// suspectLegacyFile is a file with no recorded source that is far smaller
// than the size Google reports for the attachment: a preview, not the file.
func suspectLegacyFile(size, reported int64) bool {
	return reported >= 64*1024 && size*4 < reported
}

func (q *mediaQueue) doneFile(p mediaPart) (int64, bool) {
	path := q.path(p)
	size, ok := nonEmptyFile(path)
	if !ok {
		return 0, false
	}
	id := p.media.GetMediaID()
	source, err := os.ReadFile(path + sourceSuffix)
	switch {
	case err == nil:
		// Downloaded from an id; a different id now means the phone uploaded it again.
		return size, id == "" || string(source) == id
	case suspectLegacyFile(size, p.media.GetSize()):
		return 0, false
	}
	return size, true
}

func nonEmptyFile(path string) (int64, bool) {
	if path == "" {
		return 0, false
	}
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() == 0 {
		return 0, false
	}
	return info.Size(), true
}

func (q *mediaQueue) describeLocked(e *mediaEntry) map[string]any {
	p := e.part
	data := map[string]any{"conversation": p.conversation, "message": p.message, "part": p.id(), "state": e.state,
		"attempt": e.attempt}
	if p.media != nil {
		data["mime"] = mimeOf(p.media)
		data["name"] = fileName(p.media)
		data["size"] = p.media.GetSize()
	} else {
		data["mime"], data["name"], data["size"] = "application/octet-stream", "", 0
	}
	if e.state == "done" {
		data["path"] = q.path(p)
		data["size"] = e.size
	} else if e.class != "" {
		data["error"] = e.class
	}
	if e.state == "failed" {
		data["retryable"] = e.class != "too_large" && e.class != "gone"
	}
	if e.preview != "" {
		data["preview"] = e.preview
	}
	return data
}

// observe records a message's media parts and returns their attachment
// descriptors plus a function that starts the downloads they need. The caller
// runs it after the message has reached Messages, so a download that finishes
// at once never reports on a message Messages hasn't seen.
func (q *mediaQueue) observe(msg *gmproto.Message) ([]any, func()) {
	status := msg.GetMessageStatus().GetStatus()
	incoming := q.isIncoming(msg)
	parts := []mediaPart{}
	for index, info := range msg.GetMessageInfo() {
		if media := info.GetMediaContent(); media != nil {
			parts = append(parts, mediaPart{conversation: msg.GetConversationID(), message: msg.GetMessageID(),
				actionID: info.GetActionMessageID(), index: index, media: media, status: status, incoming: incoming})
		}
	}
	if len(parts) == 0 && (phoneFetching(status) || phoneFailed(status) != "") {
		parts = append(parts, mediaPart{conversation: msg.GetConversationID(), message: msg.GetMessageID(), index: -1, status: status})
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	if len(parts) > 0 {
		// An MMS placeholder gives way to the parts the phone fetched.
		if placeholder := q.entries[entryKey(msg.GetMessageID(), mmsPart)]; placeholder != nil && parts[0].index >= 0 {
			if placeholder.timer != nil {
				placeholder.timer.Stop()
			}
			delete(q.entries, entryKey(msg.GetMessageID(), mmsPart))
		}
	}
	descriptors := []any{}
	start := []string{}
	for _, p := range parts {
		key := entryKey(p.message, p.id())
		e := q.entries[key]
		if e == nil {
			e = &mediaEntry{state: "pending"}
			q.entries[key] = e
		}
		e.part = p
		if q.classifyLocked(e) {
			if e.state != "failed" && p.media.GetMediaID() != "" {
				e.state = "downloading"
			}
			start = append(start, key)
		} else if e.state == "pending" && e.class == "waiting_for_phone" {
			// Nothing to download yet. Keep re-reading the message until its
			// full-size media id turns up, instead of waiting for an update
			// that may never arrive.
			q.waitForMediaIDLocked(key, e)
		}
		q.pressedBeforeLookupLocked(key, e)
		descriptors = append(descriptors, q.describeLocked(e))
	}
	return descriptors, func() {
		q.mu.Lock()
		for _, key := range start {
			q.enqueueLocked(key)
		}
		q.mu.Unlock()
	}
}

// classifyLocked sets an entry's state from what is known and reports whether
// it needs a download started.
func (q *mediaQueue) classifyLocked(e *mediaEntry) bool {
	p := e.part
	if p.media != nil {
		if size, ok := q.doneFile(p); ok {
			e.state, e.class, e.size = "done", "", size
			return false
		}
	}
	if e.state == "done" {
		e.state = "pending" // the file went away
	}
	media := p.media
	switch {
	case media == nil && phoneFetching(p.status):
		e.state, e.class = "pending", "waiting_for_phone"
		return false
	case media == nil:
		e.state, e.class = "failed", phoneFailed(p.status)
		return false
	case media.GetMediaID() == "" && media.GetThumbnailMediaID() == "" && len(media.GetMediaData()) == 0:
		// The phone is still uploading it; its message is updated when it's there.
		e.state, e.class = "pending", "waiting_for_phone"
		return false
	case media.GetMediaID() == "":
		// Only a thumbnail or inline preview bytes so far. They are shown as the
		// preview, never as the file, and the message's own update with a media
		// id starts the download. The phone is never asked for anything.
		e.state, e.class = "pending", "waiting_for_phone"
		return e.preview == "" && !e.queued && !e.running
	case media.GetSize() > maxMedia:
		if e.state != "failed" {
			e.state, e.class = "failed", "too_large"
			return media.GetThumbnailMediaID() != "" && e.preview == ""
		}
		return false
	}
	if e.queued || e.running {
		return false
	}
	if id := media.GetMediaID(); id != "" && e.tried != "" && id != e.tried {
		// New media ids: the phone uploaded the file again.
		e.attempt, e.refresh, e.waits = 0, false, 0
		return true
	}
	if media.GetMediaID() != "" && e.state == "failed" &&
		(e.class == "no_full_size" || e.class == "waiting_for_phone") {
		// The file was published after the wait had been given up on, and a
		// later read of the conversation has just found it. Take it now: the
		// person should get the picture without having to ask again.
		e.attempt, e.refresh, e.waits = 0, false, 0
		e.state, e.class = "pending", ""
		return true
	}
	if e.state == "failed" || e.timer != nil {
		return false // waiting for its retry, or for Messages to ask again
	}
	return true
}

// waitForMediaIDLocked arms the next re-read of a message whose full-size media
// id hasn't arrived yet, and reports a plain failure once the re-reads are
// spent, so a picture never waits behind a spinner forever.
//
// The re-read is listMessages and nothing else: reading a conversation cannot
// make the phone deliver anything. The phone is never asked to act, so no part
// of the receive path can be taken for a send (see outbound.go).
func (q *mediaQueue) waitForMediaIDLocked(key string, e *mediaEntry) {
	if e.timer != nil || e.queued || e.running || q.closed {
		return
	}
	if e.waits >= mediaWaitAttempts {
		if e.state != "failed" {
			e.state, e.class = "failed", "no_full_size"
			q.log.Warn().Str("message_id", e.part.message).Str("part", e.part.id()).Str("mime", mimeOfPart(e.part)).
				Int64("size", e.part.media.GetSize()).Int("re_reads", e.waits).
				Msg("no full-size media id after every re-read; reported as a failure the person can retry")
		}
		return
	}
	delay := q.jitter(mediaWaitBackoff[min(e.waits, len(mediaWaitBackoff)-1)])
	e.waits++
	q.log.Debug().Str("message_id", e.part.message).Str("part", e.part.id()).Int("re_read", e.waits).
		Dur("in", delay).Bool("has_preview", e.preview != "").Msg("no full-size media id yet; re-reading the message")
	e.timer = q.after(delay, func() {
		q.mu.Lock()
		if current := q.entries[key]; current == e && e.timer != nil {
			e.timer = nil
			e.refresh = true
			q.enqueueLocked(key)
		}
		q.mu.Unlock()
	})
}

func (q *mediaQueue) enqueueLocked(key string) {
	e := q.entries[key]
	if e == nil || e.queued || e.running || q.closed {
		return
	}
	if e.timer != nil {
		e.timer.Stop()
		e.timer = nil
	}
	e.queued = true
	if e.state != "done" && e.part.media.GetMediaID() != "" {
		e.state = "downloading"
	}
	q.order = append(q.order, key)
	q.cond.Signal()
}

func (q *mediaQueue) work() {
	for {
		q.mu.Lock()
		for len(q.order) == 0 && !q.closed {
			q.cond.Wait()
		}
		if q.closed {
			q.mu.Unlock()
			return
		}
		key := q.order[0]
		q.order = q.order[1:]
		e := q.entries[key]
		if e == nil {
			q.mu.Unlock()
			continue
		}
		e.queued, e.running = false, true
		part, refresh := e.part, e.refresh
		e.refresh = false
		q.mu.Unlock()
		q.run(key, part, refresh)
	}
}

func (q *mediaQueue) run(key string, part mediaPart, refresh bool) {
	if !q.net.ready() {
		q.failed(key, part, &mediaError{Class: "not_connected", Err: errors.New("Google Messages isn't connected")})
		return
	}
	if refresh {
		if fresh := q.refresh(part); fresh != nil {
			part = *fresh
			q.mu.Lock()
			if e := q.entries[key]; e != nil {
				e.part = part
			}
			q.mu.Unlock()
		}
	}
	media := part.media
	if media == nil {
		q.failed(key, part, &mediaError{Class: firstNonEmpty(phoneFailed(part.status), "waiting_for_phone")})
		return
	}
	if media.GetSize() > maxMedia {
		q.failed(key, part, &mediaError{Class: "too_large"})
		return
	}
	path := q.path(part)
	if err := os.MkdirAll(q.dir, 0o700); err != nil {
		q.failed(key, part, &mediaError{Class: "io", Err: err})
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	started := time.Now()
	var size int64
	var err error
	if media.GetMediaID() == "" {
		q.previewOnly(key, part)
		return
	}
	size, err = q.net.download(ctx, media.GetMediaID(), media.GetDecryptionKey(), path, maxMedia)
	if err == nil {
		if writeErr := os.WriteFile(path+sourceSuffix, []byte(media.GetMediaID()), 0o600); writeErr != nil {
			os.Remove(path)
			err = &mediaError{Class: "io", Err: writeErr}
		}
	}
	if err != nil {
		q.failed(key, part, asMediaError(err))
		return
	}
	if strings.HasPrefix(mimeOf(media), "image/hei") {
		// Few systems can decode HEIC; the thumbnail shows where the photo can't.
		q.fetchPreview(key, part)
	}
	q.mu.Lock()
	e := q.entries[key]
	if e == nil {
		q.mu.Unlock()
		return
	}
	e.running, e.state, e.class, e.size, e.tried = false, "done", "", size, media.GetMediaID()
	recovered := e.attempt
	data := q.describeLocked(e)
	q.mu.Unlock()
	event := q.log.Debug()
	if recovered > 0 {
		event = q.log.Warn()
	}
	event.Str("message_id", part.message).Str("part", part.id()).Str("mime", mimeOf(media)).Int64("size", size).
		Int("attempt", recovered+1).Dur("took", time.Since(started)).Msg("media download finished")
	q.emit("media", data)
}

// previewOnly handles a part that has a thumbnail or inline preview bytes but no
// media id yet: the preview is saved and reported, and the part waits for the
// message update that carries its media id. Nothing is requested of the phone:
// GetFullSizeImage and every other call that makes the phone act are
// forbidden in this helper (see outbound.go).
func (q *mediaQueue) previewOnly(key string, part mediaPart) {
	media := part.media
	q.mu.Lock()
	e := q.entries[key]
	if e == nil {
		q.mu.Unlock()
		return
	}
	needPreview := e.preview == ""
	q.mu.Unlock()
	if needPreview {
		if media.GetThumbnailMediaID() != "" {
			q.fetchPreview(key, part)
		} else if len(media.GetMediaData()) > 0 {
			q.inlinePreview(key, part)
		}
	}
	q.mu.Lock()
	e = q.entries[key]
	if e == nil {
		q.mu.Unlock()
		return
	}
	e.running = false
	if e.state != "done" {
		e.state, e.class = "pending", "waiting_for_phone"
	}
	restart := e.part.media.GetMediaID() != "" && e.state != "done"
	if !restart && e.state != "done" {
		// Still no full-size media id: arm the next re-read, or give up plainly.
		q.waitForMediaIDLocked(key, e)
	}
	data := q.describeLocked(e)
	q.mu.Unlock()
	q.emit("media", data)
	if restart {
		// The update with the media id arrived while the preview was being saved.
		q.mu.Lock()
		q.enqueueLocked(key)
		q.mu.Unlock()
	}
}

// inlinePreview saves the small image bytes a message carries while its file uploads.
func (q *mediaQueue) inlinePreview(key string, part mediaPart) {
	data := part.media.GetMediaData()
	if int64(len(data)) > previewLimit {
		return
	}
	base := strings.TrimSuffix(q.path(part), filepath.Ext(q.path(part))) + "-preview"
	head := data
	if len(head) > 512 {
		head = head[:512]
	}
	path := base + extensionFor(http.DetectContentType(head))
	if _, err := writeInline(path, data); err != nil {
		return
	}
	q.mu.Lock()
	if e := q.entries[key]; e != nil {
		e.preview = path
	}
	q.mu.Unlock()
}

func writeInline(path string, data []byte) (int64, error) {
	if int64(len(data)) > maxMedia {
		return 0, &mediaError{Class: "too_large"}
	}
	temporary := path + ".part"
	if err := os.WriteFile(temporary, data, 0o600); err != nil {
		return 0, &mediaError{Class: "io", Err: err}
	}
	if err := os.Rename(temporary, path); err != nil {
		os.Remove(temporary)
		return 0, &mediaError{Class: "io", Err: err}
	}
	return int64(len(data)), nil
}

func (q *mediaQueue) failed(key string, part mediaPart, failure *mediaError) {
	q.mu.Lock()
	e := q.entries[key]
	if e == nil {
		q.mu.Unlock()
		return
	}
	e.running = false
	e.attempt++
	e.class = failure.Class
	e.tried = part.media.GetMediaID()
	// A file Google no longer holds is final at once: no other attempt can
	// help, and the phone is never asked to send it again.
	final := failure.Class == "too_large" || failure.Class == "phone_manual_download" || failure.Class == "gone" ||
		failure.Class == "no_full_size" || e.attempt >= mediaRunAttempts
	if final {
		e.state = "failed"
	} else {
		e.state = "pending"
		// A phone-side failure is retried by re-reading the message for fresh
		// media ids. That is listMessages, a read: it cannot deliver anything.
		// Waiting for the phone used to be excluded here, which left an
		// incoming picture with nothing at all to move it forward.
		e.refresh = failure.phoneSide()
		delay := q.jitter(mediaBackoff[min(e.attempt-1, len(mediaBackoff)-1)])
		e.timer = q.after(delay, func() {
			q.mu.Lock()
			if current := q.entries[key]; current == e && e.timer != nil {
				e.timer = nil
				q.enqueueLocked(key)
			}
			q.mu.Unlock()
		})
	}
	wantPreview := (failure.phoneSide() || failure.Class == "too_large") && e.preview == "" && !e.previewTried &&
		part.media.GetThumbnailMediaID() != ""
	if wantPreview {
		e.previewTried = true // once per run: an expired file's thumbnail has expired with it
	}
	attempt := e.attempt
	q.mu.Unlock()

	q.log.Warn().Str("message_id", part.message).Str("part", part.id()).Str("mime", mimeOfPart(part)).
		Int64("size", part.media.GetSize()).Int("attempt", attempt).Str("class", failure.Class).Int("http_status", failure.Status).
		Int64("content_length", failure.Length).Int64("bytes_received", failure.Bytes).Bool("final", final).
		Bool("has_thumbnail", part.media.GetThumbnailMediaID() != "").Str("error", errorText(failure.Err)).
		Msg("media download failed")
	if wantPreview {
		q.fetchPreview(key, part)
	}
	q.mu.Lock()
	if current := q.entries[key]; current == e {
		data := q.describeLocked(e)
		q.mu.Unlock()
		q.emit("media", data)
		return
	}
	q.mu.Unlock()
}

func mimeOfPart(p mediaPart) string {
	if p.media == nil {
		return ""
	}
	return mimeOf(p.media)
}

func errorText(err error) string {
	if err == nil {
		return ""
	}
	text := err.Error()
	if len(text) > 160 {
		text = text[:160]
	}
	return text
}

// fetchPreview downloads a part's thumbnail next to where its file would be.
func (q *mediaQueue) fetchPreview(key string, part mediaPart) {
	media := part.media
	if media.GetThumbnailMediaID() == "" || !q.net.ready() {
		return
	}
	base := strings.TrimSuffix(q.path(part), filepath.Ext(q.path(part))) + "-preview"
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	size, err := q.net.download(ctx, media.GetThumbnailMediaID(), media.GetThumbnailDecryptionKey(), base, previewLimit)
	if err != nil {
		failure := asMediaError(err)
		q.log.Warn().Str("message_id", part.message).Str("part", part.id()).Str("class", failure.Class).
			Int("http_status", failure.Status).Str("error", errorText(failure.Err)).Msg("thumbnail download failed")
		return
	}
	head := make([]byte, 512)
	if file, err := os.Open(base); err == nil {
		n, _ := io.ReadFull(file, head)
		file.Close()
		head = head[:n]
	}
	path := base + extensionFor(http.DetectContentType(head))
	if path != base {
		if err := os.Rename(base, path); err != nil {
			return
		}
	}
	q.mu.Lock()
	if e := q.entries[key]; e != nil {
		e.preview = path
	}
	q.mu.Unlock()
	q.log.Debug().Str("message_id", part.message).Str("part", part.id()).Int64("size", size).Msg("thumbnail downloaded")
}

// refresh re-reads a message for its current media ids.
func (q *mediaQueue) refresh(part mediaPart) *mediaPart {
	var cursor *gmproto.Cursor
	for page := 0; page < refreshMaxPages; page++ {
		ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
		resp, err := q.net.listMessages(ctx, part.conversation, cursor)
		cancel()
		if err != nil {
			q.log.Warn().Str("message_id", part.message).Str("error", errorText(err)).Msg("re-reading a message for fresh media failed")
			return nil
		}
		for _, msg := range resp.GetMessages() {
			if msg.GetMessageID() != part.message {
				continue
			}
			fresh := part
			fresh.status = msg.GetMessageStatus().GetStatus()
			infos := msg.GetMessageInfo()
			if part.index >= 0 && part.index < len(infos) && infos[part.index].GetMediaContent() != nil {
				fresh.media = infos[part.index].GetMediaContent()
				fresh.actionID = infos[part.index].GetActionMessageID()
			}
			return &fresh
		}
		if resp.GetCursor() == nil || len(resp.GetMessages()) == 0 {
			return nil
		}
		cursor = resp.GetCursor()
	}
	return nil
}

// fetch is media.fetch: download a message's media again, or look the message
// up when this run hasn't seen it. reread marks a person's own tap, which reads
// the conversation again and gives the part a fresh set of re-reads.
//
// Nothing here asks the phone for anything. The old "force" argument, which
// helpers up to 0.8 documented as asking the phone to send the file again, is
// not read at all: only a person's send or reaction can reach the phone.
func (q *mediaQueue) fetch(conversation, message, part string, reread bool) {
	q.mu.Lock()
	known, lookup := false, false
	report := []map[string]any{}
	for key, e := range q.entries {
		if e.part.message != message || (part != "" && e.part.id() != part) {
			continue
		}
		known = true
		if reread {
			// A person asked for this one: give it a fresh set of re-reads.
			e.waits = 0
		}
		switch {
		case e.state == "done":
			if _, ok := q.doneFile(e.part); ok {
				report = append(report, q.describeLocked(e))
				continue
			}
			e.state = "pending"
			q.enqueueLocked(key)
		case e.queued || e.running:
			report = append(report, q.describeLocked(e))
		case e.part.media == nil || e.class == "waiting_for_phone" || e.class == "no_full_size" || e.class == "too_large":
			// Nothing to download yet; look again for what the phone has now.
			lookup = true
		case e.class == "gone":
			// Google no longer holds this file, so downloading the same id
			// again cannot help. A person's own tap still re-reads the
			// message once, in case the phone has uploaded it afresh.
			if !reread {
				report = append(report, q.describeLocked(e))
				continue
			}
			lookup = true
		default:
			e.attempt = 0
			q.enqueueLocked(key)
		}
	}
	if !known || lookup {
		wanted := q.lookups[conversation]
		if wanted == nil {
			wanted = map[string]bool{}
			q.lookups[conversation] = wanted
			go q.lookup(conversation)
		}
		wanted[message] = true
	}
	q.mu.Unlock()
	for _, data := range report {
		q.emit("media", data)
	}
}

// kick retries now what was waiting out a network problem.
func (q *mediaQueue) kick() {
	q.mu.Lock()
	defer q.mu.Unlock()
	for key, e := range q.entries {
		if e.timer == nil {
			continue
		}
		switch e.class {
		case "network", "not_connected", "server", "truncated", "unauthorized":
			q.enqueueLocked(key)
		case "waiting_for_phone":
			// A reconnect is the best moment to look again: the phone is back,
			// and what it uploaded while it was away is visible now.
			e.refresh = true
			q.enqueueLocked(key)
		}
	}
}

// lookup pages back through a conversation for the messages media.fetch asked about.
func (q *mediaQueue) lookup(conversation string) {
	var cursor *gmproto.Cursor
	class := "not_found"
	searched := map[string]bool{} // asked for before the first page was read
	for page := 0; page < lookupMaxPages; page++ {
		if page == 0 {
			q.mu.Lock()
			for message := range q.lookups[conversation] {
				searched[message] = true
			}
			q.mu.Unlock()
		}
		if !q.net.ready() {
			class = "not_connected"
			break
		}
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
		resp, err := q.net.listMessages(ctx, conversation, cursor)
		cancel()
		if err != nil {
			class = "not_connected"
			if errors.Is(err, libgm.ErrPhoneNotResponding) {
				class = "phone_offline"
			}
			break
		}
		for _, msg := range resp.GetMessages() {
			q.mu.Lock()
			wanted := q.lookups[conversation][msg.GetMessageID()]
			if wanted {
				delete(q.lookups[conversation], msg.GetMessageID())
			}
			q.mu.Unlock()
			if wanted {
				q.found(msg)
			}
		}
		q.mu.Lock()
		remaining := len(q.lookups[conversation])
		q.mu.Unlock()
		if remaining == 0 {
			break
		}
		if resp.GetCursor() == nil || len(resp.GetMessages()) == 0 {
			break
		}
		cursor = resp.GetCursor()
	}
	q.mu.Lock()
	missing := map[string]bool{}
	later := map[string]bool{}
	for message := range q.lookups[conversation] {
		if searched[message] || class != "not_found" {
			missing[message] = true
		} else {
			later[message] = true // asked for while the search was under way; search again
		}
	}
	if len(later) > 0 {
		q.lookups[conversation] = later
		go q.lookup(conversation)
	} else {
		delete(q.lookups, conversation)
	}
	q.mu.Unlock()
	for message := range missing {
		q.log.Warn().Str("message_id", message).Str("class", class).Msg("media lookup did not find the message")
		q.emit("media", map[string]any{"conversation": conversation, "message": message, "state": "failed",
			"error": class, "retryable": class != "not_found"})
	}
}

// ── Google Messages as the queue's network ──────────────────────────────────

type libgmNetwork struct {
	h  *helper
	dl *downloader
}

func newLibgmNetwork(h *helper, client *http.Client) *libgmNetwork {
	n := &libgmNetwork{h: h}
	n.dl = &downloader{client: client, url: util.UploadMediaURL, auth: func() *gmproto.AuthMessage {
		c := h.currentClient()
		if c == nil || c.AuthData == nil || len(c.AuthData.TachyonAuthToken) == 0 {
			return nil
		}
		return &gmproto.AuthMessage{TachyonAuthToken: bytes.Clone(c.AuthData.TachyonAuthToken), Network: c.AuthData.AuthNetwork(),
			ConfigVersion: util.ConfigMessage}
	}}
	return n
}

func (n *libgmNetwork) ready() bool { return n.dl.auth() != nil }

func (n *libgmNetwork) download(ctx context.Context, mediaID string, key []byte, dst string, limit int64) (int64, error) {
	return n.dl.download(ctx, mediaID, key, dst, limit)
}

func (n *libgmNetwork) listMessages(ctx context.Context, conversation string, cursor *gmproto.Cursor) (*gmproto.ListMessagesResponse, error) {
	client := n.h.currentClient()
	if client == nil {
		return nil, errors.New("not signed in")
	}
	return client.FetchMessages(ctx, conversation, lookupPageSize, cursor)
}
