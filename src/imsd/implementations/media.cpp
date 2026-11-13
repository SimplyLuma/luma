// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types no-char-pointer
/*
imsd-media — the RTP media leg for a userspace VoLTE call, spawned as its
own process by the daemon (imsd) exactly as the Python prototype spawned
rtpcap.py: one media leg per call, argv-configured, torn down on SIGTERM or
when the downlink dries up. Keeping it a separate process preserves the
far-end-hangup contract (exit code 3 = downlink RTP stopped, which on carriers
whose network BYE never reaches our SAs is the reliable teardown trigger) and
isolates a media crash from the control-plane daemon.

Codecs (CODEC env, set by the daemon from the negotiated SDP): AMR-WB
(16 kHz; the mobile-to-mobile VoLTE codec, the default), AMR narrowband and
G.711 PCMA/PCMU (8 kHz; what a PSTN gateway offers when a landline calls).
AMR frames ride RFC 4867 payloads, octet-aligned or bandwidth-efficient
(OCTET_ALIGN, mirrored from the SDP); G.711 is raw samples. The AMR codecs
are dlopen'd (libvo-amrwbenc + libopencore-amrwb for WB, libopencore-amrnb
for NB) so the binary has no link-time dependency on them; G.711 is a table.

Binds the advertised local RTP port, sends uplink frames toward the media
gateway, captures the downlink, and — with PLAY=1 — reconstructs the media
clock from RTP timestamps so pw-play stays real-time-paced through far-end
DTX silence (every missing 20 ms slot is decoded as a NO_DATA frame for
CNG/PLC; zeros for G.711). MIC=1 feeds live pw-record audio through the
encoder.

DTMF goes out as RFC 4733 telephone-event packets at the payload type the
SDP negotiated for the codec's clock (DTMF_PT; unset = none): stdin is the
daemon's control pipe, and each `dtmf <tones>` line queues its tones (0-9,
*, #, A-D). A tone takes seven 20 ms slots of the uplink in place of the
audio frames — the event's five (100 ms at -10 dBm0) and two in which its
end packet goes out again — then three slots of audio run before the next
tone. The far end's events — taken at DTMF_RX_PT (default DTMF_PT) and
at DTMF_PT, never at the audio's payload type — are played as the tones
they stand for, in step with the audio clock.

Usage: imsd-media <local-ip> <rtp-port> <remote-ip> <remote-port> <pt> <secs> <out-base>
       imsd-media --selftest   (payload pack/depay roundtrip, both formats,
                                both AMR codecs; G.711 table roundtrip; the
                                telephone-event payload, codes and Q.23
                                frequency pairs)
Env: CODEC MIC PLAY GAIN PLAY_GAIN AMR_MODE DTX MEDIA_TIMEOUT RTP_DUMP
OCTET_ALIGN AUDIO_USER MIC_SRC PCM_DUMP DTMF_PT DTMF_RX_PT
(OCTET_ALIGN=0 selects RFC 4867 bandwidth-efficient payloads both ways; the
daemon sets it from the negotiated SDP — 1 is the default/legacy behavior.
AMR_MODE is the encoder mode of whichever AMR codec is active; default 2
for AMR-WB (12.65 kbit/s), 7 for AMR (12.2 kbit/s). MIC_SRC=<file> feeds
raw s16 PCM at the codec rate through the encoder instead of pw-record,
paced in real time; PCM_DUMP=1 writes the decoded downlink to <out-base>.pcm
— both are test seams for driving the leg against a synthetic RTP peer
with no PipeWire and no network.)
*/

#include <arpa/inet.h>
#include <cerrno>
#include <dlfcn.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
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

enum class Codec { AmrWb, AmrNb, Pcma, Pcmu };

Codec ParseCodec(std::string_view name) {
    if (name == "AMR") return Codec::AmrNb;
    if (name == "PCMA") return Codec::Pcma;
    if (name == "PCMU") return Codec::Pcmu;
    return Codec::AmrWb;
}
std::string_view CodecName(Codec c) {
    switch (c) {
        case Codec::AmrWb: return "AMR-WB";
        case Codec::AmrNb: return "AMR";
        case Codec::Pcma: return "PCMA";
        case Codec::Pcmu: return "PCMU";
    }
    return "AMR-WB";
}
bool IsAmr(Codec c) { return c == Codec::AmrWb || c == Codec::AmrNb; }
// Sample rate = RTP clock rate; one 20 ms frame is 320 or 160 samples.
int CodecRate(Codec c) { return c == Codec::AmrWb ? 16000 : 8000; }
int FrameSamples(Codec c) { return CodecRate(c) / 50; }

// AMR speech bits per frame type — the exact payload bit counts of the
// bandwidth-efficient format. AMR-WB: RFC 4867 table 2 / TS 26.201; AMR:
// RFC 4867 table 1 / TS 26.101 (FT 8 = SID, 9-11 = the other systems' SID
// frames a gateway may forward). FT 14/15 (SPEECH_LOST/NO_DATA) carry 0
// bits; unknown FTs return -1 so a corrupt ToC aborts the packet instead of
// shifting every later bit.
constexpr int AmrBits(Codec c, int ft) {
    if (c == Codec::AmrWb) {
        switch (ft) {
            case 0: return 132; case 1: return 177; case 2: return 253; case 3: return 285;
            case 4: return 317; case 5: return 365; case 6: return 397; case 7: return 461;
            case 8: return 477; case 9: return 40; case 14: return 0; case 15: return 0;
            default: return -1;
        }
    }
    switch (ft) {
        case 0: return 95; case 1: return 103; case 2: return 118; case 3: return 134;
        case 4: return 148; case 5: return 159; case 6: return 204; case 7: return 244;
        case 8: return 39; case 9: return 43; case 10: return 38; case 11: return 37;
        case 15: return 0;
        default: return -1;
    }
}

// octet-aligned speech-frame byte size by frame type: the bits rounded up
// (AMR-WB: 17 23 32 36 40 46 50 58 60, SID 5; AMR: 12 13 15 17 19 20 26 31,
// SID 5). 0 for frame types that carry no speech.
constexpr int AmrBytes(Codec c, int ft) {
    int bits = AmrBits(c, ft);
    return bits <= 0 ? 0 : (bits + 7) / 8;
}

// ---- G.711 (ITU-T, the classic Sun g711.c formulation) --------------------
// 16-bit linear <-> 8-bit companded. Byte 0xD5 (A-law) / 0xFF (mu-law) is
// digital zero. Idempotent: encoding a decoded sample gives the same byte.

constexpr int UlawBias = 0x84;
constexpr int UlawClip = 8159;

int SegmentOf(int val, std::span<const int> ends) {
    for (std::size_t i = 0; i < ends.size(); i++)
        if (val <= ends[i]) return static_cast<int>(i);
    return static_cast<int>(ends.size());
}

std::uint8_t LinearToAlaw(std::int16_t pcm) {
    static constexpr std::array<int, 8> ends = {0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF};
    int val = pcm >> 3;  // 13-bit magnitude space
    int mask = 0xD5;
    if (val < 0) {
        mask = 0x55;
        val = -val - 1;
    }
    int seg = SegmentOf(val, ends);
    if (seg >= 8) return static_cast<std::uint8_t>(0x7F ^ mask);
    int aval = seg << 4;
    aval |= seg < 2 ? (val >> 1) & 0x0F : (val >> seg) & 0x0F;
    return static_cast<std::uint8_t>(aval ^ mask);
}

std::int16_t AlawToLinear(std::uint8_t a) {
    a ^= 0x55;
    int t = (a & 0x0F) << 4;
    int seg = (a & 0x70) >> 4;
    if (seg == 0) t += 8;
    else if (seg == 1) t += 0x108;
    else t = (t + 0x108) << (seg - 1);
    return static_cast<std::int16_t>((a & 0x80) ? t : -t);
}

std::uint8_t LinearToUlaw(std::int16_t pcm) {
    static constexpr std::array<int, 8> ends = {0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF};
    int val = pcm >> 2;  // 14-bit magnitude space
    int mask = 0xFF;
    if (val < 0) {
        val = -val;
        mask = 0x7F;
    }
    if (val > UlawClip) val = UlawClip;
    val += UlawBias >> 2;
    int seg = SegmentOf(val, ends);
    if (seg >= 8) return static_cast<std::uint8_t>(0x7F ^ mask);
    int uval = (seg << 4) | ((val >> (seg + 1)) & 0x0F);
    return static_cast<std::uint8_t>(uval ^ mask);
}

std::int16_t UlawToLinear(std::uint8_t u) {
    u = static_cast<std::uint8_t>(~u);
    int t = ((u & 0x0F) << 3) + UlawBias;
    t <<= (u & 0x70) >> 4;
    return static_cast<std::int16_t>((u & 0x80) ? (UlawBias - t) : (t - UlawBias));
}

std::uint8_t G711Encode(Codec c, std::int16_t pcm) { return c == Codec::Pcma ? LinearToAlaw(pcm) : LinearToUlaw(pcm); }
std::int16_t G711Decode(Codec c, std::uint8_t b) { return c == Codec::Pcma ? AlawToLinear(b) : UlawToLinear(b); }
std::uint8_t G711Zero(Codec c) { return c == Codec::Pcma ? 0xD5 : 0xFF; }

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

// ---- AMR codecs via dlopen ------------------------------------------------
// AMR-WB: vo-amrwbenc E_IF_* (encode), opencore-amrwb D_IF_* (decode).
// AMR:    opencore-amrnb Encoder_Interface_* / Decoder_Interface_*.
// Both families speak the RFC 4867 §5.3 storage format: [header byte][speech],
// header = (FT << 3) | (Q << 2) — exactly the octet-aligned ToC byte with F=0.

class Encoder {
public:
    bool Open(Codec c, int dtx) {
        codec_ = c;
        if (c == Codec::AmrWb) {
            lib_ = dlopen("libvo-amrwbenc.so.0", RTLD_NOW);
            if (!lib_) return false;
            wbInit_ = reinterpret_cast<WbInitFn>(dlsym(lib_, "E_IF_init"));
            wbEnc_ = reinterpret_cast<WbEncFn>(dlsym(lib_, "E_IF_encode"));
            exit_ = reinterpret_cast<ExitFn>(dlsym(lib_, "E_IF_exit"));
            if (!wbInit_ || !wbEnc_ || !exit_) return false;
            st_ = wbInit_();
            return st_ != nullptr;
        }
        lib_ = dlopen("libopencore-amrnb.so.0", RTLD_NOW);
        if (!lib_) return false;
        nbInit_ = reinterpret_cast<NbInitFn>(dlsym(lib_, "Encoder_Interface_init"));
        nbEnc_ = reinterpret_cast<NbEncFn>(dlsym(lib_, "Encoder_Interface_Encode"));
        exit_ = reinterpret_cast<ExitFn>(dlsym(lib_, "Encoder_Interface_exit"));
        if (!nbInit_ || !nbEnc_ || !exit_) return false;
        st_ = nbInit_(dtx);  // NB: DTX is an init-time choice
        return st_ != nullptr;
    }
    // one 20 ms frame of s16 samples -> one storage frame (header byte + speech).
    std::vector<std::uint8_t> Encode(std::int16_t* samples, int mode, int dtx) {
        std::uint8_t out[128];
        int n = codec_ == Codec::AmrWb
            ? wbEnc_(st_, static_cast<std::int16_t>(mode), samples, out, static_cast<std::int16_t>(dtx))
            : nbEnc_(st_, mode, samples, out, 0);
        if (n <= 0) return {};
        return std::vector<std::uint8_t>(out, out + n);
    }
    ~Encoder() {
        if (st_ && exit_) exit_(st_);
        if (lib_) dlclose(lib_);
    }
private:
    using WbInitFn = void* (*)();
    using WbEncFn = int (*)(void*, std::int16_t, std::int16_t*, std::uint8_t*, std::int16_t);
    using NbInitFn = void* (*)(int);
    using NbEncFn = int (*)(void*, int, const std::int16_t*, std::uint8_t*, int);
    using ExitFn = void (*)(void*);
    Codec codec_ = Codec::AmrWb;
    void* lib_ = nullptr;
    void* st_ = nullptr;
    WbInitFn wbInit_ = nullptr;
    WbEncFn wbEnc_ = nullptr;
    NbInitFn nbInit_ = nullptr;
    NbEncFn nbEnc_ = nullptr;
    ExitFn exit_ = nullptr;
};

class Decoder {
public:
    bool Open(Codec c) {
        codec_ = c;
        bool wb = c == Codec::AmrWb;
        lib_ = dlopen(wb ? "libopencore-amrwb.so.0" : "libopencore-amrnb.so.0", RTLD_NOW);
        if (!lib_) return false;
        init_ = reinterpret_cast<InitFn>(dlsym(lib_, wb ? "D_IF_init" : "Decoder_Interface_init"));
        dec_ = reinterpret_cast<DecFn>(dlsym(lib_, wb ? "D_IF_decode" : "Decoder_Interface_Decode"));
        exit_ = reinterpret_cast<ExitFn>(dlsym(lib_, wb ? "D_IF_exit" : "Decoder_Interface_exit"));
        if (!init_ || !dec_ || !exit_) return false;
        st_ = init_();
        return st_ != nullptr;
    }
    // one storage frame ([header][speech]) -> one 20 ms frame of s16 PCM.
    std::vector<std::int16_t> Decode(const std::uint8_t* frame, int len) {
        std::vector<std::int16_t> out(static_cast<std::size_t>(FrameSamples(codec_)), 0);
        std::vector<std::uint8_t> in(frame, frame + len);
        dec_(st_, in.data(), out.data(), 0);
        return out;
    }
    ~Decoder() {
        if (st_ && exit_) exit_(st_);
        if (lib_) dlclose(lib_);
    }
private:
    using InitFn = void* (*)();
    using DecFn = void (*)(void*, const std::uint8_t*, std::int16_t*, int);
    using ExitFn = void (*)(void*);
    Codec codec_ = Codec::AmrWb;
    void* lib_ = nullptr;
    void* st_ = nullptr;
    InitFn init_ = nullptr;
    DecFn dec_ = nullptr;
    ExitFn exit_ = nullptr;
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

// bandwidth-efficient AMR de-payload. Same contract as DepayOctet:
// [(storage-header-byte, speech-bytes)...], speech re-aligned to octets.
std::vector<std::pair<std::uint8_t, std::vector<std::uint8_t>>>
DepayBe(Codec c, std::span<const std::uint8_t> pl) {
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
        int bits = AmrBits(c, ft);
        if (bits < 0 || !br.Ok(static_cast<std::size_t>(bits))) break;
        std::vector<std::uint8_t> speech(AmrBytes(c, ft), 0);
        for (int i = 0; i < bits; i++)
            if (br.Take(1)) speech[i >> 3] |= 0x80 >> (i & 7);
        out.emplace_back(static_cast<std::uint8_t>((ft << 3) | (q ? 0x04 : 0)), std::move(speech));
    }
    return out;
}

// storage-format frame (header byte + octet-aligned speech) -> RTP payload.
// Octet-aligned: CMR byte + the frame verbatim (the storage header doubles
// as a ToC byte with F=0). Bandwidth-efficient: 10 header bits + exactly
// AmrBits(ft) speech bits, final octet zero-padded.
std::vector<std::uint8_t> PayloadFromFrame(Codec c, std::span<const std::uint8_t> frame, bool octetAlign) {
    if (octetAlign) {
        std::vector<std::uint8_t> pl = {0xF0};
        pl.insert(pl.end(), frame.begin(), frame.end());
        return pl;
    }
    int ft = (frame[0] >> 3) & 0x0F;
    int q = (frame[0] >> 2) & 1;
    int bits = AmrBits(c, ft);
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

// octet-aligned AMR de-payload: skip CMR, read ToC bytes until F=0, then
// the speech runs. Returns [(storage-header-byte, speech-bytes)...].
std::vector<std::pair<std::uint8_t, std::vector<std::uint8_t>>>
DepayOctet(Codec c, std::span<const std::uint8_t> pl) {
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
        int n = AmrBytes(c, ft);
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

// The desktop session whose PipeWire instance carries call audio, resolved
// ONCE and thread-safely: getpwnam() hands out one static buffer, and the
// mic and playout threads spawn their pw helpers at the same instant — the
// race sent both into /run/user/0, where no PipeWire session lives, and a
// call was answered into static (dev phone, 2026-09-08 20:01: tx_mic=0
// rx_played=0 qdrop=883). AUDIO_USER names the account: imsd sets it per
// call from logind's seat0 session (or its own AUDIO_USER), and sets it
// empty when nobody is logged in. The packaged default used to be
// postmarketOS's account name `user`: a unit whose account was called
// otherwise got no audio and no message (alyx report 14, 2026-09-20).
// That default stays for a caller that does not set the variable at all —
// an imsd older than 0.3.10, still running while an upgrade replaces this
// binary, or a hand run — so its calls keep their audio.
// Logged per call so a journal shows which session the helpers were aimed at.
struct AudioSession { uid_t uid = 0; gid_t gid = 0; std::string name; };

const AudioSession& Session() {
    static std::once_flag once;
    static AudioSession s;
    std::call_once(once, [] {
        passwd pw{};
        passwd* res = nullptr;
        char buf[4096];
        const char* given = std::getenv("AUDIO_USER");
        std::string want = given ? given : "user";
        if (!given) std::println(std::cerr, "imsd-media: no AUDIO_USER in the environment (a caller older than 0.3.10, or a hand run); trying the account `user`");
        if (want.empty())
            std::println(std::cerr, "imsd-media: AUDIO_USER empty (no session to aim at); the helpers run as uid 0 (no audio)");
        else if (getpwnam_r(want.c_str(), &pw, buf, sizeof buf, &res) == 0 && res) { s.uid = res->pw_uid; s.gid = res->pw_gid; s.name = want; }
        else
            std::println(std::cerr, "imsd-media: AUDIO_USER '{}' is not an account; the helpers run as uid 0 (no audio)", want);
        std::println(std::cerr, "imsd-media: audio session: {} -> /run/user/{}", s.name.empty() ? std::string("(none)") : s.name, s.uid);
    });
    return s;
}

// Spawn pw-record/pw-play in the desktop session's PipeWire instance (sudo -u
// when run as root; as ourselves when there is no session to aim at).
// `toChild` true = we write the child's stdin (pw-play); false = we read its
// stdout (pw-record). Returns {pid, fd}.
struct Child { pid_t pid = -1; int fd = -1; };
Child SpawnPw(bool play, bool toChild, int rate) {
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
        const AudioSession& session = Session();
        if (!session.name.empty()) {
            audioUid = session.uid;
            audioGid = session.gid;
            audioUser = session.name;
        }
    }
    std::vector<std::string> argv;
    std::string rateStr = std::to_string(rate);
    std::string micFile = EnvOr("MIC_FILE", "");
    if (!play && !micFile.empty()) {
        // Deterministic call-quality fixture: decode and loop the supplied
        // track directly to mono PCM at the codec's clock. Normal calls leave
        // MIC_FILE unset and continue to use the hardware microphone below.
        for (const char* a : {"ffmpeg", "-hide_banner", "-loglevel", "error",
                              "-re", "-stream_loop", "-1", "-i"})
            argv.emplace_back(a);
        argv.push_back(micFile);
        for (const char* a : {"-f", "s16le", "-acodec", "pcm_s16le", "-ar",
                              rateStr.c_str(), "-ac", "1", "pipe:1"})
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
                              rateStr.c_str(), "--channels", "1", "--format", "s16",
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
        int devnull = open("/dev/null", O_RDWR);
        if (devnull >= 0) {
            dup2(devnull, STDERR_FILENO);
            // our stdin is the daemon's control pipe: not pw-record's to hold
            if (!toChild) dup2(devnull, STDIN_FILENO);
            close(devnull);
        }
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
    std::uint32_t tsStep = 320;  // samples per 20 ms frame at the codec's clock
    std::uint32_t ssrc = 0x5EED1234;
    std::uint32_t seq = 1000;
    std::uint32_t ts = 160000;
    sockaddr_storage dst{}; socklen_t dstLen = 0;
    sockaddr_storage latched{}; socklen_t latchedLen = 0;
    std::uint64_t tx = 0;
    std::uint64_t txMic = 0;
    std::uint64_t txDtmf = 0;
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

// One packet on the uplink, audio or event. Every packet stands in one
// 20 ms slot of the stream's single timestamp base (RFC 4733 §2.1: events
// share the audio's sequence and timestamp space), so the next slot's
// timestamp advances whichever kind went out; an audio frame is stamped
// with its slot (`ts` empty), an event packet with the event's start
// (`ts` given — constant over the event, §2.5.1.2). Returns the timestamp
// used.
std::uint32_t RtpSendPacket(int sock, TxState& st, int pt, bool marker, std::optional<std::uint32_t> ts, std::span<const std::uint8_t> payload) {
    std::uint8_t hdr[12];
    sockaddr_storage a1;
    sockaddr_storage a2;
    socklen_t l1;
    socklen_t l2;
    std::uint32_t stamp;
    {
        std::scoped_lock g(st.lock);
        stamp = ts.value_or(st.ts);
        hdr[0] = 0x80;
        // A talkspurt that follows silence or NO_DATA carries the marker
        // (RFC 3551 4.1); only audio packets consume that pending mark.
        bool mark = marker || (pt == st.pt && st.markNext.exchange(false, std::memory_order_relaxed));
        hdr[1] = static_cast<std::uint8_t>((mark ? 0x80 : 0) | (pt & 0x7F));
        hdr[2] = (st.seq >> 8) & 0xFF; hdr[3] = st.seq & 0xFF;
        hdr[4] = (stamp >> 24) & 0xFF; hdr[5] = (stamp >> 16) & 0xFF;
        hdr[6] = (stamp >> 8) & 0xFF;  hdr[7] = stamp & 0xFF;
        hdr[8] = (st.ssrc >> 24) & 0xFF; hdr[9] = (st.ssrc >> 16) & 0xFF;
        hdr[10] = (st.ssrc >> 8) & 0xFF; hdr[11] = st.ssrc & 0xFF;
        st.seq = (st.seq + 1) & 0xFFFF;
        st.ts = (st.ts + st.tsStep) & 0xFFFFFFFF;
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
    return stamp;
}

// an audio frame: the stream's payload type, stamped with its slot
void RtpSend(int sock, TxState& st, std::span<const std::uint8_t> payload) {
    RtpSendPacket(sock, st, st.pt, false, std::nullopt, payload);
}

// ---- DTMF: RFC 4733 telephone-event packets on the uplink -----------------
// A tone is one event of five slots (100 ms): an event packet in each in
// place of the audio frame, the duration growing by a slot, the marker bit
// on the first, the end bit on the fifth, which goes out three times, the
// repeats in two slots of their own (§2.5.1.4) — then three slots of audio
// before the next tone. The gateway's detector sees a 100 ms tone and a
// 100 ms gap: what a keypad press produces, and Q.24's 40 ms minimums
// with margin. The audio's own senders carry the events — per slot, a
// captured frame in the mic thread or a silence tick in the main loop
// without a mic (and the latch burst), `DtmfSender::SendSlot` sends
// either the event packet the slot carries or the frame, under one lock —
// so one packet leaves per slot, no frame can land inside an event
// whichever thread holds the slot, and the stream's clock stays the
// capture's; the slots an event took advanced it like frames
// (RtpSendPacket), so the audio that follows is stamped as if it had run
// through it.
constexpr int DtmfToneSlots = 5;
constexpr int DtmfEndRepeats = 2;
constexpr int DtmfGapSlots = 3;
constexpr std::uint8_t DtmfVolume = 10;   // -10 dBm0 (§2.3.4: 0 to -63)

// The event code of a keypad character (§3.2: 0-9, * = 10, # = 11, A-D =
// 12-15, either case); nullopt for anything else.
std::optional<int> DtmfEvent(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c == '*') return 10;
    if (c == '#') return 11;
    if (c >= 'A' && c <= 'D') return 12 + (c - 'A');
    if (c >= 'a' && c <= 'd') return 12 + (c - 'a');
    return std::nullopt;
}

// §2.3: event, E | R | volume, the duration in timestamp units, big-endian.
std::array<std::uint8_t, 4> DtmfPayload(int event, bool end, std::uint16_t duration) {
    return {static_cast<std::uint8_t>(event),
            static_cast<std::uint8_t>((end ? 0x80 : 0) | DtmfVolume),
            static_cast<std::uint8_t>(duration >> 8),
            static_cast<std::uint8_t>(duration & 0xFF)};
}

// The tone an event stands for (ITU-T Q.23 / RFC 4733 §3.2): the keypad's
// row and column frequencies of events 0-15; nullopt for any other event.
struct DtmfPair { int low; int high; };
std::optional<DtmfPair> DtmfFrequencies(int event) {
    static constexpr std::array<int, 4> lows = {697, 770, 852, 941};
    static constexpr std::array<int, 4> highs = {1209, 1336, 1477, 1633};
    // keypad rows 1 2 3 A / 4 5 6 B / 7 8 9 C / * 0 # D, indexed by event
    static constexpr std::array<std::pair<int, int>, 16> cell = {{
        {3, 1}, {0, 0}, {0, 1}, {0, 2}, {1, 0}, {1, 1}, {1, 2}, {2, 0},
        {2, 1}, {2, 2}, {3, 0}, {3, 2}, {0, 3}, {1, 3}, {2, 3}, {3, 3}}};
    if (event < 0 || event > 15) return std::nullopt;
    const auto [row, col] = cell[static_cast<std::size_t>(event)];
    return DtmfPair{lows[static_cast<std::size_t>(row)], highs[static_cast<std::size_t>(col)]};
}

// Sample `k` of a tone at `rate`: the two sines at `amp` each.
std::int16_t DtmfSample(DtmfPair f, std::uint32_t k, int rate, double amp) {
    const double t = static_cast<double>(k) / rate;
    const double v = amp * (std::sin(2 * std::numbers::pi * f.low * t) + std::sin(2 * std::numbers::pi * f.high * t));
    return static_cast<std::int16_t>(std::lround(std::clamp(v, -32768.0, 32767.0)));
}

// The tones the control pipe queued, paid out one slot at a time by the
// audio's own senders: one sender per slot, so no frame can land inside an
// event and the stream's clock is the capture's.
class DtmfSender {
public:
    void Queue(std::string_view tones) {
        std::scoped_lock g(lock_);
        for (char c : tones)
            if (auto ev = DtmfEvent(c)) queue_.push_back(*ev);
    }
    // Called by the audio's sender for every uplink slot, with the frame
    // it would send: under one lock the slot is decided and sent — the
    // event packet the slot carries (returns true: the frame stays unsent),
    // or the frame (false; an empty frame, a DTX gap, sends nothing). One
    // decision and one send per slot, so no frame can land inside an event
    // whichever thread holds the slot.
    bool SendSlot(int sock, TxState& st, int pt, std::span<const std::uint8_t> frame) {
        std::scoped_lock g(lock_);
        if (gap_ > 0) {
            gap_--;
        } else if (event_ >= 0 || !queue_.empty()) {
            if (event_ < 0) {
                event_ = queue_.front();
                queue_.pop_front();
                slot_ = 0;
                start_.reset();
            }
            slot_++;
            const int reported = std::min(slot_, DtmfToneSlots);
            const auto payload = DtmfPayload(event_, slot_ >= DtmfToneSlots, static_cast<std::uint16_t>(reported * st.tsStep));
            start_ = RtpSendPacket(sock, st, pt, slot_ == 1, start_, payload);
            { std::scoped_lock s(st.lock); st.txDtmf++; }
            if (slot_ == DtmfToneSlots + DtmfEndRepeats) {
                event_ = -1;
                gap_ = DtmfGapSlots;
            }
            return true;
        }
        if (!frame.empty()) RtpSend(sock, st, frame);
        return false;
    }
private:
    std::mutex lock_;
    std::deque<int> queue_;
    int event_ = -1;   // the event on the wire; -1 = none
    int slot_ = 0;     // its packets sent so far
    int gap_ = 0;      // slots of audio owed before the next event
    std::optional<std::uint32_t> start_;   // the event's timestamp
};

std::vector<std::pair<std::uint8_t, std::vector<std::uint8_t>>>
Depay(Codec c, std::span<const std::uint8_t> pl, bool octetAlign) {
    return octetAlign ? DepayOctet(c, pl) : DepayBe(c, pl);
}

// What goes out every 20 ms while the mic is not feeding frames: for AMR a
// storage frame ToC(F=0,FT,Q=1) + zeroed speech, payloaded per mode; for
// G.711 one frame of digital zero.
std::vector<std::uint8_t> SilenceFrame(Codec c, int ft, bool octetAlign) {
    if (!IsAmr(c)) return std::vector<std::uint8_t>(static_cast<std::size_t>(FrameSamples(c)), G711Zero(c));
    std::vector<std::uint8_t> f = {static_cast<std::uint8_t>((ft << 3) | 0x04)};
    f.resize(1 + static_cast<std::size_t>(AmrBytes(c, ft)), 0);
    return PayloadFromFrame(c, f, octetAlign);
}

// G.711 uplink: one 20 ms frame of samples -> one payload of companded bytes.
std::vector<std::uint8_t> G711Payload(Codec c, std::span<const std::int16_t> pcm) {
    std::vector<std::uint8_t> pl(pcm.size());
    for (std::size_t i = 0; i < pcm.size(); i++)
        pl[i] = G711Encode(c, pcm[i]);
    return pl;
}

// G.711 downlink: one frame's worth of companded bytes -> PCM.
std::vector<std::int16_t> G711DecodeFrame(Codec c, std::span<const std::uint8_t> bytes) {
    std::vector<std::int16_t> pcm(bytes.size());
    for (std::size_t i = 0; i < bytes.size(); i++)
        pcm[i] = G711Decode(c, bytes[i]);
    return pcm;
}

std::atomic<bool> Quit{false};
void OnSig(int) { Quit.store(true); }

// playout queue item: an audio packet, or (`event`) a telephone-event one
struct PktItem { std::uint32_t ts; std::vector<std::uint8_t> payload; bool event = false; };

// --selftest: pack->depay roundtrip of every frame type, both payload
// formats, both AMR codecs; G.711 digital zero, idempotence over the whole
// 16-bit range, and a 1 kHz sine surviving with the codec's nominal SNR;
// the RFC 4733 telephone-event payload bytes, every keypad character's
// event code, every event's Q.23 frequency pair and no tone past D.
int SelfTest() {
    for (Codec c : {Codec::AmrWb, Codec::AmrNb}) {
        for (int ft = 0; ft < 16; ft++) {
            int bits = AmrBits(c, ft);
            if (bits <= 0) continue;
            // BE carries exactly AmrBits(ft) bits, so the pattern's padding
            // bits in the last speech byte must be zero for equality to hold.
            std::vector<std::uint8_t> frame = {static_cast<std::uint8_t>((ft << 3) | 0x04)};
            for (int i = 0; i < AmrBytes(c, ft); i++)
                frame.push_back(static_cast<std::uint8_t>(0xA5 + i * 31));
            if (bits % 8) frame.back() &= static_cast<std::uint8_t>(0xFF << (8 - bits % 8));
            for (bool oa : {true, false}) {
                auto got = Depay(c, PayloadFromFrame(c, frame, oa), oa);
                if (got.size() != 1 || got[0].first != frame[0] || !std::equal(got[0].second.begin(), got[0].second.end(), frame.begin() + 1, frame.end())) {
                    std::println(std::cerr, "selftest FAIL {} ft={} oa={}", CodecName(c), ft, oa);
                    return 1;
                }
            }
        }
    }
    if (LinearToAlaw(0) != 0xD5 || LinearToUlaw(0) != 0xFF) {
        std::println(std::cerr, "selftest FAIL G.711 digital zero");
        return 1;
    }
    // RFC 4733 §2.3: event, E | R | volume (-10 dBm0), duration big-endian
    if (DtmfPayload(1, false, 160) != std::array<std::uint8_t, 4>{0x01, 0x0A, 0x00, 0xA0}
        || DtmfPayload(11, true, 1600) != std::array<std::uint8_t, 4>{0x0B, 0x8A, 0x06, 0x40}) {
        std::println(std::cerr, "selftest FAIL telephone-event payload");
        return 1;
    }
    // §3.2: every keypad character's event code, the letters in either case,
    // and no code for anything else
    for (const char* keys : {"0123456789*#ABCD", "0123456789*#abcd"}) {
        for (int ev = 0; ev < 16; ev++) {
            if (DtmfEvent(keys[ev]) != ev) {
                std::println(std::cerr, "selftest FAIL telephone-event code of '{}'", keys[ev]);
                return 1;
            }
        }
    }
    if (DtmfEvent('E') || DtmfEvent('e') || DtmfEvent(' ') || DtmfEvent('\n')) {
        std::println(std::cerr, "selftest FAIL telephone-event codes");
        return 1;
    }
    // Q.23: every event's pair — rows 697/770/852/941 Hz for 1 2 3 A, 4 5 6 B,
    // 7 8 9 C, * 0 # D; columns 1209/1336/1477/1633 Hz — and no tone past D
    static constexpr std::array<std::pair<int, int>, 16> q23 = {{
        {941, 1336}, {697, 1209}, {697, 1336}, {697, 1477}, {770, 1209}, {770, 1336}, {770, 1477}, {852, 1209},
        {852, 1336}, {852, 1477}, {941, 1209}, {941, 1477}, {697, 1633}, {770, 1633}, {852, 1633}, {941, 1633}}};
    for (int ev = 0; ev < 16; ev++) {
        const auto f = DtmfFrequencies(ev);
        if (!f || f->low != q23[static_cast<std::size_t>(ev)].first || f->high != q23[static_cast<std::size_t>(ev)].second) {
            std::println(std::cerr, "selftest FAIL telephone-event tone {}", ev);
            return 1;
        }
    }
    if (DtmfFrequencies(16) || DtmfFrequencies(-1)) {
        std::println(std::cerr, "selftest FAIL telephone-event tones past D");
        return 1;
    }
    for (Codec c : {Codec::Pcma, Codec::Pcmu}) {
        for (int v = -32768; v <= 32767; v++) {
            std::uint8_t b = G711Encode(c, static_cast<std::int16_t>(v));
            if (G711Encode(c, G711Decode(c, b)) != b) {
                std::println(std::cerr, "selftest FAIL {} not idempotent at {}", CodecName(c), v);
                return 1;
            }
        }
        double sig = 0;
        double err = 0;
        for (int i = 0; i < 8000; i++) {
            double x = 10000.0 * std::sin(2 * std::numbers::pi * 1000.0 * i / 8000.0);
            auto sample = static_cast<std::int16_t>(std::lround(x));
            std::int16_t d = G711Decode(c, G711Encode(c, sample));
            sig += x * x;
            err += (x - d) * (x - d);
        }
        double snr = 10 * std::log10(sig / err);
        if (snr < 30) {
            std::println(std::cerr, "selftest FAIL {} sine SNR {:.1f} dB", CodecName(c), snr);
            return 1;
        }
    }
    std::println("selftest OK");
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string_view(argv[1]) == "--selftest") return SelfTest();
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

    Codec codec = ParseCodec(EnvOr("CODEC", "AMR-WB"));
    bool amr = IsAmr(codec);
    int rate = CodecRate(codec);
    auto frameSamples = static_cast<std::size_t>(FrameSamples(codec));
    bool mic = EnvBool("MIC", false);
    bool play = EnvBool("PLAY", false);
    int amrMode = std::atoi(EnvOr("AMR_MODE", codec == Codec::AmrWb ? "2" : "7").c_str());
    // A mastered audio fixture already peaks close to full scale, while the
    // FP6 microphone path needs its own hardware-specific gain. Do not apply
    // the microphone boost to MIC_FILE: it clips before encoding.
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
    std::string micSrc = EnvOr("MIC_SRC", "");
    bool pcmDump = EnvBool("PCM_DUMP", false);
    const int dtmfPt = std::atoi(EnvOr("DTMF_PT", "0").c_str());
    const int dtmfRxPt = std::atoi(EnvOr("DTMF_RX_PT", EnvOr("DTMF_PT", "0").c_str()).c_str());
    constexpr int ExitMediaTimeout = 3;
    constexpr int PrimeFrames = 8;
    constexpr int MaxFill = 25;
    constexpr std::size_t PlayqMax = 256;

    int sock = socket(Is6(local) ? AF_INET6 : AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (sock < 0) {
        std::println(std::cerr, "imsd-media: socket failed");
        return 1;
    }
    int one = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_storage bindA;
    socklen_t bindL = MakeAddr(local, rtpPort, bindA);
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
    st.tsStep = static_cast<std::uint32_t>(frameSamples);
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
    st.latched = st.dst;
    st.latchedLen = st.dstLen;

    signal(SIGTERM, OnSig);
    signal(SIGINT, OnSig);
    signal(SIGPIPE, SIG_IGN);

    // ---- DTMF: the tones the control pipe (below) queues; the audio's
    // senders pay them out slot by slot
    DtmfSender dtmf;

    // ---- mic uplink thread: pw-record (or the MIC_SRC file, real-time paced)
    // through the codec's encoder, one RTP packet per 20 ms frame — or the
    // event packet whose slot it is
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
            if (amr && !enc.Open(codec, dtx)) {
                std::println(std::cerr, "imsd-media: mic: {} encoder unavailable; silence fallback", CodecName(codec));
                return;
            }
            int fd = -1;
            pid_t pid = -1;
            if (!micSrc.empty()) {
                fd = open(micSrc.c_str(), O_RDONLY);
                if (fd < 0) {
                    std::println(std::cerr, "imsd-media: mic: cannot open MIC_SRC {}", micSrc);
                    return;
                }
            } else {
                Child rec = SpawnPw(false, /*toChild=*/false, rate);
                if (rec.pid < 0) return;
                fd = rec.fd;
                pid = rec.pid;
            }
            std::vector<std::uint8_t> raw(frameSamples * 2);
            std::vector<std::int16_t> samples(frameSamples);
            auto next = Clock::now();
            while (!stop.load()) {
                if (!ReadExact(fd, raw.data(), raw.size())) {
                    std::println(std::cerr, "imsd-media: mic: {} EOF; silence fallback", pid > 0 ? "pw-record" : "MIC_SRC");
                    break;
                }
                if (pid < 0) {
                    // a file delivers instantly; pace it like a microphone
                    next += std::chrono::milliseconds(20);
                    std::this_thread::sleep_until(next);
                }
                st.micOn.store(true);
                std::memcpy(samples.data(), raw.data(), raw.size());
                if (gain != 1.0)
                    for (auto& sample : samples) {
                        int v = static_cast<int>(sample * gain);
                        sample = static_cast<std::int16_t>(v < -32768 ? -32768 : (v > 32767 ? 32767 : v));
                    }
                {
                    std::uint64_t sq = 0;
                    int pk = 0;
                    for (std::int16_t sample : samples) {
                        int v = sample;
                        sq += static_cast<std::uint64_t>(v) * static_cast<std::uint64_t>(v);
                        int a = v < 0 ? -v : v;
                        if (a > pk) pk = a;
                    }
                    st.micSumSq.fetch_add(sq, std::memory_order_relaxed);
                    st.micSamples.fetch_add(samples.size(), std::memory_order_relaxed);
                    int prev = st.micPeak.load(std::memory_order_relaxed);
                    while (pk > prev &&
                           !st.micPeak.compare_exchange_weak(prev, pk,
                                                             std::memory_order_relaxed)) {}
                }
                std::vector<std::uint8_t> payload;   // empty: a DTX gap, nothing to send in the slot
                if (amr) {
                    std::vector<std::uint8_t> frame = enc.Encode(samples.data(), amrMode, dtx);
                    if (!frame.empty()) payload = PayloadFromFrame(codec, frame, octetAlign);
                } else {
                    payload = G711Payload(codec, samples);
                }
                // the slot's packet: the event's, or this frame
                if (dtmf.SendSlot(sock, st, dtmfPt, payload) || payload.empty()) continue;
                { std::scoped_lock g(st.lock); st.txMic++; }
            }
            st.micOn.store(false);
            if (fd >= 0) close(fd);
            if (pid > 0) {
                // the helper tree has its own process group (SpawnPw)
                Child rec{pid, -1};
                StopChildGroup(rec);
            }
        });
    }

    // ---- downlink playout (decode + RTP-timestamp clock reconstruction).
    // Runs for pw-play (PLAY=1) and/or the PCM dump (PCM_DUMP=1).
    Decoder dec;
    Child playCh;
    bool playOn = false;
    bool decodeOn = false;
    if (play || pcmDump) {
        if (!amr || dec.Open(codec)) {
            if (play) {
                playCh = SpawnPw(true, /*toChild=*/true, rate);
                playOn = playCh.pid >= 0;
            }
            decodeOn = playOn || pcmDump;
        } else {
            std::println(std::cerr, "imsd-media: play: {} decoder unavailable; capture only", CodecName(codec));
        }
    }
    std::FILE* pcmFile = decodeOn && pcmDump ? std::fopen((std::format("{}.pcm", out)).c_str(), "wb") : nullptr;
    std::mutex qlock;
    std::condition_variable qcv;
    std::deque<PktItem> playq;
    std::uint64_t rxPlayed = 0;
    std::uint64_t cng = 0;
    std::uint64_t late = 0;
    std::uint64_t qdrop = 0;
    std::uint64_t rxDtmf = 0;    // telephone-event packets from the far end
    std::uint64_t tones = 0;     // events of theirs rendered as a tone
    std::jthread playThread;
    if (decodeOn) {
        playThread = std::jthread([&] {
            auto writePcm = [&](std::span<const std::int16_t> pcm) {
                if (pcmFile) std::fwrite(pcm.data(), 2, pcm.size(), pcmFile);
                if (!playOn) return true;
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
            auto applyGain = [&](std::vector<std::int16_t>& pcm) {
                if (playGain == 1.0) return;
                for (auto& sample : pcm) {
                    int v = static_cast<int>(sample * playGain);
                    sample = static_cast<std::int16_t>(v < -32768 ? -32768 : (v > 32767 ? 32767 : v));
                }
            };
            // Decoder state is time-ordered: the CNG fill for a gap must be
            // decoded BEFORE the frames that follow the gap, so depay first,
            // decode in playout order.
            auto decodeFrame = [&](std::span<const std::uint8_t> f) {
                return amr ? dec.Decode(f.data(), static_cast<int>(f.size())) : G711DecodeFrame(codec, f);
            };
            std::vector<std::int16_t> zero(frameSamples, 0);
            std::optional<std::uint32_t> expect;
            std::optional<std::uint32_t> toneTs;   // the event being rendered, by its start stamp
            for (;;) {
                PktItem item;
                {
                    std::unique_lock g(qlock);
                    qcv.wait(g, [&] { return !playq.empty() || stop.load(); });
                    if (playq.empty()) return;
                    item = std::move(playq.front());
                    playq.pop_front();
                }
                if (item.event) {
                    // The far end's key press, played as the tone it stands
                    // for (§2.5.2.2 leaves rendering to the receiver). The
                    // gap before the event's start — a stock sender's DTX
                    // silence often precedes a key press (dev phone,
                    // 2026-10-15: 0 to 7 slots) — is filled as before an
                    // audio frame, so the tone starts where the event does.
                    // Each packet reports the event's duration so far from
                    // its start stamp; what the clock has not covered yet is
                    // synthesized — a retransmitted end packet adds nothing,
                    // a lost update is caught up by the next. A far end
                    // whose audio resumes past the event (the stock phone's
                    // does) meets the clock with the usual fill.
                    if (item.payload.size() < 4) continue;
                    const auto freqs = DtmfFrequencies(item.payload[0]);
                    if (!freqs) continue;
                    const std::uint32_t dur = (static_cast<std::uint32_t>(item.payload[2]) << 8) | item.payload[3];
                    const double amp = 16383.0 * std::pow(10.0, -(item.payload[1] & 0x3F) / 20.0);
                    if (!expect) {
                        for (int i = 0; i < PrimeFrames; i++)
                            if (!writePcm(zero)) return;
                        expect = item.ts;
                    }
                    const std::uint32_t before = (item.ts - *expect) & 0xFFFFFFFF;
                    if (before < 0x80000000u) {
                        int fill = static_cast<int>(before / frameSamples);
                        if (fill > MaxFill) fill = 0;
                        for (int i = 0; i < fill; i++) {
                            cng++;
                            std::uint8_t nodata = 0x7C;
                            std::vector<std::int16_t> pcm = amr ? dec.Decode(&nodata, 1) : zero;
                            applyGain(pcm);
                            if (!writePcm(pcm)) return;
                        }
                        expect = item.ts;
                    }
                    const std::uint32_t end = (item.ts + dur) & 0xFFFFFFFF;
                    const std::uint32_t ahead = (end - *expect) & 0xFFFFFFFF;
                    if (ahead == 0 || ahead > static_cast<std::uint32_t>(2 * rate)) continue;   // covered, or not a tone's length
                    if (!toneTs || *toneTs != item.ts) { toneTs = item.ts; tones++; }
                    const std::uint32_t from = (*expect - item.ts) & 0xFFFFFFFF;
                    std::vector<std::int16_t> pcm(ahead);
                    for (std::uint32_t i = 0; i < ahead; i++)
                        pcm[i] = DtmfSample(*freqs, from + i, rate, amp);
                    applyGain(pcm);
                    if (!writePcm(pcm)) return;
                    expect = end;
                    continue;
                }
                std::vector<std::vector<std::uint8_t>> frames;
                if (amr) {
                    for (auto& [hdr, speech] : Depay(codec, item.payload, octetAlign)) {
                        std::vector<std::uint8_t> f = {hdr};
                        f.insert(f.end(), speech.begin(), speech.end());
                        frames.push_back(std::move(f));
                    }
                } else {
                    // whole frames back to back (a gateway may pack 2 at ptime 40)
                    for (std::size_t off = 0; off + frameSamples <= item.payload.size(); off += frameSamples)
                        frames.emplace_back(item.payload.begin() + static_cast<std::ptrdiff_t>(off), item.payload.begin() + static_cast<std::ptrdiff_t>(off + frameSamples));
                }
                if (frames.empty()) continue;
                int fill = 0;
                if (!expect) {
                    for (int i = 0; i < PrimeFrames; i++)
                        if (!writePcm(zero)) return;
                } else {
                    std::uint32_t diff = (item.ts - *expect) & 0xFFFFFFFF;
                    if (diff >= 0x80000000u) {
                        late++;
                        continue;
                    }
                    fill = static_cast<int>(diff / frameSamples);
                    if (fill > MaxFill) fill = 0;
                }
                for (int i = 0; i < fill; i++) {
                    cng++;
                    std::uint8_t nodata = 0x7C;
                    std::vector<std::int16_t> pcm = amr ? dec.Decode(&nodata, 1) : zero;
                    applyGain(pcm);
                    if (!writePcm(pcm)) return;
                }
                for (auto& f : frames) {
                    rxPlayed++;
                    std::vector<std::int16_t> pcm = decodeFrame(f);
                    applyGain(pcm);
                    if (!writePcm(pcm)) return;
                }
                expect = (item.ts + static_cast<std::uint32_t>(frameSamples * frames.size())) & 0xFFFFFFFF;
            }
        });
    }

    // ---- the control pipe that queues the tones: the daemon's
    // `dtmf <tones>` lines on stdin. No pipe (a hand run, /dev/null) ends
    // the reader at once; the leg runs on.
    std::jthread ctlThread([&] {
        // a hand run in the background of a job-control shell: a read of
        // its terminal would stop the whole process (SIGTTIN); blocked for
        // this thread, the read fails with EIO and the reader ends
        sigset_t ttin;
        sigemptyset(&ttin);
        sigaddset(&ttin, SIGTTIN);
        pthread_sigmask(SIG_BLOCK, &ttin, nullptr);
        std::string pending;
        bool discarding = false;   // an overlong line is dropped through its end
        char buf[256];
        while (!stop.load()) {
            pollfd pfd{STDIN_FILENO, POLLIN, 0};
            if (poll(&pfd, 1, 100) <= 0) continue;
            const ssize_t n = read(STDIN_FILENO, buf, sizeof buf);
            if (n <= 0) return;
            std::string_view in(buf, static_cast<std::size_t>(n));
            if (discarding) {
                const std::size_t end = in.find('\n');
                if (end == std::string_view::npos) continue;
                in.remove_prefix(end + 1);
                discarding = false;
            }
            pending.append(in);
            // a line of more than 4096 bytes with no end in sight: dropped
            // through its end; the next line goes
            if (pending.size() > 4096 && pending.find('\n') == std::string::npos) {
                std::println(std::cerr, "imsd-media: control line of {} bytes without an end dropped", pending.size());
                pending.clear();
                discarding = true;
                continue;
            }
            std::size_t nl;
            while ((nl = pending.find('\n')) != std::string::npos) {
                const std::string line = pending.substr(0, nl);
                pending.erase(0, nl + 1);
                // the same bound on a line whose end came in the read that
                // took it past 4096 bytes
                if (nl > 4096) {
                    std::println(std::cerr, "imsd-media: control line of {} bytes dropped", nl);
                    continue;
                }
                if (!line.starts_with("dtmf ")) {
                    std::println(std::cerr, "imsd-media: control line ignored: {}", line);
                } else if (dtmfPt > 0) {
                    dtmf.Queue(std::string_view(line).substr(5));
                } else {
                    std::println(std::cerr, "imsd-media: {} dropped: no telephone-event payload type (DTMF_PT)", line);
                }
            }
        }
    });

    // ---- main recv loop
    // FT=15 is the RFC 4867 NO_DATA frame. A zero-filled FT=0 speech frame
    // decodes as harsh synthetic noise and was audible during early media.
    // G.711 has no NO_DATA frame; SilenceFrame gives it digital zero.
    std::vector<std::uint8_t> silence = SilenceFrame(codec, 15, octetAlign);
    for (int i = 0; i < 5; i++) dtmf.SendSlot(sock, st, dtmfPt, silence);  // latch burst, the one path

    std::FILE* dump = rtpDump ? std::fopen((std::format("{}.rtp", out)).c_str(), "wb") : nullptr;
    double t0 = Now();
    double lastTx = 0;
    double lastRx = Now();
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
            // no mic: the slot's packet is the event's, or a silence frame
            dtmf.SendSlot(sock, st, dtmfPt, silence);
            lastTx = now;
        }
        std::uint8_t buf[65535];
        sockaddr_storage src;
        socklen_t srcLen = sizeof src;
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
        // the far end's events arrive at the payload type our own SDP gave
        // telephone-event (DTMF_RX_PT), or at the one we send with when the
        // answerer numbers for itself — never at the audio's: a frame is
        // audio first
        const int rxPt = n >= 12 ? buf[1] & 0x7F : -1;
        const bool isEvent = n >= 12 && rxPt != pt && ((dtmfRxPt > 0 && rxPt == dtmfRxPt) || (dtmfPt > 0 && rxPt == dtmfPt));
        if (isEvent) rxDtmf++;
        if (playThread.joinable() && n >= 12 && (rxPt == pt || isEvent)) {
            std::size_t off = RtpPayloadOffset(std::span(buf, static_cast<std::size_t>(n)));
            std::uint32_t pktTs = (static_cast<std::uint32_t>(buf[4]) << 24) |
                                  (buf[5] << 16) | (buf[6] << 8) | buf[7];
            std::scoped_lock g(qlock);
            if (playq.size() >= PlayqMax) {
                playq.pop_front();
                qdrop++;
            }
            playq.push_back({pktTs, std::vector<std::uint8_t>(buf + off, buf + n), isEvent});
            qcv.notify_one();
        }
    }

    stop.store(true);
    qcv.notify_all();
    if (ctlThread.joinable()) ctlThread.join();
    if (micThread.joinable()) micThread.join();
    if (playThread.joinable()) playThread.join();
    if (dump) std::fclose(dump);
    if (pcmFile) std::fclose(pcmFile);
    if (playOn) {
        StopChildGroup(playCh);
    }
    close(sock);
    if (rtcpSock >= 0) close(rtcpSock);

    // .stats sidecar (tiny; always written)
    if (std::FILE* sf = std::fopen((std::format("{}.stats", out)).c_str(), "w")) {
        std::print(sf,
            "{{\"codec\": \"{}\", \"tx\": {}, \"tx_mic\": {}, \"tx_dtmf\": {}, \"rx\": {}, \"rx_bytes\": {}, "
            "\"rx_played\": {}, \"rx_dtmf\": {}, \"tones\": {}, \"cng\": {}, \"late\": {}, \"qdrop\": {}, "
            "\"first_src\": \"{}\", \"dst\": \"{}:{}\", \"pt\": {}, \"dtmf_pt\": {}, \"dtmf_rx_pt\": {}, \"mic\": {}, "
            "\"play\": {}, \"amr_mode\": {}, \"media_ended\": {}}}",
            CodecName(codec), st.tx, st.txMic, st.txDtmf, rx, rxBytes, rxPlayed, rxDtmf, tones, cng, late, qdrop, firstSrc,
            rIp, rPort, pt, dtmfPt, dtmfRxPt, mic, play, amrMode, mediaEnded);
        std::fclose(sf);
    }
    std::uint64_t micN = st.micSamples.load();
    double micRms = micN ? std::sqrt(static_cast<double>(st.micSumSq.load()) /
                                     static_cast<double>(micN))
                         : 0.0;
    std::println("imsd-media: codec={} tx={} tx_mic={} tx_dtmf={} rx={} rx_played={} rx_dtmf={} tones={} cng={} late={} " "qdrop={} rx_bytes={} first_src={} media_ended={} mic_rms={:.1f} mic_peak={} mic_dbfs={:.1f}", CodecName(codec), st.tx, st.txMic, st.txDtmf, rx, rxPlayed, rxDtmf, tones, cng, late, qdrop, rxBytes, firstSrc, mediaEnded, micRms, st.micPeak.load(), micRms > 0 ? 20.0 * std::log10(micRms / 32768.0) : -99.0);
    return mediaEnded ? ExitMediaTimeout : 0;
}
