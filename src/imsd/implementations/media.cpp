// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types no-char-pointer
/*
imsd-media — the RTP/AMR-WB media leg for a userspace VoLTE call, spawned as
its own process by the daemon (imsd) exactly as the Python prototype spawned
rtpcap.py: one media leg per call, argv-configured, torn down on SIGTERM or
when the downlink dries up. Keeping it a separate process preserves the
far-end-hangup contract (exit code 3 = downlink RTP stopped, which on carriers
whose network BYE never reaches our SAs is the reliable teardown trigger) and
isolates a media crash from the control-plane daemon.

Binds the advertised local RTP port, sends uplink octet-aligned AMR-WB frames
toward the media gateway, captures the downlink, and — with PLAY=1 —
reconstructs the media clock from RTP timestamps so pw-play stays real-time-
paced through far-end DTX silence (every missing 20 ms slot is decoded as an
FT-15 NO_DATA frame for CNG/PLC). MIC=1 feeds live pw-record audio through
libvo-amrwbenc; downlink decode is libopencore-amrwb. Both codecs are dlopen'd
so the binary has no link-time dependency on them.

Usage: imsd-media <local-ip> <rtp-port> <remote-ip> <remote-port> <pt> <secs> <out-base>
       imsd-media --selftest   (payload pack/depay roundtrip, both formats)
Env: MIC PLAY GAIN PLAY_GAIN AMR_MODE DTX MEDIA_TIMEOUT RTP_DUMP OCTET_ALIGN
AUDIO_USER
(OCTET_ALIGN=0 selects RFC 4867 bandwidth-efficient payloads both ways; the
daemon sets it from the negotiated SDP — 1 is the default/legacy behavior.)
*/

#include <arpa/inet.h>
#include <cerrno>
#include <dlfcn.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <pwd.h>
#include <grp.h>
#include <stdlib.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

import std;

namespace {

using Clock = std::chrono::steady_clock;
double Now() {
    return std::chrono::duration<double>(Clock::now().time_since_epoch()).count();
}

// octet-aligned AMR-WB speech-frame byte sizes by frame type (mode).
constexpr int AmrwbBytes(int ft) {
    switch (ft) {
        case 0: return 17; case 1: return 23; case 2: return 32; case 3: return 36;
        case 4: return 40; case 5: return 46; case 6: return 50; case 7: return 58;
        case 8: return 60; case 9: return 5; default: return 0;
    }
}

// AMR-WB speech bits per frame type (RFC 4867 table 2 / TS 26.201) — the
// exact payload bit counts of the bandwidth-efficient format. FT 14/15
// (SPEECH_LOST/NO_DATA) carry 0 bits; unknown FTs return -1 so a corrupt
// ToC aborts the packet instead of shifting every later bit.
constexpr int AmrwbBits(int ft) {
    switch (ft) {
        case 0: return 132; case 1: return 177; case 2: return 253; case 3: return 285;
        case 4: return 317; case 5: return 365; case 6: return 397; case 7: return 461;
        case 8: return 477; case 9: return 40; case 14: return 0; case 15: return 0;
        default: return -1;
    }
}

std::string EnvOr(const char* k, const char* d) {
    const char* v = std::getenv(k);
    return v ? std::string(v) : std::string(d);
}
bool EnvBool(const char* k, bool d) {
    const char* v = std::getenv(k);
    return v ? std::string_view(v) == "1" : d;
}

bool Is6(std::string_view a) { return a.contains(':'); }

// Fill a sockaddr_storage from an IP literal + port; returns its length.
socklen_t MakeAddr(std::string_view ip, int port, sockaddr_storage& ss) {
    std::memset(&ss, 0, sizeof ss);
    std::string s(ip);
    if (Is6(ip)) {
        auto* a = reinterpret_cast<sockaddr_in6*>(&ss);
        a->sin6_family = AF_INET6;
        a->sin6_port = htons(static_cast<uint16_t>(port));
        inet_pton(AF_INET6, s.c_str(), &a->sin6_addr);
        return sizeof(sockaddr_in6);
    }
    auto* a = reinterpret_cast<sockaddr_in*>(&ss);
    a->sin_family = AF_INET;
    a->sin_port = htons(static_cast<uint16_t>(port));
    inet_pton(AF_INET, s.c_str(), &a->sin_addr);
    return sizeof(sockaddr_in);
}

// ---- AMR-WB codecs via dlopen (vo-amrwbenc E_IF_*, opencore-amrwb D_IF_*) --

class Encoder {
public:
    bool Open() {
        lib_ = dlopen("libvo-amrwbenc.so.0", RTLD_NOW);
        if (!lib_) return false;
        init_ = reinterpret_cast<InitFn>(dlsym(lib_, "E_IF_init"));
        enc_ = reinterpret_cast<EncFn>(dlsym(lib_, "E_IF_encode"));
        exit_ = reinterpret_cast<ExitFn>(dlsym(lib_, "E_IF_exit"));
        if (!init_ || !enc_ || !exit_) return false;
        st_ = init_();
        return st_ != nullptr;
    }
    // 320 int16 samples -> one RFC 3267 storage frame (header byte + speech).
    std::vector<std::uint8_t> Encode(std::int16_t* samples, int mode, int dtx) {
        std::uint8_t out[128];
        int n = enc_(st_, static_cast<std::int16_t>(mode), samples, out, static_cast<std::int16_t>(dtx));
        if (n <= 0) return {};
        return std::vector<std::uint8_t>(out, out + n);
    }
    ~Encoder() { if (st_ && exit_) exit_(st_); if (lib_) dlclose(lib_); }
private:
    using InitFn = void* (*)();
    using EncFn = int (*)(void*, std::int16_t, std::int16_t*, std::uint8_t*, std::int16_t);
    using ExitFn = void (*)(void*);
    void* lib_ = nullptr; void* st_ = nullptr;
    InitFn init_ = nullptr; EncFn enc_ = nullptr; ExitFn exit_ = nullptr;
};

class Decoder {
public:
    bool Open() {
        lib_ = dlopen("libopencore-amrwb.so.0", RTLD_NOW);
        if (!lib_) return false;
        init_ = reinterpret_cast<InitFn>(dlsym(lib_, "D_IF_init"));
        dec_ = reinterpret_cast<DecFn>(dlsym(lib_, "D_IF_decode"));
        exit_ = reinterpret_cast<ExitFn>(dlsym(lib_, "D_IF_exit"));
        if (!init_ || !dec_ || !exit_) return false;
        st_ = init_();
        return st_ != nullptr;
    }
    // one storage frame ([header][speech]) -> 640 bytes of 16 kHz s16 PCM.
    std::array<std::int16_t, 320> Decode(const std::uint8_t* frame, int len) {
        std::array<std::int16_t, 320> out{};
        std::vector<std::uint8_t> in(frame, frame + len);
        dec_(st_, in.data(), out.data(), 0);
        return out;
    }
    ~Decoder() { if (st_ && exit_) exit_(st_); if (lib_) dlclose(lib_); }
private:
    using InitFn = void* (*)();
    using DecFn = void (*)(void*, std::uint8_t*, std::int16_t*, int);
    using ExitFn = void (*)(void*);
    void* lib_ = nullptr; void* st_ = nullptr;
    InitFn init_ = nullptr; DecFn dec_ = nullptr; ExitFn exit_ = nullptr;
};

// MSB-first bit cursor over an RTP payload (bandwidth-efficient AMR-WB is
// bit-packed: 4-bit CMR, 6-bit ToC entries, then the speech bits back to
// back with only the final octet padded — RFC 4867 §4.3).
struct BitReader {
    std::span<const std::uint8_t> d;
    std::size_t pos = 0;
    bool Ok(std::size_t n) const { return pos + n <= d.size() * 8; }
    std::uint32_t Take(int n) {
        std::uint32_t v = 0;
        for (int i = 0; i < n; i++, pos++)
            v = (v << 1) | ((d[pos >> 3] >> (7 - (pos & 7))) & 1);
        return v;
    }
};

// bandwidth-efficient AMR-WB de-payload. Same contract as DepayOctet:
// [(storage-header-byte, speech-bytes)...], speech re-aligned to octets.
std::vector<std::pair<std::uint8_t, std::vector<std::uint8_t>>>
DepayBe(std::span<const std::uint8_t> pl) {
    std::vector<std::pair<std::uint8_t, std::vector<std::uint8_t>>> out;
    BitReader br{pl};
    if (!br.Ok(4)) return out;
    br.Take(4);  // CMR
    struct Toc { int ft; int q; };
    std::vector<Toc> tocs;
    for (;;) {
        if (!br.Ok(6)) return out;
        int f = static_cast<int>(br.Take(1));
        int ft = static_cast<int>(br.Take(4));
        int q = static_cast<int>(br.Take(1));
        tocs.push_back({ft, q});
        if (!f) break;
    }
    for (auto [ft, q] : tocs) {
        int bits = AmrwbBits(ft);
        if (bits < 0 || !br.Ok(static_cast<std::size_t>(bits))) break;
        std::vector<std::uint8_t> speech(AmrwbBytes(ft), 0);
        for (int i = 0; i < bits; i++)
            if (br.Take(1)) speech[i >> 3] |= 0x80 >> (i & 7);
        out.emplace_back(static_cast<std::uint8_t>((ft << 3) | (q ? 0x04 : 0)), std::move(speech));
    }
    return out;
}

// storage-format frame (header byte + octet-aligned speech) -> RTP payload.
// Octet-aligned: CMR byte + the frame verbatim (the storage header doubles
// as a ToC byte with F=0). Bandwidth-efficient: 10 header bits + exactly
// AmrwbBits(ft) speech bits, final octet zero-padded.
std::vector<std::uint8_t> PayloadFromFrame(std::span<const std::uint8_t> frame, bool octetAlign) {
    if (octetAlign) {
        std::vector<std::uint8_t> pl = {0xF0};
        pl.insert(pl.end(), frame.begin(), frame.end());
        return pl;
    }
    int ft = (frame[0] >> 3) & 0x0F;
    int q = (frame[0] >> 2) & 1;
    int bits = AmrwbBits(ft);
    if (bits < 0) bits = 0;
    std::vector<std::uint8_t> pl((10 + bits + 7) / 8, 0);
    auto put = [&](int pos, int n, std::uint32_t v) {
        for (int i = 0; i < n; i++)
            if ((v >> (n - 1 - i)) & 1) pl[(pos + i) >> 3] |= 0x80 >> ((pos + i) & 7);
    };
    put(0, 4, 15);  // CMR: no mode request
    put(4, 1, 0);   // F: single frame
    put(5, 4, static_cast<std::uint32_t>(ft));
    put(9, 1, static_cast<std::uint32_t>(q));
    for (int i = 0; i < bits; i++)
        if (frame[1 + (i >> 3)] & (0x80 >> (i & 7))) pl[(10 + i) >> 3] |= 0x80 >> ((10 + i) & 7);
    return pl;
}

// octet-aligned AMR-WB de-payload: skip CMR, read ToC bytes until F=0, then
// the speech runs. Returns [(storage-header-byte, speech-bytes)...].
std::vector<std::pair<std::uint8_t, std::vector<std::uint8_t>>>
DepayOctet(std::span<const std::uint8_t> pl) {
    std::vector<std::pair<std::uint8_t, std::vector<std::uint8_t>>> out;
    if (pl.empty()) return out;
    std::size_t i = 1;  // skip CMR
    std::vector<std::uint8_t> tocs;
    while (i < pl.size()) {
        std::uint8_t toc = pl[i++];
        tocs.push_back(toc);
        if (!(toc & 0x80)) break;
    }
    for (std::uint8_t toc : tocs) {
        int ft = (toc >> 3) & 0x0F;
        int n = AmrwbBytes(ft);
        std::vector<std::uint8_t> speech;
        if (n > 0 && i + n <= pl.size()) speech.assign(pl.begin() + i, pl.begin() + i + n);
        out.emplace_back(static_cast<std::uint8_t>(toc & 0x7C), std::move(speech));
        i += n;
    }
    return out;
}

std::size_t RtpPayloadOffset(std::span<const std::uint8_t> pkt) {
    int cc = pkt[0] & 0x0F;
    int ext = (pkt[0] >> 4) & 1;
    std::size_t off = 12 + cc * 4;
    if (ext && off + 4 <= pkt.size()) {
        std::uint16_t extlen = (pkt[off + 2] << 8) | pkt[off + 3];
        off += 4 + extlen * 4;
    }
    return off;
}

// Spawn pw-record/pw-play in the desktop user's PipeWire session (sudo -u
// when run as root). AUDIO_USER names the session owner; the default "user"
// is postmarketOS's standard account. `toChild` true = we write the child's
// stdin (pw-play); false = we read its stdout (pw-record). Returns {pid, fd}.
struct Child { pid_t pid = -1; int fd = -1; };
Child SpawnPw(bool play, bool toChild) {
    int pipefd[2];
    if (pipe(pipefd) != 0) return {};
    // Drop to the session owner in the forked child rather than exec'ing
    // sudo. sudo puts the real command in a process group of its own, so the
    // call-teardown kill(-pgid) reached only the sudo launcher and left one
    // pw-record alive per call (five were found orphaned after an afternoon of
    // testing, all still holding the capture device).
    uid_t audioUid = 0;
    gid_t audioGid = 0;
    std::string audioUser;
    if (geteuid() == 0) {
        audioUser = EnvOr("AUDIO_USER", "user");
        if (passwd* pw = getpwnam(audioUser.c_str())) {
            audioUid = pw->pw_uid;
            audioGid = pw->pw_gid;
        }
    }
    std::vector<std::string> argv;
    std::string micFile = EnvOr("MIC_FILE", "");
    if (!play && !micFile.empty()) {
        // Deterministic call-quality fixture: decode and loop the supplied
        // track directly to 16 kHz mono PCM. Normal calls leave MIC_FILE
        // unset and continue to use the hardware microphone below.
        for (const char* a : {"ffmpeg", "-hide_banner", "-loglevel", "error",
                              "-re", "-stream_loop", "-1", "-i"})
            argv.emplace_back(a);
        argv.push_back(micFile);
        for (const char* a : {"-f", "s16le", "-acodec", "pcm_s16le", "-ar",
                              "16000", "-ac", "1", "pipe:1"})
            argv.emplace_back(a);
    } else {
        const char* tool = play ? "pw-play" : "pw-record";
        const char* lat = play ? "40ms" : "20ms";
        // media.role=Phone routes call audio through the session's phone role
        // instead of the default "Music" role. On a role-based mobile audio
        // setup those are separate loopback sinks, and the multimedia one was
        // found muted -- which silently discarded every call's downlink while
        // every counter still reported success. Call audio must not depend on
        // the state of the media role.
        for (const char* a : {tool, "--media-role", "Phone", "--raw", "--rate",
                              "16000", "--channels", "1", "--format", "s16",
                              "--latency", lat, "-"})
            argv.emplace_back(a);
    }
    pid_t pid = fork();
    if (pid == 0) {
        // `sudo`/ffmpeg/PipeWire may add another process below this launcher.
        // Give the complete media helper tree its own process group so call
        // teardown cannot orphan a pw-play or pw-record child.
        (void)setpgid(0, 0);
        if (audioUid != 0) {
            std::string rt = std::format("/run/user/{}", audioUid);
            setenv("XDG_RUNTIME_DIR", rt.c_str(), 1);
            if (!audioUser.empty()) (void)initgroups(audioUser.c_str(), audioGid);
            if (setgid(audioGid) != 0) _exit(126);
            if (setuid(audioUid) != 0) _exit(126);
        }
        if (toChild) { dup2(pipefd[0], STDIN_FILENO); }
        else { dup2(pipefd[1], STDOUT_FILENO); }
        close(pipefd[0]); close(pipefd[1]);
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0) { dup2(devnull, STDERR_FILENO); close(devnull); }
        std::vector<char*> cargv;
        for (auto& s : argv) cargv.push_back(const_cast<char*>(s.c_str()));
        cargv.push_back(nullptr);
        execvp(cargv[0], cargv.data());
        _exit(127);
    }
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return {}; }
    (void)setpgid(pid, pid);  // close the parent/child setpgid race
    if (toChild) { close(pipefd[0]); return {pid, pipefd[1]}; }
    close(pipefd[1]);
    return {pid, pipefd[0]};
}

void StopChildGroup(Child& child) {
    if (child.fd >= 0) { close(child.fd); child.fd = -1; }
    if (child.pid < 0) return;
    // Prefer a graceful group stop. Fall back to the launcher PID if group
    // creation raced with exec or was rejected, then bound shutdown before a
    // final group kill. Only this call-owned process group is targeted.
    if (kill(-child.pid, SIGTERM) != 0) (void)kill(child.pid, SIGTERM);
    int status = 0;
    bool reaped = false;
    for (int i = 0; i < 20; i++) {
        pid_t r = waitpid(child.pid, &status, WNOHANG);
        if (r == child.pid || (r < 0 && errno == ECHILD)) { reaped = true; break; }
        usleep(50000);
    }
    if (!reaped) {
        if (kill(-child.pid, SIGKILL) != 0) (void)kill(child.pid, SIGKILL);
        (void)waitpid(child.pid, &status, 0);
    }
    child.pid = -1;
}

bool ReadExact(int fd, std::uint8_t* buf, std::size_t n) {
    std::size_t got = 0;
    while (got < n) {
        ssize_t r = read(fd, buf + got, n - got);
        if (r <= 0) return false;
        got += static_cast<std::size_t>(r);
    }
    return true;
}

// ---- shared RTP tx state (main + mic threads both send) -------------------
struct TxState {
    std::mutex lock;
    int pt = 0;
    std::uint32_t ssrc = 0x5EED1234;
    std::uint32_t seq = 1000;
    std::uint32_t ts = 160000;
    sockaddr_storage dst{}; socklen_t dstLen = 0;
    sockaddr_storage latched{}; socklen_t latchedLen = 0;
    std::uint64_t tx = 0;
    std::uint64_t txMic = 0;
    std::atomic<bool> micOn{false};
    // Uplink level telemetry (metadata only: aggregate energy, never audio).
    // tx_mic counts frames, and with DTX off the encoder emits one every 20 ms
    // even for pure silence -- so the frame count cannot distinguish "carrying
    // speech" from "carrying nothing". These make the encoder input level
    // observable without capturing or storing any sample data.
    std::atomic<std::uint64_t> micSumSq{0};
    std::atomic<std::uint64_t> micSamples{0};
    std::atomic<int> micPeak{0};
    // RFC 3551 4.1: the first packet of a talkspurt is marked. We previously
    // never set M, so a gateway that opens the uplink leg on the talkspurt
    // boundary saw a stream that never started.
    std::atomic<bool> markNext{true};
    std::atomic<std::uint64_t> txOctets{0};
};

void RtpSend(int sock, TxState& st, std::span<const std::uint8_t> payload) {
    std::uint8_t hdr[12];
    sockaddr_storage a1, a2; socklen_t l1, l2;
    {
        std::scoped_lock g(st.lock);
        hdr[0] = 0x80;
        bool mark = st.markNext.exchange(false, std::memory_order_relaxed);
        hdr[1] = static_cast<std::uint8_t>((mark ? 0x80 : 0x00) | (st.pt & 0x7F));
        hdr[2] = (st.seq >> 8) & 0xFF; hdr[3] = st.seq & 0xFF;
        hdr[4] = (st.ts >> 24) & 0xFF; hdr[5] = (st.ts >> 16) & 0xFF;
        hdr[6] = (st.ts >> 8) & 0xFF;  hdr[7] = st.ts & 0xFF;
        hdr[8] = (st.ssrc >> 24) & 0xFF; hdr[9] = (st.ssrc >> 16) & 0xFF;
        hdr[10] = (st.ssrc >> 8) & 0xFF; hdr[11] = st.ssrc & 0xFF;
        st.seq = (st.seq + 1) & 0xFFFF;
        st.ts = (st.ts + 320) & 0xFFFFFFFF;
        a1 = st.dst; l1 = st.dstLen; a2 = st.latched; l2 = st.latchedLen;
    }
    std::vector<std::uint8_t> pkt(hdr, hdr + 12);
    pkt.insert(pkt.end(), payload.begin(), payload.end());
    auto sendTo = [&](sockaddr_storage& a, socklen_t l) {
        if (l && sendto(sock, pkt.data(), pkt.size(), 0, reinterpret_cast<sockaddr*>(&a), l) >= 0) {
            st.txOctets.fetch_add(payload.size(), std::memory_order_relaxed);
            std::scoped_lock g(st.lock);
            st.tx++;
        }
    };
    sendTo(a1, l1);
    // avoid double-send when latched == dst
    if (l2 && (l2 != l1 || std::memcmp(&a1, &a2, l1) != 0)) sendTo(a2, l2);
}

std::vector<std::pair<std::uint8_t, std::vector<std::uint8_t>>>
Depay(std::span<const std::uint8_t> pl, bool octetAlign) {
    return octetAlign ? DepayOctet(pl) : DepayBe(pl);
}

std::vector<std::uint8_t> SilenceFrame(int ft, bool octetAlign) {
    // storage frame: ToC(F=0,FT,Q=1) + zeroed speech, payloaded per mode.
    std::vector<std::uint8_t> f = {static_cast<std::uint8_t>((ft << 3) | 0x04)};
    f.resize(1 + AmrwbBytes(ft), 0);
    return PayloadFromFrame(f, octetAlign);
}

std::atomic<bool> Quit{false};
void OnSig(int) { Quit.store(true); }

// playout queue item
struct PktItem { std::uint32_t ts; std::vector<std::uint8_t> payload; };

}  // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string_view(argv[1]) == "--selftest") {
        // pack->depay roundtrip of every frame type, both payload formats.
        // BE carries exactly AmrwbBits(ft) bits, so the pattern's padding
        // bits in the last speech byte must be zero for equality to hold.
        for (int ft : {0, 1, 2, 3, 4, 5, 6, 7, 8, 9}) {
            std::vector<std::uint8_t> frame = {
                static_cast<std::uint8_t>((ft << 3) | 0x04)};
            int bits = AmrwbBits(ft);
            for (int i = 0; i < AmrwbBytes(ft); i++)
                frame.push_back(static_cast<std::uint8_t>(0xA5 + i * 31));
            if (bits % 8) frame.back() &= static_cast<std::uint8_t>(0xFF << (8 - bits % 8));
            for (bool oa : {true, false}) {
                auto got = Depay(PayloadFromFrame(frame, oa), oa);
                if (got.size() != 1 || got[0].first != frame[0] || !std::equal(got[0].second.begin(), got[0].second.end(), frame.begin() + 1, frame.end())) {
                    std::println(std::cerr, "selftest FAIL ft={} oa={}", ft, oa);
                    return 1;
                }
            }
        }
        std::println("selftest OK");
        return 0;
    }
    if (argc < 8) {
        std::println(std::cerr, "usage: imsd-media local rtp_port remote_ip remote_port pt secs out");
        return 2;
    }
    std::string local = argv[1];
    int rtpPort = std::atoi(argv[2]);
    std::string rIp = argv[3];
    int rPort = std::atoi(argv[4]);
    int pt = std::atoi(argv[5]);
    double secs = std::atof(argv[6]);
    std::string out = argv[7];

    bool mic = EnvBool("MIC", false);
    bool play = EnvBool("PLAY", false);
    int amrMode = std::atoi(EnvOr("AMR_MODE", "2").c_str());
    // A mastered audio fixture already peaks close to full scale, while the
    // FP6 microphone path needs its own hardware-specific gain. Do not apply
    // the microphone boost to MIC_FILE: it clips before AMR-WB encoding.
    // Keeping the controls independent also means a call-quality fixture
    // cannot silently alter normal-call capture.
    bool fileMic = !EnvOr("MIC_FILE", "").empty();
    double gain = std::atof(EnvOr(fileMic ? "MIC_FILE_GAIN" : "GAIN",
                                  fileMic ? "0.5" : "1.0").c_str());
    double playGain = std::atof(EnvOr("PLAY_GAIN", "1.0").c_str());
    int dtx = EnvBool("DTX", false) ? 1 : 0;
    bool octetAlign = EnvBool("OCTET_ALIGN", true);
    double mediaTimeout = std::atof(EnvOr("MEDIA_TIMEOUT", "6.0").c_str());
    bool rtpDump = EnvBool("RTP_DUMP", false);
    constexpr int ExitMediaTimeout = 3;
    constexpr int PrimeFrames = 8;
    constexpr int MaxFill = 25;
    constexpr std::size_t PlayqMax = 256;

    int sock = socket(Is6(local) ? AF_INET6 : AF_INET,
                      SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (sock < 0) { std::println(std::cerr, "imsd-media: socket failed"); return 1; }
    int one = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_storage bindA; socklen_t bindL = MakeAddr(local, rtpPort, bindA);
    if (bind(sock, reinterpret_cast<sockaddr*>(&bindA), bindL) != 0) {
        std::println(std::cerr, "imsd-media: bind [{}]:{} failed", local, rtpPort);
        return 1;
    }
    timeval tv{0, 20000};  // 20 ms recv timeout
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);

    // RTCP (RFC 3550 11): the peer sends its receiver reports to RTP port + 1,
    // and our SDP answer advertises b=RS:/b=RR: so the gateway expects that
    // port to exist. Nothing was ever bound there, so the kernel replied
    // ICMPv6 destination-unreachable/port-unreachable to every report --
    // observed live on qmapmux0.1 as (type 1, code 4) outbound, one per
    // inbound RTCP packet. A media gateway reads that as a dead endpoint and
    // stops relaying this leg's uplink to the far party, which is exactly the
    // one-way-audio symptom: our RTP left the device correctly and was
    // discarded upstream. Binding the port suppresses the ICMP error; the
    // reports themselves need no reply for the leg to stay up.
    int rtcpSock = socket(Is6(local) ? AF_INET6 : AF_INET,
                          SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (rtcpSock >= 0) {
        int r1 = 1;
        setsockopt(rtcpSock, SOL_SOCKET, SO_REUSEADDR, &r1, sizeof r1);
        sockaddr_storage rA; socklen_t rL = MakeAddr(local, rtpPort + 1, rA);
        if (bind(rtcpSock, reinterpret_cast<sockaddr*>(&rA), rL) != 0) {
            std::println(std::cerr, "imsd-media: rtcp bind [{}]:{} failed", local, rtpPort + 1);
            close(rtcpSock);
            rtcpSock = -1;
        } else {
            setsockopt(rtcpSock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
        }
    }

    TxState st;
    st.pt = pt;
    // RFC 3550 5.1: SSRC, initial sequence number and initial timestamp must be
    // chosen randomly per session. These were compile-time constants
    // (0x5EED1234 / 1000 / 160000), so every call this device ever placed
    // presented a byte-identical stream identity. An SBC that keeps
    // per-SSRC state or does duplicate/replay suppression across sessions can
    // discard the second and later streams outright -- packets valid, correctly
    // addressed, silently dropped.
    {
        std::random_device rd;
        auto r32 = [&] {
            return (static_cast<std::uint32_t>(rd()) << 16) ^ static_cast<std::uint32_t>(rd());
        };
        st.ssrc = r32();
        st.seq = r32() & 0xFFFF;
        st.ts = r32();
    }
    st.dstLen = MakeAddr(rIp, rPort, st.dst);
    st.latched = st.dst; st.latchedLen = st.dstLen;

    signal(SIGTERM, OnSig);
    signal(SIGINT, OnSig);
    signal(SIGPIPE, SIG_IGN);

    // ---- mic uplink thread
    std::atomic<bool> stop{false};
    // Drain inbound RTCP so the socket buffer cannot fill. The bind alone is
    // what stops the ICMP error; reading keeps it healthy for a long call.
    std::jthread rtcpThread;
    if (rtcpSock >= 0) {
        rtcpThread = std::jthread([&, rtcpSock] {
            std::uint8_t buf[512];
            while (!stop.load()) {
                sockaddr_storage from{}; socklen_t fl = sizeof from;
                (void)recvfrom(rtcpSock, buf, sizeof buf, 0,
                               reinterpret_cast<sockaddr*>(&from), &fl);
            }
        });
    }
    // RFC 3550 6.4.1: we are a sender, so we must emit Sender Reports. The
    // gateway sends us Receiver Reports every few seconds and got nothing back
    // -- our SDP advertises b=RS:/b=RR: bandwidth for exactly this traffic and
    // we transmitted none. A carrier SBC that polices RTCP treats a sender
    // that never reports as unusable and stops relaying its stream, which
    // leaves downlink intact and kills uplink only.
    std::jthread srThread;
    if (rtcpSock >= 0) {
        srThread = std::jthread([&, rtcpSock] {
            auto put32 = [](std::uint8_t* d, std::uint32_t v) {
                d[0] = (v >> 24) & 0xFF; d[1] = (v >> 16) & 0xFF;
                d[2] = (v >> 8) & 0xFF;  d[3] = v & 0xFF;
            };
            const char* cname = "imsd@luma";
            int waited = 0;
            while (!stop.load()) {
                std::this_thread::sleep_for(std::chrono::milliseconds(200));
                if (++waited < 25) continue;   // ~5 s
                waited = 0;
                sockaddr_storage d; socklen_t dl;
                std::uint32_t ssrc, rtpTs;
                std::uint64_t pkts, octs;
                {
                    std::scoped_lock g(st.lock);
                    d = st.dst; dl = st.dstLen; ssrc = st.ssrc; rtpTs = st.ts;
                    pkts = st.tx;
                }
                octs = st.txOctets.load();
                if (!dl) continue;
                // RTCP goes to the peer's RTP port + 1.
                if (d.ss_family == AF_INET6) {
                    auto* a6 = reinterpret_cast<sockaddr_in6*>(&d);
                    a6->sin6_port = htons(ntohs(a6->sin6_port) + 1);
                } else {
                    auto* a4 = reinterpret_cast<sockaddr_in*>(&d);
                    a4->sin_port = htons(ntohs(a4->sin_port) + 1);
                }
                auto now = std::chrono::system_clock::now().time_since_epoch();
                auto secs = std::chrono::duration_cast<std::chrono::seconds>(now).count();
                auto usec = std::chrono::duration_cast<std::chrono::microseconds>(now).count() % 1000000;
                std::uint32_t ntpSec = static_cast<std::uint32_t>(secs) + 2208988800u;
                std::uint32_t ntpFrac = static_cast<std::uint32_t>((usec / 1000000.0) * 4294967296.0);
                std::uint8_t buf[128]; int n = 0;
                // Sender Report (PT=200), no report blocks
                buf[n++] = 0x80; buf[n++] = 200; buf[n++] = 0; buf[n++] = 6;
                put32(buf + n, ssrc); n += 4;
                put32(buf + n, ntpSec); n += 4;
                put32(buf + n, ntpFrac); n += 4;
                put32(buf + n, rtpTs); n += 4;
                put32(buf + n, static_cast<std::uint32_t>(pkts)); n += 4;
                put32(buf + n, static_cast<std::uint32_t>(octs)); n += 4;
                // SDES (PT=202) with CNAME -- RFC 3550 requires it alongside SR
                int sdesStart = n;
                buf[n++] = 0x81; buf[n++] = 202; buf[n++] = 0; buf[n++] = 0;
                put32(buf + n, ssrc); n += 4;
                buf[n++] = 1;                                   // CNAME
                std::size_t cl = std::strlen(cname);
                buf[n++] = static_cast<std::uint8_t>(cl);
                std::memcpy(buf + n, cname, cl); n += static_cast<int>(cl);
                buf[n++] = 0;                                   // end of list
                while ((n - sdesStart) % 4) buf[n++] = 0;
                buf[sdesStart + 3] = static_cast<std::uint8_t>(((n - sdesStart) / 4) - 1);
                (void)!sendto(rtcpSock, buf, static_cast<std::size_t>(n), 0,
                              reinterpret_cast<sockaddr*>(&d), dl);
            }
        });
    }
    std::jthread micThread;
    if (mic) {
        micThread = std::jthread([&] {
            Encoder enc;
            if (!enc.Open()) {
                std::println(std::cerr, "imsd-media: mic: encoder unavailable; silence fallback");
                return;
            }
            Child rec = SpawnPw(false, /*toChild=*/false);
            if (rec.pid < 0) return;
            std::uint8_t raw[640];
            while (!stop.load()) {
                if (!ReadExact(rec.fd, raw, 640)) {
                    std::println(std::cerr, "imsd-media: mic: pw-record EOF; silence fallback");
                    break;
                }
                std::int16_t samples[320];
                std::memcpy(samples, raw, 640);
                if (gain != 1.0)
                    for (int i = 0; i < 320; i++) {
                        int v = static_cast<int>(samples[i] * gain);
                        samples[i] = static_cast<std::int16_t>(v < -32768 ? -32768 : (v > 32767 ? 32767 : v));
                    }
                {
                    std::uint64_t sq = 0;
                    int pk = 0;
                    for (int i = 0; i < 320; i++) {
                        int v = samples[i];
                        sq += static_cast<std::uint64_t>(v) * static_cast<std::uint64_t>(v);
                        int a = v < 0 ? -v : v;
                        if (a > pk) pk = a;
                    }
                    st.micSumSq.fetch_add(sq, std::memory_order_relaxed);
                    st.micSamples.fetch_add(320, std::memory_order_relaxed);
                    int prev = st.micPeak.load(std::memory_order_relaxed);
                    while (pk > prev &&
                           !st.micPeak.compare_exchange_weak(prev, pk,
                                                             std::memory_order_relaxed)) {}
                }
                std::vector<std::uint8_t> frame = enc.Encode(samples, amrMode, dtx);
                if (frame.empty()) continue;
                st.micOn.store(true);
                { std::scoped_lock g(st.lock); st.txMic++; }
                RtpSend(sock, st, PayloadFromFrame(frame, octetAlign));
            }
            st.micOn.store(false);
            StopChildGroup(rec);
        });
    }

    // ---- downlink playout (decode + RTP-timestamp clock reconstruction)
    Decoder dec;
    Child playCh;
    bool playOn = false;
    if (play) {
        if (dec.Open()) {
            playCh = SpawnPw(true, /*toChild=*/true);
            playOn = playCh.pid >= 0;
        } else {
            std::println(std::cerr, "imsd-media: play: decoder unavailable; capture only");
        }
    }
    std::mutex qlock;
    std::condition_variable qcv;
    std::deque<PktItem> playq;
    std::uint64_t rxPlayed = 0;
    std::uint64_t cng = 0;
    std::uint64_t late = 0;
    std::uint64_t qdrop = 0;
    std::jthread playThread;
    if (playOn) {
        playThread = std::jthread([&] {
            auto writePcm = [&](std::span<const std::int16_t> pcm) {
                std::size_t bytes = pcm.size() * 2;
                const char* p = reinterpret_cast<const char*>(pcm.data());
                std::size_t off = 0;
                while (off < bytes) {
                    ssize_t w = write(playCh.fd, p + off, bytes - off);
                    if (w <= 0) return false;
                    off += static_cast<std::size_t>(w);
                }
                return true;
            };
            auto applyGain = [&](std::array<std::int16_t, 320>& pcm) {
                if (playGain == 1.0) return;
                for (auto& s : pcm) {
                    int v = static_cast<int>(s * playGain);
                    s = static_cast<std::int16_t>(v < -32768 ? -32768 : (v > 32767 ? 32767 : v));
                }
            };
            std::array<std::int16_t, 320> zero{};
            std::optional<std::uint32_t> expect;
            for (;;) {
                PktItem item;
                {
                    std::unique_lock g(qlock);
                    qcv.wait(g, [&] { return !playq.empty() || stop.load(); });
                    if (playq.empty()) return;
                    item = std::move(playq.front());
                    playq.pop_front();
                }
                auto frames = Depay(item.payload, octetAlign);
                if (frames.empty()) continue;
                int fill = 0;
                if (!expect) {
                    for (int i = 0; i < PrimeFrames; i++)
                        if (!writePcm(zero)) return;
                } else {
                    std::uint32_t diff = (item.ts - *expect) & 0xFFFFFFFF;
                    if (diff >= 0x80000000u) { late++; continue; }
                    fill = static_cast<int>(diff / 320);
                    if (fill > MaxFill) fill = 0;
                }
                for (int i = 0; i < fill; i++) {
                    cng++;
                    std::uint8_t nodata = 0x7C;
                    auto pcm = dec.Decode(&nodata, 1);
                    applyGain(pcm);
                    if (!writePcm(pcm)) return;
                }
                for (auto& [hdr, speech] : frames) {
                    rxPlayed++;
                    std::vector<std::uint8_t> f = {hdr};
                    f.insert(f.end(), speech.begin(), speech.end());
                    auto pcm = dec.Decode(f.data(), static_cast<int>(f.size()));
                    applyGain(pcm);
                    if (!writePcm(pcm)) return;
                }
                expect = (item.ts + 320 * static_cast<std::uint32_t>(frames.size())) & 0xFFFFFFFF;
            }
        });
    }

    // ---- main recv loop
    // FT=15 is the RFC 4867 NO_DATA frame. A zero-filled FT=0 speech frame
    // decodes as harsh synthetic noise and was audible during early media.
    std::vector<std::uint8_t> silence = SilenceFrame(15, octetAlign);
    for (int i = 0; i < 5; i++) RtpSend(sock, st, silence);  // latch burst

    std::FILE* dump = rtpDump ? std::fopen((std::format("{}.rtp", out)).c_str(), "wb") : nullptr;
    double t0 = Now(), lastTx = 0, lastRx = Now();
    std::uint64_t rx = 0;
    std::uint64_t rxBytes = 0;
    bool gotMedia = false;
    bool mediaEnded = false;
    std::string firstSrc;

    while (Now() - t0 < secs && !Quit.load()) {
        double now = Now();
        if (mediaTimeout > 0 && gotMedia && now - lastRx > mediaTimeout) {
            mediaEnded = true;
            break;
        }
        if (now - lastTx >= 0.02 && !st.micOn.load()) {
            RtpSend(sock, st, silence);
            lastTx = now;
        }
        std::uint8_t buf[65535];
        sockaddr_storage src; socklen_t srcLen = sizeof src;
        ssize_t n = recvfrom(sock, buf, sizeof buf, 0, reinterpret_cast<sockaddr*>(&src), &srcLen);
        if (n <= 0) continue;
        lastRx = Now();
        gotMedia = true;
        if (firstSrc.empty()) {
            char host[INET6_ADDRSTRLEN] = {};
            int port = 0;
            if (src.ss_family == AF_INET6) {
                auto* a = reinterpret_cast<sockaddr_in6*>(&src);
                inet_ntop(AF_INET6, &a->sin6_addr, host, sizeof host);
                port = ntohs(a->sin6_port);
            } else {
                auto* a = reinterpret_cast<sockaddr_in*>(&src);
                inet_ntop(AF_INET, &a->sin_addr, host, sizeof host);
                port = ntohs(a->sin_port);
            }
            firstSrc = std::format("{}:{}", host, port);
            std::scoped_lock g(st.lock);
            std::memcpy(&st.latched, &src, srcLen);
            st.latchedLen = srcLen;  // relatch to the actual media source
        }
        rx++;
        rxBytes += static_cast<std::uint64_t>(n);
        if (dump) {
            std::uint32_t len = static_cast<std::uint32_t>(n);
            std::uint8_t lb[4] = {static_cast<std::uint8_t>((len >> 24) & 0xFF),
                                  static_cast<std::uint8_t>((len >> 16) & 0xFF),
                                  static_cast<std::uint8_t>((len >> 8) & 0xFF),
                                  static_cast<std::uint8_t>(len & 0xFF)};
            std::fwrite(lb, 1, 4, dump);
            std::fwrite(buf, 1, static_cast<std::size_t>(n), dump);
        }
        if (playThread.joinable() && n >= 12 && (buf[1] & 0x7F) == pt) {
            std::size_t off = RtpPayloadOffset(std::span(buf, static_cast<std::size_t>(n)));
            std::uint32_t pktTs = (static_cast<std::uint32_t>(buf[4]) << 24) |
                                  (buf[5] << 16) | (buf[6] << 8) | buf[7];
            std::scoped_lock g(qlock);
            if (playq.size() >= PlayqMax) { playq.pop_front(); qdrop++; }
            playq.push_back({pktTs, std::vector<std::uint8_t>(buf + off, buf + n)});
            qcv.notify_one();
        }
    }

    stop.store(true);
    qcv.notify_all();
    if (micThread.joinable()) micThread.join();
    if (playThread.joinable()) playThread.join();
    if (dump) std::fclose(dump);
    if (playOn) {
        StopChildGroup(playCh);
    }
    close(sock);
    if (rtcpSock >= 0) close(rtcpSock);

    // .stats sidecar (tiny; always written)
    if (std::FILE* sf = std::fopen((std::format("{}.stats", out)).c_str(), "w")) {
        std::print(sf,
            "{{\"tx\": {}, \"tx_mic\": {}, \"rx\": {}, \"rx_bytes\": {}, "
            "\"rx_played\": {}, \"cng\": {}, \"late\": {}, \"qdrop\": {}, "
            "\"first_src\": \"{}\", \"dst\": \"{}:{}\", \"pt\": {}, \"mic\": {}, "
            "\"play\": {}, \"amr_mode\": {}, \"media_ended\": {}}}",
            st.tx, st.txMic, rx, rxBytes, rxPlayed, cng, late, qdrop, firstSrc,
            rIp, rPort, pt, mic, play, amrMode, mediaEnded);
        std::fclose(sf);
    }
    std::uint64_t micN = st.micSamples.load();
    double micRms = micN ? std::sqrt(static_cast<double>(st.micSumSq.load()) /
                                     static_cast<double>(micN))
                         : 0.0;
    std::println("imsd-media: tx={} tx_mic={} rx={} rx_played={} cng={} late={} " "qdrop={} rx_bytes={} first_src={} media_ended={} mic_rms={:.1f} mic_peak={} mic_dbfs={:.1f}", st.tx, st.txMic, rx, rxPlayed, cng, late, qdrop, rxBytes, firstSrc, mediaEnded, micRms, st.micPeak.load(), micRms > 0 ? 20.0 * std::log10(micRms / 32768.0) : -99.0);
    return mediaEnded ? ExitMediaTimeout : 0;
}
