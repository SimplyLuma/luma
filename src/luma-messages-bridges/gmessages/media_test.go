// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/rs/zerolog"

	"go.mau.fi/mautrix-gmessages/pkg/libgm/crypto"
	"go.mau.fi/mautrix-gmessages/pkg/libgm/gmproto"
)

var testKey = bytes.Repeat([]byte{7}, 32)

func encrypt(t *testing.T, key, plain []byte) []byte {
	t.Helper()
	helper, err := crypto.NewAESGCMHelper(key)
	if err != nil {
		t.Fatal(err)
	}
	data, err := helper.EncryptData(plain)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func testDownloader(url string) *downloader {
	return &downloader{client: &http.Client{Timeout: 5 * time.Second}, url: url,
		auth: func() *gmproto.AuthMessage { return &gmproto.AuthMessage{} }}
}

func TestDownloadClassifiesWhatTheServerSends(t *testing.T) {
	plain := bytes.Repeat([]byte("picture"), 20000) // several chunks
	encrypted := encrypt(t, testKey, plain)
	cases := []struct {
		name    string
		handler http.HandlerFunc
		key     []byte
		limit   int64
		class   string
	}{
		{"a whole file", func(w http.ResponseWriter, r *http.Request) { w.Write(encrypted) }, testKey, maxMedia, ""},
		{"gone from the server", func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(404) }, testKey, maxMedia, "expired"},
		{"an expired token", func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(401) }, testKey, maxMedia, "unauthorized"},
		{"a server hiccup", func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(503) }, testKey, maxMedia, "server"},
		// What the journal showed as "download stopped after 0 bytes: unexpected EOF":
		// the phone uploaded an empty file, whose encrypted form is a two-byte header.
		// Recorded on a real account (2026-09-16), for every attachment older than
		// about six months: 200 OK, Content-Length 2, the encrypted-object header alone.
		{"a file Google no longer holds", func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Length", "2")
			w.Write(encrypt(t, testKey, nil))
		}, testKey, maxMedia, "gone"},
		{"the same, without a length", func(w http.ResponseWriter, r *http.Request) { w.Write(encrypt(t, testKey, nil)) }, testKey, maxMedia, "gone"},
		{"a two-byte error body", func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("{}")) }, testKey, maxMedia, "empty"},
		{"no body at all", func(w http.ResponseWriter, r *http.Request) {}, testKey, maxMedia, "empty"},
		{"a cut-off transfer", func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Length", strconv.Itoa(len(encrypted)))
			w.WriteHeader(200)
			w.Write(encrypted[:len(encrypted)/2])
			w.(http.Flusher).Flush()
			conn, _, _ := w.(http.Hijacker).Hijack()
			conn.Close()
		}, testKey, maxMedia, "truncated"},
		{"the wrong key", func(w http.ResponseWriter, r *http.Request) { w.Write(encrypted) }, bytes.Repeat([]byte{9}, 32), maxMedia, "decrypt"},
		{"no key", func(w http.ResponseWriter, r *http.Request) { w.Write(encrypted) }, nil, maxMedia, "decrypt"},
		{"too large", func(w http.ResponseWriter, r *http.Request) { w.Write(encrypted) }, testKey, 1000, "too_large"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			server := httptest.NewServer(c.handler)
			defer server.Close()
			dir := t.TempDir()
			dst := filepath.Join(dir, "file")
			size, err := testDownloader(server.URL).download(context.Background(), "media", c.key, dst, c.limit)
			if c.class == "" {
				if err != nil {
					t.Fatalf("download failed: %v", err)
				}
				got, _ := os.ReadFile(dst)
				if size != int64(len(plain)) || !bytes.Equal(got, plain) {
					t.Fatalf("got %d bytes, want %d", size, len(plain))
				}
				return
			}
			var failure *mediaError
			if !errors.As(err, &failure) || failure.Class != c.class {
				t.Fatalf("got %v, want class %s", err, c.class)
			}
			if entries, _ := os.ReadDir(dir); len(entries) != 0 {
				t.Fatalf("left files behind: %v", entries)
			}
		})
	}
}

func TestDownloadWithNoServerIsANetworkError(t *testing.T) {
	listener, _ := net.Listen("tcp", "127.0.0.1:0")
	address := listener.Addr().String()
	listener.Close()
	_, err := testDownloader("http://"+address).download(context.Background(), "media", testKey, filepath.Join(t.TempDir(), "f"), maxMedia)
	if failure := asMediaError(err); failure.Class != "network" {
		t.Fatalf("got %v", err)
	}
}

// ── Queue ──

type fakeNet struct {
	mu        sync.Mutex
	offline   bool
	failures  map[string][]string // media id -> classes of the next attempts
	always    map[string]string   // media id -> class of every attempt
	calls     map[string]int
	gate      chan struct{}
	active    int
	maxActive int
	pages     map[string][][]*gmproto.Message
	lists     int
}

func newFakeNet() *fakeNet {
	return &fakeNet{failures: map[string][]string{}, always: map[string]string{}, calls: map[string]int{}, pages: map[string][][]*gmproto.Message{}}
}

func (f *fakeNet) ready() bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return !f.offline
}

func (f *fakeNet) download(ctx context.Context, mediaID string, key []byte, dst string, limit int64) (int64, error) {
	f.mu.Lock()
	f.calls[mediaID]++
	f.active++
	if f.active > f.maxActive {
		f.maxActive = f.active
	}
	gate := f.gate
	class := f.always[mediaID]
	if class == "" && len(f.failures[mediaID]) > 0 {
		class = f.failures[mediaID][0]
		f.failures[mediaID] = f.failures[mediaID][1:]
	}
	f.mu.Unlock()
	defer func() { f.mu.Lock(); f.active--; f.mu.Unlock() }()
	if gate != nil {
		<-gate
	}
	if class != "" {
		return 0, &mediaError{Class: class, Err: errors.New("scripted")}
	}
	data := []byte("\xff\xd8\xff contents of " + mediaID)
	return int64(len(data)), os.WriteFile(dst, data, 0o600)
}

func (f *fakeNet) listMessages(ctx context.Context, conversation string, cursor *gmproto.Cursor) (*gmproto.ListMessagesResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.lists++
	pages := f.pages[conversation]
	page := 0
	if cursor != nil {
		page, _ = strconv.Atoi(cursor.GetLastItemID())
	}
	if page >= len(pages) {
		return &gmproto.ListMessagesResponse{}, nil
	}
	resp := &gmproto.ListMessagesResponse{Messages: pages[page]}
	if page+1 < len(pages) {
		resp.Cursor = &gmproto.Cursor{LastItemID: strconv.Itoa(page + 1)}
	}
	return resp, nil
}

type harness struct {
	t      *testing.T
	q      *mediaQueue
	net    *fakeNet
	events chan map[string]any
	found  chan *gmproto.Message
}

func newHarness(t *testing.T) *harness {
	h := &harness{t: t, net: newFakeNet(), events: make(chan map[string]any, 256), found: make(chan *gmproto.Message, 16)}
	h.q = newMediaQueue(t.TempDir(), zerolog.Nop(), h.net, func(name string, data any) {
		if name == "media" {
			h.events <- data.(map[string]any)
		}
	}, func(msg *gmproto.Message) { h.found <- msg })
	h.q.after = func(d time.Duration, f func()) *time.Timer { return time.AfterFunc(d, f) }
	h.q.jitter = func(time.Duration) time.Duration { return 5 * time.Millisecond }
	t.Cleanup(h.q.close)
	return h
}

func (h *harness) next(state string) map[string]any {
	h.t.Helper()
	deadline := time.After(5 * time.Second)
	for {
		select {
		case event := <-h.events:
			if event["state"] == state {
				return event
			}
		case <-deadline:
			h.t.Fatalf("no %s media event", state)
		}
	}
}

func photo(id, mediaID string) *gmproto.Message {
	return &gmproto.Message{MessageID: id, ConversationID: "conv", MessageStatus: &gmproto.MessageStatus{Status: gmproto.MessageStatusType_INCOMING_COMPLETE},
		MessageInfo: []*gmproto.MessageInfo{{ActionMessageID: strptr("action-" + id), Data: &gmproto.MessageInfo_MediaContent{MediaContent: &gmproto.MediaContent{
			MediaID: mediaID, MediaName: "IMG_" + id + ".jpg", Size: 40, MimeType: "image/jpeg", DecryptionKey: testKey}}}}}
}

func strptr(value string) *string { return &value }

func (h *harness) receive(msg *gmproto.Message) []any {
	descriptors, start := h.q.observe(msg)
	start()
	return descriptors
}

func TestAnUnexpectedEOFIsRetriedUntilThePictureArrives(t *testing.T) {
	h := newHarness(t)
	h.net.failures["m1"] = []string{"truncated", "network"}
	descriptors := h.receive(photo("1", "m1"))
	if descriptors[0].(map[string]any)["state"] != "downloading" {
		t.Fatalf("a new picture starts downloading: %v", descriptors)
	}
	pending := h.next("pending")
	if pending["error"] != "truncated" || pending["attempt"] != 1 {
		t.Fatalf("the first failure is reported with its class: %v", pending)
	}
	done := h.next("done")
	if _, err := os.Stat(done["path"].(string)); err != nil || h.net.calls["m1"] != 3 {
		t.Fatalf("done after three attempts: %v calls=%d", done, h.net.calls["m1"])
	}
}

func TestAnExpiredFileUsesFreshMediaIDsWithoutAskingThePhone(t *testing.T) {
	h := newHarness(t)
	h.q.jitter = func(time.Duration) time.Duration { return 50 * time.Millisecond }
	h.net.always["old"] = "expired"
	msg := photo("2", "old")
	msg.MessageInfo[0].GetMediaContent().ThumbnailMediaID = "thumb"
	msg.MessageInfo[0].GetMediaContent().ThumbnailDecryptionKey = testKey
	h.receive(msg)
	pending := h.next("pending")
	if pending["error"] != "expired" || pending["preview"] == nil {
		t.Fatalf("an expired file shows its thumbnail meanwhile: %v", pending)
	}
	// Re-reading the message (a read-only list call) finds a newer media id.
	h.net.mu.Lock()
	h.net.pages["conv"] = [][]*gmproto.Message{{photo("2", "new")}}
	h.net.mu.Unlock()
	done := h.next("done")
	if h.net.calls["new"] != 1 || done["preview"] == nil {
		t.Fatalf("downloaded under the fresh id: %v calls=%v", done, h.net.calls)
	}
}

func TestDownloadsRunAtMostThreeAtATime(t *testing.T) {
	h := newHarness(t)
	h.net.gate = make(chan struct{})
	for i := 0; i < 10; i++ {
		h.receive(photo(fmt.Sprint(i), fmt.Sprint("m", i)))
	}
	time.Sleep(100 * time.Millisecond)
	h.net.mu.Lock()
	active := h.net.active
	h.net.mu.Unlock()
	if active != mediaWorkers {
		t.Fatalf("%d downloads at once, want %d", active, mediaWorkers)
	}
	close(h.net.gate)
	for i := 0; i < 10; i++ {
		h.next("done")
	}
	if h.net.maxActive != mediaWorkers {
		t.Fatalf("at most %d at once, saw %d", mediaWorkers, h.net.maxActive)
	}
}

func TestReconnectingRetriesNetworkFailuresAtOnce(t *testing.T) {
	h := newHarness(t)
	h.q.jitter = func(time.Duration) time.Duration { return time.Hour }
	h.net.failures["m3"] = []string{"network"}
	h.receive(photo("3", "m3"))
	h.next("pending")
	h.q.kick()
	h.next("done")
}

func TestOfflineAttemptsEndFailedButRetryable(t *testing.T) {
	h := newHarness(t)
	h.net.offline = true
	h.receive(photo("4", "m4"))
	failed := h.next("failed")
	if failed["error"] != "not_connected" || failed["retryable"] != true || failed["attempt"] != mediaRunAttempts {
		t.Fatalf("got %v", failed)
	}
	// Messages asks again once the account is back.
	h.net.mu.Lock()
	h.net.offline = false
	h.net.mu.Unlock()
	h.q.fetch("conv", "4", "", false)
	h.next("done")
}

func TestAPictureStillUploadingWaitsForItsUpdate(t *testing.T) {
	h := newHarness(t)
	h.receive(photo("5", ""))
	descriptors := h.receive(photo("5", ""))
	d := descriptors[0].(map[string]any)
	if d["state"] != "pending" || d["error"] != "waiting_for_phone" || len(h.net.calls) != 0 {
		t.Fatalf("nothing to download yet: %v", d)
	}
	h.receive(photo("5", "m5"))
	h.next("done")
}

func TestAnMMSThePhoneIsFetchingIsAPlaceholderUntilItsParts(t *testing.T) {
	h := newHarness(t)
	msg := &gmproto.Message{MessageID: "6", ConversationID: "conv",
		MessageStatus: &gmproto.MessageStatus{Status: gmproto.MessageStatusType_INCOMING_AUTO_DOWNLOADING}}
	d := h.receive(msg)[0].(map[string]any)
	if d["part"] != mmsPart || d["state"] != "pending" || d["error"] != "waiting_for_phone" {
		t.Fatalf("got %v", d)
	}
	d = h.receive(photo("6", "m6"))[0].(map[string]any)
	if d["part"] != "0" || h.q.entries[entryKey("6", mmsPart)] != nil {
		t.Fatalf("the placeholder gives way: %v", d)
	}
	h.next("done")
}

func TestFilesAlreadyDownloadedAreDone(t *testing.T) {
	h := newHarness(t)
	msg := photo("7", "m7")
	part := mediaPart{conversation: "conv", message: "7", index: 0, media: msg.MessageInfo[0].GetMediaContent()}
	os.MkdirAll(h.q.dir, 0o700)
	os.WriteFile(h.q.path(part), []byte("jpeg"), 0o600)
	d := h.receive(msg)[0].(map[string]any)
	if d["state"] != "done" || d["path"] != h.q.path(part) || len(h.net.calls) != 0 {
		t.Fatalf("got %v", d)
	}
	// An empty file from an interrupted earlier download doesn't count.
	msg = photo("8", "m8")
	part = mediaPart{conversation: "conv", message: "8", index: 0, media: msg.MessageInfo[0].GetMediaContent()}
	os.WriteFile(h.q.path(part), nil, 0o600)
	if d := h.receive(msg)[0].(map[string]any); d["state"] != "downloading" {
		t.Fatalf("got %v", d)
	}
	h.next("done")
}

func TestMediaFetchLooksBackForMessagesThisRunHasNotSeen(t *testing.T) {
	h := newHarness(t)
	h.net.pages["conv"] = [][]*gmproto.Message{{photo("20", "m20")}, {photo("10", "m10")}}
	h.q.fetch("conv", "10", "", true)
	select {
	case msg := <-h.found:
		if msg.GetMessageID() != "10" {
			t.Fatalf("found %s", msg.GetMessageID())
		}
	case <-time.After(5 * time.Second):
		t.Fatal("the message was not looked up")
	}
	h.q.fetch("conv", "missing", "", true)
	failed := h.next("failed")
	if failed["message"] != "missing" || failed["error"] != "not_found" || failed["retryable"] != false {
		t.Fatalf("got %v", failed)
	}
}

func TestTooLargeIsFinalAndShowsTheThumbnail(t *testing.T) {
	h := newHarness(t)
	msg := photo("9", "m9")
	media := msg.MessageInfo[0].GetMediaContent()
	media.Size, media.ThumbnailMediaID, media.ThumbnailDecryptionKey = maxMedia+1, "t9", testKey
	d := h.receive(msg)[0].(map[string]any)
	if d["state"] != "failed" || d["error"] != "too_large" {
		t.Fatalf("got %v", d)
	}
	failed := h.next("failed")
	if failed["preview"] == nil || failed["retryable"] != false || h.net.calls["m9"] != 0 {
		t.Fatalf("got %v calls=%v", failed, h.net.calls)
	}
}

// What Nick saw: every picture a kilobyte, blocky, and never replaced. A
// message first arrives carrying only inline preview bytes (or a thumbnail)
// while the phone uploads the file; that preview used to be saved as the file
// and called done, so the update with the real media id changed nothing.
func TestInlinePreviewBytesAreAPreviewNotTheFile(t *testing.T) {
	h := newHarness(t)
	msg := photo("30", "")
	media := msg.MessageInfo[0].GetMediaContent()
	media.Size, media.MediaData = 2_400_000, []byte("\xff\xd8\xff tiny blurry preview")
	d := h.receive(msg)[0].(map[string]any)
	if d["state"] != "pending" || d["error"] != "waiting_for_phone" {
		t.Fatalf("a preview is not the file: %v", d)
	}
	pending := h.next("pending")
	if pending["preview"] == nil || pending["path"] != nil {
		t.Fatalf("the inline bytes become the preview only: %v", pending)
	}
	// The phone finishes uploading: the same message arrives with its media id.
	h.receive(photo("30", "m30"))
	done := h.next("done")
	path := done["path"].(string)
	if h.net.calls["m30"] != 1 || done["preview"] == nil {
		t.Fatalf("the full file downloads under its id: %v calls=%v", done, h.net.calls)
	}
	if source, _ := os.ReadFile(path + sourceSuffix); string(source) != "m30" {
		t.Fatalf("the file records the id it came from: %q", source)
	}
}

func TestAThumbnailOnlyMessageShowsThePreviewAndWaits(t *testing.T) {
	h := newHarness(t)
	msg := photo("31", "")
	media := msg.MessageInfo[0].GetMediaContent()
	media.ThumbnailMediaID, media.ThumbnailDecryptionKey = "t31", testKey
	h.receive(msg)
	pending := h.next("pending")
	if pending["preview"] == nil || pending["error"] != "waiting_for_phone" || h.net.calls["t31"] != 1 {
		t.Fatalf("got %v calls=%v", pending, h.net.calls)
	}
	// Seen again before the upload finishes: no second request, no download.
	h.receive(photo("31", ""))
}

// Installs that already have a kilobyte preview saved as the file heal: with no
// recorded source and far smaller than the reported size, it is fetched again.
func TestALegacyPreviewSavedAsTheFileIsFetchedAgain(t *testing.T) {
	h := newHarness(t)
	msg := photo("32", "m32")
	msg.MessageInfo[0].GetMediaContent().Size = 1_800_000
	part := mediaPart{conversation: "conv", message: "32", index: 0, media: msg.MessageInfo[0].GetMediaContent()}
	os.MkdirAll(h.q.dir, 0o700)
	os.WriteFile(h.q.path(part), bytes.Repeat([]byte{1}, 900), 0o600)
	if d := h.receive(msg)[0].(map[string]any); d["state"] != "downloading" {
		t.Fatalf("a kilobyte file for a 1.8 MB photo is not done: %v", d)
	}
	h.next("done")
	if h.net.calls["m32"] != 1 {
		t.Fatalf("calls=%v", h.net.calls)
	}
	// A full file downloaded from its id stays done even if smaller than reported.
	msg = photo("33", "m33")
	msg.MessageInfo[0].GetMediaContent().Size = 1_800_000
	part = mediaPart{conversation: "conv", message: "33", index: 0, media: msg.MessageInfo[0].GetMediaContent()}
	os.WriteFile(h.q.path(part), []byte("jpeg"), 0o600)
	os.WriteFile(h.q.path(part)+sourceSuffix, []byte("m33"), 0o600)
	if d := h.receive(msg)[0].(map[string]any); d["state"] != "done" {
		t.Fatalf("got %v", d)
	}
	// A re-upload under a new id replaces it.
	msg = photo("33", "m33b")
	if d := h.receive(msg)[0].(map[string]any); d["state"] != "downloading" {
		t.Fatalf("a new media id downloads again: %v", d)
	}
	h.next("done")
}

func TestMessagesThisAccountWroteAreOutgoingWhateverTheirStatus(t *testing.T) {
	h := &helper{self: map[string]bool{}, convs: map[string]*gmproto.Conversation{}, pending: map[string]string{},
		sims: map[string]*gmproto.SIMCard{}, log: zerolog.Nop()}
	h.media = newMediaQueue(t.TempDir(), zerolog.Nop(), newFakeNet(), func(string, any) {}, func(*gmproto.Message) {})
	t.Cleanup(h.media.close)
	h.remember(&gmproto.Conversation{ConversationID: "conv", Participants: []*gmproto.Participant{
		{ID: &gmproto.SmallInfo{ParticipantID: "7"}, IsMe: true},
		{ID: &gmproto.SmallInfo{ParticipantID: "9", Number: "+15125550101"}, FullName: "Alex"}}})
	text := func(id, participant string, status gmproto.MessageStatusType) *gmproto.Message {
		return &gmproto.Message{MessageID: id, ConversationID: "conv", ParticipantID: participant,
			MessageStatus: &gmproto.MessageStatus{Status: status},
			MessageInfo:   []*gmproto.MessageInfo{{Data: &gmproto.MessageInfo_MessageContent{MessageContent: &gmproto.MessageContent{Content: "hi"}}}}}
	}
	cases := []struct {
		msg      *gmproto.Message
		outgoing bool
		state    string
	}{
		{text("a", "7", gmproto.MessageStatusType_INCOMING_COMPLETE), true, "sent"},  // sent from the phone
		{text("b", "7", gmproto.MessageStatusType_STATUS_UNKNOWN), true, "sent"},     // no status yet
		{text("c", "1", gmproto.MessageStatusType_INCOMING_DELIVERED), true, "sent"}, // libgm's own self id
		{text("d", "9", gmproto.MessageStatusType_OUTGOING_DISPLAYED), true, "read"},
		{text("e", "9", gmproto.MessageStatusType_INCOMING_COMPLETE), false, "received"},
		{text("f", "9", gmproto.MessageStatusType_INCOMING_DISPLAYED), false, "read"}, // read on the phone
	}
	for _, c := range cases {
		mapped, _ := h.message(c.msg)
		if mapped["outgoing"] != c.outgoing || mapped["state"] != c.state {
			t.Errorf("%s: outgoing=%v state=%v, want %v %v", c.msg.GetMessageID(), mapped["outgoing"], mapped["state"], c.outgoing, c.state)
		}
	}
}

func TestAFileGoogleNoLongerHoldsIsFinalAtOnce(t *testing.T) {
	h := newHarness(t)
	h.net.always["old"] = "gone"
	msg := photo("40", "old")
	msg.MessageInfo[0].GetMediaContent().ThumbnailMediaID = "old-thumb"
	msg.MessageInfo[0].GetMediaContent().ThumbnailDecryptionKey = testKey
	h.net.always["old-thumb"] = "gone"
	h.receive(msg)
	failed := h.next("failed")
	if failed["error"] != "gone" || failed["retryable"] != false {
		t.Fatalf("no longer available, and nothing can bring it back: %v", failed)
	}
	// A tap re-reads the message once (0.10), in case the phone uploaded it
	// afresh: a read of the conversation, with no download of the same id.
	h.net.mu.Lock()
	h.net.pages["conv"] = [][]*gmproto.Message{{msg}}
	h.net.mu.Unlock()
	h.q.fetch("conv", "40", "", true)
	select {
	case found := <-h.found:
		if found.GetMessageID() != "40" {
			t.Fatalf("looked up %s", found.GetMessageID())
		}
	case <-time.After(5 * time.Second):
		t.Fatal("a tap did not re-read the message")
	}
	h.net.mu.Lock()
	calls, thumbs := h.net.calls["old"], h.net.calls["old-thumb"]
	h.net.mu.Unlock()
	if calls != 1 || thumbs != 1 {
		t.Fatalf("downloads=%d thumbnail tries=%d, want 1 and 1", calls, thumbs)
	}
	// If the message ever arrives with a new media id, it downloads.
	h.receive(photo("40", "reuploaded"))
	h.next("done")
}

// photoWaitingForItsFile is an incoming picture as Google Messages first
// delivers it: a size and a thumbnail, but no full-size media id yet. This is
// what an iPhone HEIC picture looks like when it reaches the desktop.
func photoWaitingForItsFile(id string) *gmproto.Message {
	return &gmproto.Message{MessageID: id, ConversationID: "conv",
		MessageStatus: &gmproto.MessageStatus{Status: gmproto.MessageStatusType_INCOMING_COMPLETE},
		MessageInfo: []*gmproto.MessageInfo{{ActionMessageID: strptr("action-" + id), Data: &gmproto.MessageInfo_MediaContent{
			MediaContent: &gmproto.MediaContent{MediaName: "IMG_" + id + ".heic", Size: 1245192, MimeType: "image/heic",
				ThumbnailMediaID: "thumb-" + id, ThumbnailDecryptionKey: testKey, DecryptionKey: testKey}}}}}
}

// A picture whose full-size media id has not arrived used to be parked with no
// timer and no re-read, so it waited behind a spinner for a message update that
// never came. It is re-read until the id turns up.
func TestAPictureWithNoMediaIDYetIsReReadUntilItsFileArrives(t *testing.T) {
	h := newHarness(t)
	// What a re-read of the conversation sees: the message now carries its
	// full-size id. Set before the picture arrives, so no worker is reading
	// the fake network's pages while the test writes them.
	h.net.pages["conv"] = [][]*gmproto.Message{{photo("7", "full-7")}}
	descriptors := h.receive(photoWaitingForItsFile("7"))
	if got := descriptors[0].(map[string]any); got["state"] != "pending" {
		t.Fatalf("a picture with no media id starts out pending: %v", got)
	}
	done := h.next("done")
	if _, err := os.Stat(done["path"].(string)); err != nil {
		t.Fatalf("the picture finished downloading: %v (%v)", done, err)
	}
	if h.net.calls["full-7"] != 1 {
		t.Fatalf("the full-size file is downloaded once, from the re-read id: %d", h.net.calls["full-7"])
	}
	if h.net.lists == 0 {
		t.Fatal("the message was never re-read; nothing could have moved the picture forward")
	}
}

// When the id never arrives the row must not spin forever: the wait ends in a
// failure the person can retry.
func TestAPictureWhoseFileNeverArrivesEndsInAFailureNotASpinner(t *testing.T) {
	h := newHarness(t)
	h.receive(photoWaitingForItsFile("8"))
	deadline := time.After(20 * time.Second)
	for {
		select {
		case event := <-h.events:
			if event["state"] != "failed" {
				continue
			}
			if event["error"] != "no_full_size" {
				t.Fatalf("the wait ends with its own class: %v", event)
			}
			if event["retryable"] != true {
				t.Fatalf("a picture that never arrived can be retried: %v", event)
			}
			h.q.mu.Lock()
			waits := h.q.entries[entryKey("8", "0")].waits
			h.q.mu.Unlock()
			if waits != mediaWaitAttempts {
				t.Fatalf("every re-read is spent before giving up: %d", waits)
			}
			return
		case <-deadline:
			t.Fatal("a picture with no media id never stopped waiting")
		}
	}
}

// Reconnecting is the moment to look again, rather than waiting out the backoff.
func TestReconnectingReReadsAPictureThatIsStillWaiting(t *testing.T) {
	h := newHarness(t)
	h.net.pages["conv"] = [][]*gmproto.Message{{photo("9", "full-9")}}
	// The next re-read is an hour away, so only the reconnect can bring it in.
	h.q.jitter = func(time.Duration) time.Duration { return time.Hour }
	h.receive(photoWaitingForItsFile("9"))
	waitFor(t, func() bool {
		h.q.mu.Lock()
		defer h.q.mu.Unlock()
		e := h.q.entries[entryKey("9", "0")]
		return e != nil && e.timer != nil && e.class == "waiting_for_phone" && !e.running
	}, "the picture is waiting with a timer armed")
	h.q.kick()
	if done := h.next("done"); done["path"] == nil {
		t.Fatalf("the reconnect brought the picture in: %v", done)
	}
}

func waitFor(t *testing.T, ok func() bool, what string) {
	t.Helper()
	for i := 0; i < 400; i++ {
		if ok() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("timed out waiting until %s", what)
}
