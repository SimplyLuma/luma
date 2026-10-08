// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types no-char-pointer
/*
imsd — the userspace IMS/VoLTE daemon (control plane).

Owns net.catcrafts.IMS1 on the system bus with the project's frozen D-Bus ABI
(README) and drives it with the engine core: register on the IMS PDN (USIM AKA
+ IPsec sec-agree, fresh or warm-resume, with a keepalive re-REGISTER refresh),
run outgoing and incoming calls through the Imsd:Engine call machine (inbound
INVITEs on the protected-port listeners become ringing calls a dialer answers
via Accept), and spawn one imsd-media data-plane process per answered call.
GLib/GDBus lives only here; every message, digest, SA command, and call-state
decision comes from the GLib-free core.

Threads: a dedicated engine thread runs the SIP loop (blocking framed recv,
protected-port listeners, media reaping, keepalive); the GLib main loop owns
the bus. D-Bus method calls push commands to the engine queue; engine events
reach the bus via g_idle_add, where the calls table + status snapshot live
(touched only on the main thread).

  --session   own the name on the session bus (development; no policy needed)
*/

#include <arpa/inet.h>
#include <fcntl.h>
#include <gio/gio.h>
#include <glib-unix.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

import std;
import Imsd;

namespace {

// ---- static config (env-overridable, same knobs as imsd.py) ---------------
constexpr const char* Version = "0.3.0";
constexpr const char* BusName = "net.catcrafts.IMS1";
constexpr const char* ObjPath = "/net/catcrafts/IMS1";
constexpr const char* Iface = "net.catcrafts.IMS1";
constexpr int PortUc = imsd::util::PortUc;
constexpr int PortUs = imsd::util::PortUs;
constexpr int InitPort = 5060;

std::string EnvOr(const char* k, std::string d) {
    const char* v = std::getenv(k);
    return v ? std::string(v) : std::move(d);
}

std::string SelfDir;  // dir of argv[0], for locating imsd-media

void Log(std::string_view m) { std::println("imsd: {}", m); std::fflush(stdout); }

bool Is6(std::string_view a) { return a.contains(':'); }

// ---- subprocess exec ------------------------------------------------------

struct ProcResult { int rc = -1; std::string out; };

// Run argv, capture stdout (stderr discarded), wait. No shell.
ProcResult RunCapture(const std::vector<std::string>& argv) {
    int pipefd[2];
    if (pipe(pipefd) != 0) return {};
    pid_t pid = fork();
    if (pid == 0) {
        dup2(pipefd[1], STDOUT_FILENO);
        close(pipefd[0]); close(pipefd[1]);
        int dn = open("/dev/null", O_WRONLY);
        if (dn >= 0) { dup2(dn, STDERR_FILENO); close(dn); }
        std::vector<char*> c;
        for (auto& s : argv) c.push_back(const_cast<char*>(s.c_str()));
        c.push_back(nullptr);
        execvp(c[0], c.data());
        _exit(127);
    }
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return {}; }
    close(pipefd[1]);
    std::string out;
    char buf[4096];
    ssize_t n;
    while ((n = read(pipefd[0], buf, sizeof buf)) > 0)
        out.append(buf, static_cast<std::size_t>(n));
    close(pipefd[0]);
    int status = 0;
    waitpid(pid, &status, 0);
    return {WIFEXITED(status) ? WEXITSTATUS(status) : -1, std::move(out)};
}

// Run argv, discard output, return exit code.
int RunCmd(const std::vector<std::string>& argv) { return RunCapture(argv).rc; }

// ---- small text extractors (401 header params, qmicli "completed:") -------

std::optional<std::string> QuotedAfter(std::string_view text, std::string_view key) {
    std::size_t at = text.find(key);
    if (at == std::string_view::npos) return std::nullopt;
    std::size_t start = at + key.size();
    std::size_t end = text.find('"', start);
    if (end == std::string_view::npos) return std::nullopt;
    return std::string(text.substr(start, end - start));
}

std::optional<long> IntAfter(std::string_view text, std::string_view key) {
    std::size_t at = text.find(key);
    if (at == std::string_view::npos) return std::nullopt;
    std::size_t i = at + key.size();
    std::size_t start = i;
    while (i < text.size() && text[i] >= '0' && text[i] <= '9') i++;
    if (i == start) return std::nullopt;
    long v = 0;
    std::from_chars(text.data() + start, text.data() + i, v);
    return v;
}

std::string EalgAfter(std::string_view ss) {
    std::size_t at = ss.find("ealg=");
    if (at == std::string_view::npos) return "null";
    std::size_t i = at + 5;
    std::size_t start = i;
    auto ok = [](char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-'; };
    while (i < ss.size() && ok(ss[i])) i++;
    return std::string(ss.substr(start, i - start));
}

// value after "completed:" — integer or the hex ("AB:CD:..") token
std::optional<long> CompletedInt(std::string_view out) {
    return IntAfter(out, "completed: ");
}
std::optional<std::string> CompletedHex(std::string_view out) {
    std::size_t at = out.find("completed:");
    if (at == std::string_view::npos) return std::nullopt;
    std::size_t i = at + 10;
    while (i < out.size() && (out[i] == ' ' || out[i] == '\t')) i++;
    std::size_t start = i;
    auto ok = [](char c) {
        return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'F') ||
               (c >= 'a' && c <= 'f') || c == ':';
    };
    while (i < out.size() && ok(out[i])) i++;
    if (i == start) return std::nullopt;
    std::string s(out.substr(start, i - start));
    std::string h;
    for (char c : s) if (c != ':') h.push_back(static_cast<char>(std::tolower(c)));
    return h;
}

// global IPv6 on the ims PDN, from `ip -6 addr show dev <dev> scope global`.
std::optional<std::string> DetectLocal(const std::string& dev) {
    auto r = RunCapture({"ip", "-6", "addr", "show", "dev", dev, "scope", "global"});
    std::size_t at = r.out.find("inet6 ");
    if (at == std::string_view::npos) return std::nullopt;
    std::size_t start = at + 6;
    std::size_t end = r.out.find('/', start);
    if (end == std::string_view::npos) return std::nullopt;
    return r.out.substr(start, end - start);
}

// ---- sockaddr helper ------------------------------------------------------
socklen_t MakeAddr(std::string_view ip, int port, sockaddr_storage& ss) {
    std::memset(&ss, 0, sizeof ss);
    std::string s(ip);
    if (Is6(ip)) {
        auto* a = reinterpret_cast<sockaddr_in6*>(&ss);
        a->sin6_family = AF_INET6; a->sin6_port = htons(static_cast<uint16_t>(port));
        inet_pton(AF_INET6, s.c_str(), &a->sin6_addr);
        return sizeof(sockaddr_in6);
    }
    auto* a = reinterpret_cast<sockaddr_in*>(&ss);
    a->sin_family = AF_INET; a->sin_port = htons(static_cast<uint16_t>(port));
    inet_pton(AF_INET, s.c_str(), &a->sin_addr);
    return sizeof(sockaddr_in);
}

// ---- SIP over the protected TCP flow --------------------------------------
class SipTcp {
public:
    bool Connect(const std::string& local, int lport, const std::string& pcscf, int pport, int tries = 10) {
        int fam = Is6(local) ? AF_INET6 : AF_INET;
        for (int attempt = 0; attempt < tries; attempt++) {
            int fd = socket(fam, SOCK_STREAM | SOCK_CLOEXEC, 0);
            if (fd < 0) return false;
            int one = 1;
            setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
            // Bound how long an apparently successful write may remain queued
            // after the P-CSCF has silently discarded the protected flow.
            // Without this, Send() can report success for minutes and the call
            // state machine waits for SIP responses which can never arrive.
            int userTimeoutMs = 15000;
            setsockopt(fd, IPPROTO_TCP, TCP_USER_TIMEOUT,
                       &userTimeoutMs, sizeof userTimeoutMs);
            // REUSEPORT (paired with the listener's): a reconnect must bind
            // the protected client port while the ServerPorts listener holds
            // it in LISTEN state — REUSEADDR alone only covers TIME_WAIT.
            setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &one, sizeof one);
            sockaddr_storage la; socklen_t ll = MakeAddr(local, lport, la);
            if (bind(fd, reinterpret_cast<sockaddr*>(&la), ll) != 0) { close(fd); return false; }
            sockaddr_storage pa; socklen_t pl = MakeAddr(pcscf, pport, pa);
            // non-blocking connect + poll: Linux connect() ignores SO_SNDTIMEO,
            // so bound it ourselves (a silent P-CSCF must not hang the engine).
            fcntl(fd, F_SETFL, O_NONBLOCK);
            int rc = connect(fd, reinterpret_cast<sockaddr*>(&pa), pl);
            bool connected = (rc == 0);
            if (rc < 0 && errno == EINPROGRESS) {
                pollfd p{fd, POLLOUT, 0};
                if (poll(&p, 1, 12000) > 0) {
                    int err = 0; socklen_t el = sizeof err;
                    getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &el);
                    connected = (err == 0);
                }
            }
            if (connected) {
                fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) & ~O_NONBLOCK);
                fd_ = fd; alive_ = true; fb_ = {};
                return true;
            }
            close(fd);
            if (attempt == tries - 1) return false;
            Log(std::format("connect failed; retry in 10s (TIME_WAIT?) [{}/{}]", attempt + 1, tries));
            sleep(10);
        }
        return false;
    }
    // MSG_NOSIGNAL: a flow the network killed (CSFB excursion, P-CSCF idle
    // drop) must surface as a send error, not a SIGPIPE.
    bool Send(std::string_view msg) {
        std::size_t off = 0;
        while (off < msg.size()) {
            ssize_t w = send(fd_, msg.data() + off, msg.size() - off, MSG_NOSIGNAL);
            if (w <= 0) { alive_ = false; return false; }
            off += static_cast<std::size_t>(w);
        }
        return true;
    }
    void SendKeepalive() {
        if (send(fd_, "\r\n\r\n", 4, MSG_NOSIGNAL) <= 0) alive_ = false;
    }

    std::optional<std::string> RecvMsg(double timeoutSec) {
        auto deadline = std::chrono::steady_clock::now() +
            std::chrono::duration_cast<std::chrono::steady_clock::duration>(std::chrono::duration<double>(timeoutSec));
        for (;;) {
            if (auto m = fb_.TryExtract()) return m;
            auto now = std::chrono::steady_clock::now();
            if (now >= deadline) return std::nullopt;
            int ms = static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now).count());
            pollfd p{fd_, POLLIN, 0};
            int r = poll(&p, 1, ms);
            if (r <= 0) { if (r == 0) return std::nullopt; if (errno == EINTR) continue; return std::nullopt; }
            char buf[65535];
            ssize_t n = recv(fd_, buf, sizeof buf, 0);
            // EOF/RST is not a timeout: mark the flow dead so the engine's
            // reconnect path notices within one loop turn instead of at the
            // next (possibly 30-min-away) keepalive refresh.
            if (n <= 0) { alive_ = false; return std::nullopt; }
            fb_.Append(std::string_view(buf, static_cast<std::size_t>(n)));
        }
    }
    int Fd() const { return fd_; }
    bool Alive() const { return fd_ >= 0 && alive_; }
    void Close() { if (fd_ >= 0) { close(fd_); fd_ = -1; } alive_ = false; }
private:
    int fd_ = -1;
    bool alive_ = false;
    imsd::sip::FrameBuffer fb_;
};

// ---- protected-port listeners (terminating requests: remote BYE/UPDATE) ---
struct Inbound {
    std::string msg;
    // Returning the socket result matters for terminating-call diagnostics:
    // constructing a SIP response is not evidence that it left the device.
    std::function<bool(const std::string&)> reply;
    std::string replyPath;
};

class ServerPorts {
public:
    void Open(const std::string& local) {
        int fam = Is6(local) ? AF_INET6 : AF_INET;
        for (int port : {PortUc, PortUs}) {
            int ls = socket(fam, SOCK_STREAM | SOCK_CLOEXEC, 0);
            if (ls >= 0) {
                int one = 1;
                setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
                // paired with SipTcp::Connect's REUSEPORT (reconnect binds
                // the client port while this listener holds it)
                setsockopt(ls, SOL_SOCKET, SO_REUSEPORT, &one, sizeof one);
                sockaddr_storage a; socklen_t l = MakeAddr(local, port, a);
                if (bind(ls, reinterpret_cast<sockaddr*>(&a), l) == 0 && listen(ls, 4) == 0) {
                    fcntl(ls, F_SETFL, O_NONBLOCK);
                    listeners_.push_back(ls);
                } else { close(ls); }
            }
            int us = socket(fam, SOCK_DGRAM | SOCK_CLOEXEC, 0);
            if (us >= 0) {
                int one = 1;
                setsockopt(us, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
                sockaddr_storage a; socklen_t l = MakeAddr(local, port, a);
                if (bind(us, reinterpret_cast<sockaddr*>(&a), l) == 0) {
                    fcntl(us, F_SETFL, O_NONBLOCK);
                    udp_.push_back(us);
                    if (port == PortUc) udpUc_ = us;
                    if (port == PortUs) udpUs_ = us;
                } else { close(us); }
            }
            Log(std::format("listening on protected port {} (TCP+UDP)", port));
        }
    }

    std::vector<Inbound> Poll() {
        std::vector<Inbound> out;
        for (int ls : listeners_) {
            for (;;) {
                int c = accept4(ls, nullptr, nullptr, SOCK_CLOEXEC);
                if (c < 0) break;
                fcntl(c, F_SETFL, O_NONBLOCK);
                conns_.push_back({c, {}});
            }
        }
        for (int us : udp_) {
            for (;;) {
                char buf[65535];
                sockaddr_storage src; socklen_t sl = sizeof src;
                ssize_t n = recvfrom(us, buf, sizeof buf, 0, reinterpret_cast<sockaddr*>(&src), &sl);
                if (n <= 0) break;
                std::string_view sv(buf, static_cast<std::size_t>(n));
                bool blank = sv.find_first_not_of("\r\n \t") == std::string_view::npos;
                if (blank) continue;
                std::string msg(sv);
                {
                    sockaddr_storage s = src; socklen_t sll = sl;
                    int rs = us;
                    // RFC 3261 18.2.2: rport-less UDP requests get their
                    // response at the Via sent-by port, not the source
                    // port. KPN's P-CSCF sends in-dialog requests from its
                    // protected client port but silently drops responses
                    // sent back there (s57: every UPDATE/BYE 200 ignored,
                    // audit kills the call); the Via port is its protected
                    // server port, whose SA pairs with OUR client-port
                    // socket — respond from that one so the uc->ps SA
                    // carries it.
                    if (auto vp = imsd::sip::ViaResponsePort(msg)) {
                        if (s.ss_family == AF_INET6)
                            reinterpret_cast<sockaddr_in6*>(&s)->sin6_port =
                                htons(static_cast<uint16_t>(*vp));
                        else
                            reinterpret_cast<sockaddr_in*>(&s)->sin_port =
                                htons(static_cast<uint16_t>(*vp));
                        if (udpUc_ >= 0) rs = udpUc_;
                    }
                    out.push_back({std::move(msg), [rs, s, sll](const std::string& r) mutable {
                        ssize_t written = sendto(rs, r.data(), r.size(), 0,
                                                 reinterpret_cast<sockaddr*>(&s), sll);
                        return written == static_cast<ssize_t>(r.size());
                    }, "protected-udp"});
                }
            }
        }
        for (auto it = conns_.begin(); it != conns_.end();) {
            bool eof = false;
            for (;;) {
                char buf[65535];
                ssize_t n = recv(it->fd, buf, sizeof buf, 0);
                if (n == 0) { eof = true; break; }
                if (n < 0) break;
                it->fb.Append(std::string_view(buf, static_cast<std::size_t>(n)));
            }
            while (auto m = it->fb.TryExtract()) {
                // Some IMS cores, including T-Mobile US, return responses to
                // the protected Via port on a separately accepted flow rather
                // than the TCP connection used to send the request. Preserve
                // responses here; the engine loop matches them by Call-ID and
                // CSeq just like responses read from the client flow.
                int fd = it->fd;
                out.push_back({*m, [fd](const std::string& r) {
                    std::size_t off = 0;
                    while (off < r.size()) {
                        ssize_t written = send(fd, r.data() + off, r.size() - off,
                                               MSG_NOSIGNAL);
                        if (written <= 0) return false;
                        off += static_cast<std::size_t>(written);
                    }
                    return true;
                }, "protected-tcp"});
            }
            if (eof) { close(it->fd); it = conns_.erase(it); } else { ++it; }
        }
        return out;
    }

    void PrimeServerSa(const std::string& pcscf, int portPc) {
        if (udpUs_ < 0 || portPc <= 0) return;
        sockaddr_storage peer;
        socklen_t length = MakeAddr(pcscf, portPc, peer);
        static constexpr std::string_view keepalive = "\r\n";
        (void)sendto(udpUs_, keepalive.data(), keepalive.size(), 0,
                     reinterpret_cast<sockaddr*>(&peer), length);
    }

    void CloseAll() {
        for (int f : listeners_) close(f);
        for (int f : udp_) close(f);
        for (auto& c : conns_) close(c.fd);
        listeners_.clear(); udp_.clear(); conns_.clear();
    }
private:
    struct Conn { int fd; imsd::sip::FrameBuffer fb; };
    int udpUc_ = -1;
    int udpUs_ = -1;
    std::vector<int> listeners_, udp_;
    std::vector<Conn> conns_;
};

// ---- registration state ---------------------------------------------------
struct RegState {
    std::string callid, ftag;
    long cseq = 2;
    std::uint32_t spiUc = 0;
    std::uint32_t spiUs = 0;
    long expiry = 0;
    int slot = 0;
    std::string aid;
    // Contact user-part of the stored binding (UUID since the s53 oracle
    // diff; empty = legacy IMSI binding from an older state file).
    std::string contactUser;
};

// Writable directory for the daemon's own files: the persisted registration
// context, media stats, and optional debug dumps. Root daemon (the packaged
// service): /var/lib/imsd; --session/dev runs: the XDG state dir.
std::string StateDir() {
    if (geteuid() == 0) return "/var/lib/imsd";
    if (const char* x = std::getenv("XDG_STATE_HOME")) return std::format("{}/imsd", std::string(x));
    return std::format("{}/.local/state/imsd", EnvOr("HOME", "/tmp"));
}

// tiny JSON reader/writer for the persisted registration context.
std::string StatePath() { return EnvOr("STATE_FILE", std::format("{}/imsreg.state", StateDir())); }

void PersistState(const RegState& r, const std::string& route, const std::string& ppi) {
    std::string j = std::format(
        "{{\"callid\": \"{}\", \"ftag\": \"{}\", \"cseq\": {}, \"spi_uc\": {}, "
        "\"spi_us\": {}, \"expiry\": {}, \"route\": \"{}\", \"ppi\": \"{}\", "
        "\"contact_user\": \"{}\"}}",
        r.callid, r.ftag, r.cseq, r.spiUc, r.spiUs, r.expiry, route, ppi,
        r.contactUser);
    std::string path = StatePath();
    std::error_code ec;
    std::filesystem::create_directories(std::filesystem::path(path).parent_path(), ec);
    if (std::FILE* f = std::fopen(path.c_str(), "w")) {
        std::fwrite(j.data(), 1, j.size(), f);
        std::fclose(f);
    }
}

// Raw dump of a received message for offline diffing against the stock-modem
// oracle (rung-5b registration-parity work). One file per message kind,
// overwritten on every registration cycle. The dumps carry the subscriber's
// IMSI/MSISDN and addresses, so they are opt-in (DUMP_SIP=1) and mode 0600.
void DumpRaw(std::string_view name, std::string_view msg) {
    if (EnvOr("DUMP_SIP", "0") != "1") return;
    std::string path = std::format("{}/{}", EnvOr("DUMP_DIR", StateDir()), name);
    int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) return;
    (void)!write(fd, msg.data(), msg.size());
    close(fd);
}

struct PersistedState {
    std::optional<std::string> callid, ftag, route, ppi, contactUser;
    std::optional<long> cseq, spiUc, spiUs, expiry;
};
std::optional<PersistedState> LoadState() {
    std::FILE* f = std::fopen(StatePath().c_str(), "r");
    if (!f) return std::nullopt;
    std::string s;
    char buf[1024]; std::size_t n;
    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) s.append(buf, n);
    std::fclose(f);
    PersistedState ps;
    ps.callid = QuotedAfter(s, "\"callid\": \"");
    ps.ftag = QuotedAfter(s, "\"ftag\": \"");
    ps.route = QuotedAfter(s, "\"route\": \"");
    ps.ppi = QuotedAfter(s, "\"ppi\": \"");
    ps.contactUser = QuotedAfter(s, "\"contact_user\": \"");
    ps.cseq = IntAfter(s, "\"cseq\": ");
    ps.spiUc = IntAfter(s, "\"spi_uc\": ");
    ps.spiUs = IntAfter(s, "\"spi_us\": ");
    ps.expiry = IntAfter(s, "\"expiry\": ");
    return ps;
}

// ---- daemon-level shared state (main thread only) -------------------------
struct CallInfo {
    std::string uni, number, state, reason;
    std::int64_t startedAt = 0;
    std::int64_t answeredAt = 0;
    std::string direction = "outgoing";
};
struct StatusSnapshot {
    bool registered = false;
    std::string local, pcscf, ppi;
    std::int64_t expiry = 0;
    std::int64_t cseq = 0;
};

// engine -> main-loop event
struct Event {
    enum class Type { Added, State, Deleted, Registration, Status, Fatal } type;
    CallInfo info;                 // Added
    std::string uni, state, reason;  // State/Deleted
    StatusSnapshot status;         // Registration/Status
    std::string text;              // Fatal
};

GDBusConnection* DbusConn = nullptr;
std::map<std::string, CallInfo> Calls;   // main thread only
// Calls created through the restricted pre-session ABI.  The greeter can act
// only on these opaque identifiers, so it cannot inspect or hang up a user's
// ordinary call.  Final state is retained for the lifetime of this daemon so
// the locked UI can observe a clean return after CallDeleted.
std::set<std::string> EmergencyCalls;          // main thread only
std::map<std::string, std::string> EmergencyFinalReasons; // main thread only
StatusSnapshot StatusSnap;                    // main thread only
GMainLoop* MainLoop = nullptr;
bool FatalExit = false;                       // main thread only

std::int64_t NowEpoch() {
    return std::chrono::duration_cast<std::chrono::seconds>(std::chrono::system_clock::now().time_since_epoch()).count();
}

// build an a{sv} from a CallInfo (matches imsd.py _d typing)
GVariant* CallVariant(const CallInfo& c) {
    GVariantBuilder b;
    g_variant_builder_init(&b, G_VARIANT_TYPE("a{sv}"));
    g_variant_builder_add(&b, "{sv}", "uni", g_variant_new_string(c.uni.c_str()));
    g_variant_builder_add(&b, "{sv}", "number", g_variant_new_string(c.number.c_str()));
    g_variant_builder_add(&b, "{sv}", "state", g_variant_new_string(c.state.c_str()));
    g_variant_builder_add(&b, "{sv}", "reason", g_variant_new_string(c.reason.c_str()));
    g_variant_builder_add(&b, "{sv}", "startedAt", g_variant_new_int64(c.startedAt));
    g_variant_builder_add(&b, "{sv}", "answeredAt", g_variant_new_int64(c.answeredAt));
    g_variant_builder_add(&b, "{sv}", "direction", g_variant_new_string(c.direction.c_str()));
    return g_variant_builder_end(&b);
}

void EmitSignal(const char* name, GVariant* params) {
    if (!DbusConn) return;
    g_dbus_connection_emit_signal(DbusConn, nullptr, ObjPath, Iface, name, params, nullptr);
}

gboolean OnIdleEvent(gpointer data) {
    std::unique_ptr<Event> ev(static_cast<Event*>(data));
    switch (ev->type) {
        case Event::Type::Added: {
            Calls[ev->info.uni] = ev->info;
            EmitSignal("CallAdded", g_variant_new("(s@a{sv})", ev->info.uni.c_str(), CallVariant(ev->info)));
            break;
        }
        case Event::Type::State: {
            auto it = Calls.find(ev->uni);
            if (it != Calls.end()) {
                it->second.state = ev->state;
                it->second.reason = ev->reason;
                if (ev->state == "active" && it->second.answeredAt == 0) it->second.answeredAt = NowEpoch();
            }
            if (EmergencyCalls.contains(ev->uni) && ev->state == "terminated")
                EmergencyFinalReasons[ev->uni] = ev->reason;
            EmitSignal("CallStateChanged", g_variant_new("(sss)", ev->uni.c_str(), ev->state.c_str(), ev->reason.c_str()));
            break;
        }
        case Event::Type::Deleted:
            Calls.erase(ev->uni);
            EmitSignal("CallDeleted", g_variant_new("(s)", ev->uni.c_str()));
            break;
        case Event::Type::Registration:
            StatusSnap = ev->status;
            EmitSignal("RegistrationChanged", g_variant_new("(b)", ev->status.registered ? TRUE : FALSE));
            break;
        case Event::Type::Status:
            StatusSnap = ev->status;
            break;
        case Event::Type::Fatal:
            Log(std::format("engine fatal: {} — exiting for systemd restart", ev->text));
            FatalExit = true;
            g_main_loop_quit(MainLoop);
            break;
    }
    return G_SOURCE_REMOVE;
}

void PostEvent(std::unique_ptr<Event> ev) {
    g_idle_add(OnIdleEvent, ev.release());
}

// ================= engine ==================================================
class Engine {
public:
    Engine() {
        // No baked-in default: the P-CSCF is carrier infrastructure, learned
        // from the IMS PDN's PCO on a stock stack. Until PCO discovery is
        // implemented, PCSCF= must be configured (see README).
        pcscf_ = EnvOr("PCSCF", "");
        pcscfPort_ = std::atoi(EnvOr("PCSCF_PORT", "5060").c_str());
        dev_ = EnvOr("DEV", "qmapmux0.0");
        outDir_ = EnvOr("OUT_DIR", StateDir());
        rtpPort_ = std::atoi(EnvOr("RTP_PORT", "50004").c_str());
        precond_ = EnvOr("PRECOND", "0") == "1";
        // Diagnostic only: answer a terminating INVITE in the same engine
        // turn, before a carrier voicemail fork can enqueue CANCEL. Normal
        // Luma sessions leave this off and answer through the D-Bus UI.
        autoAnswer_ = EnvOr("AUTO_ANSWER", "0") == "1";
        ealgOffer_ = EnvOr("EALG", "aes-cbc");
        mediaBin_ = ResolveMediaBin();
        // EMERGENCY_NUMBERS: comma-separated additions to the builtin
        // 112/911 (a lab core's advertised short code, carrier extras).
        std::string extra = EnvOr("EMERGENCY_NUMBERS", "");
        for (std::size_t pos = 0; pos <= extra.size();) {
            std::size_t comma = extra.find(',', pos);
            std::size_t end = comma == std::string::npos ? extra.size() : comma;
            std::string n = extra.substr(pos, end - pos);
            std::erase(n, ' ');
            if (!n.empty()) emergencyExtra_.push_back(std::move(n));
            if (comma == std::string::npos) break;
            pos = comma + 1;
        }
    }

    void SetLocal(std::string l) { local_ = std::move(l); }

    // Re-detect the IMS PDN address. local_ is captured once at start-up, but
    // the bearer's global address changes -- a re-attach, a carrier
    // reprovision, or RA renewal all replace it, and stale ones linger on the
    // interface. Binding the old address then fails with EADDRNOTAVAIL, which
    // surfaced as "bind init port failed" on every re-registration attempt:
    // 410 escalations overnight, all failing at the same bind. Refresh before
    // registering from scratch rather than assuming start-up state still holds.
    bool RefreshLocal() {
        auto found = DetectLocal(EnvOr("DEV", "qmapmux0.0"));
        if (!found || found->empty() || *found == local_) return false;
        Log("IMS PDN address changed; rebinding");
        local_ = *found;
        ctx_.local = local_;
        return true;
    }
    void SetDevMode(bool d) { devMode_ = d; }
    const std::string& Local() const { return local_; }

    // Dial() runs on the D-Bus thread: allocate the uni, queue the work.
    std::string Dial(const std::string& number) {
        std::scoped_lock g(cmdLock_);
        std::string uni = std::format("ims-call-{}", ++callSeq_);
        cmds_.push_back({Cmd::Kind::Dial, uni, number});
        return uni;
    }
    bool IsEmergencyNumber(const std::string& number) const {
        return imsd::msg::IsEmergency(number, emergencyExtra_);
    }
    void HangUp(const std::string& uni) {
        std::scoped_lock g(cmdLock_);
        cmds_.push_back({Cmd::Kind::HangUp, uni, {}});
    }
    void Accept(const std::string& uni) {
        std::scoped_lock g(cmdLock_);
        cmds_.push_back({Cmd::Kind::Accept, uni, {}});
    }
    void Stop() {
        { std::scoped_lock g(cmdLock_); cmds_.push_back({Cmd::Kind::Stop, {}, {}}); }
        quit_.store(true);
    }

    void Run() {
        try {
            BringUp();
        } catch (const std::exception& e) {
            if (devMode_) {
                // dev/session mode (no modem): keep the ABI up, unregistered,
                // so the D-Bus surface can be exercised without a phone.
                Log(std::format("bring-up skipped (dev mode): {}", e.what()));
                registered_ = false;
                EmitStatus(true);
                DevLoop();
                return;
            }
            Log(std::format("bring-up FAILED: {}", e.what()));
            auto ev = std::make_unique<Event>();
            ev->type = Event::Type::Fatal; ev->text = e.what();
            PostEvent(std::move(ev));
            return;
        }
        registered_ = true;
        EmitStatus(true);
        lastRefresh_ = Mono();
        lastKa_ = Mono();
        try { server_.Open(local_); }
        catch (...) { Log("server ports unavailable; remote hangup/incoming not seen"); }
        server_.PrimeServerSa(pcscf_, portPc_);
        // Registration-event subscription is useful for auditing terminating
        // bindings, but it is not required for originating calls. Some IMS
        // cores (observed on T-Mobile US) answer it 500 and then close the
        // otherwise healthy protected TCP flow. Keep it explicitly opt-in.
        if (RegEventEnabled()) SubscribeRegEvent();

        while (!quit_.load()) {
            if (auto cmd = PopCmd()) { HandleCmd(*cmd); continue; }
            for (Inbound& in : server_.Poll()) {
                reply_ = in.reply;
                replyPath_ = in.replyPath;
                if (auto st = imsd::sip::Status(in.msg)) {
                    std::string cseq(imsd::sip::Header(in.msg, "CSeq").value_or(""));
                    std::string callid(imsd::sip::Header(in.msg, "Call-ID").value_or(""));
                    if (call_ && callid == call_->CallId() && cseq.ends_with("INVITE")) {
                        DumpRaw(std::format("imsd-invite-response-{}.raw", *st), in.msg);
                        Execute(call_->OnInviteResponse(in.msg));
                        MaybeDropCall();
                    } else if (call_ && callid == call_->CallId() && cseq.ends_with("PRACK")) {
                        DumpRaw(std::format("imsd-prack-response-{}.raw", *st), in.msg);
                        Execute(call_->OnPrackResponse(in.msg));
                    } else if (call_ && callid == call_->CallId() && cseq.ends_with("UPDATE")) {
                        DumpRaw(std::format("imsd-update-response-{}.raw", *st), in.msg);
                    } else if (!subCallid_.empty() && callid == subCallid_ && cseq.ends_with("SUBSCRIBE")) {
                        Log(std::format("reg-event SUBSCRIBE -> {}", *st));
                        DumpRaw("imsd-subscribe-200.raw", in.msg);
                    }
                    continue;
                }
                DispatchRequest(in.msg);
            }
            MaybeRefresh();
            MaybeReconnect();
            if (!call_ && sip_.Alive() && Mono() - lastKa_ > 30) { sip_.SendKeepalive(); lastKa_ = Mono(); }
            ReapMedia();
            if (call_ && call_->State() != imsd::engine::CallState::Active && Mono() > deadline_) {
                Execute(call_->OnDeadline());
                MaybeDropCall();
            }
            auto msg = sip_.RecvMsg(0.3);
            if (!msg) continue;
            auto st = imsd::sip::Status(*msg);
            if (!st) {
                reply_ = [this](const std::string& r) { return sip_.Send(r); };
                replyPath_ = "registered-tcp";
                DispatchRequest(*msg);
                continue;
            }
            std::string cseq(imsd::sip::Header(*msg, "CSeq").value_or(""));
            std::string callid(imsd::sip::Header(*msg, "Call-ID").value_or(""));
            if (call_ && callid == call_->CallId() && cseq.ends_with("INVITE")) {
                DumpRaw(std::format("imsd-invite-response-{}.raw", *st), *msg);
                Execute(call_->OnInviteResponse(*msg));
                MaybeDropCall();
            } else if (call_ && callid == call_->CallId() && cseq.ends_with("PRACK")) {
                DumpRaw(std::format("imsd-prack-response-{}.raw", *st), *msg);
                Execute(call_->OnPrackResponse(*msg));
            } else if (call_ && callid == call_->CallId() && cseq.ends_with("UPDATE")) {
                DumpRaw(std::format("imsd-update-response-{}.raw", *st), *msg);
            } else if (!subCallid_.empty() && callid == subCallid_ && cseq.ends_with("SUBSCRIBE")) {
                Log(std::format("reg-event SUBSCRIBE -> {}", *st));
                DumpRaw("imsd-subscribe-200.raw", *msg);
            }
        }
        // shutdown: release an active call, keep the SA/registration for resume
        if (call_) { Execute(call_->OnHangup()); MaybeDropCall(); }
        sip_.Close();
        Log("engine stopped (SA left up for resume)");
    }

private:
    struct Cmd { enum class Kind { Dial, HangUp, Accept, Stop } kind; std::string uni, number; };

    // dev/session mode without a modem: process commands so the ABI answers,
    // but there is no SIP socket — a Dial collapses to terminated/error.
    void DevLoop() {
        while (!quit_.load()) {
            if (auto cmd = PopCmd()) { HandleCmd(*cmd); MaybeDropCall(); continue; }
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
    }

    // config
    std::string pcscf_, dev_, outDir_, local_, ealgOffer_, mediaBin_;
    std::vector<std::string> emergencyExtra_;
    int pcscfPort_ = 5060;
    int rtpPort_ = 50004;
    bool precond_ = false;
    bool autoAnswer_ = false;

    // registration
    imsd::msg::Context ctx_;
    RegState reg_;
    std::string route_, ppi_, ss_;
    bool registered_ = false;
    SipTcp sip_;
    ServerPorts server_;
    std::string subCallid_;   // live reg-event subscription dialog (empty: none)
    int portPs_ = 0;          // P-CSCF protected server port (reconnect target)
    int portPc_ = 0;          // P-CSCF protected client port (server-SA keepalive)
    double nextReconnect_ = 0;
    double reconnectDelay_ = 5;
    int reconnectFailures_ = 0;   // consecutive resume-reconnect failures
    double lastEscalation_ = 0;   // rate-limit full re-registration attempts

    // call
    imsd::util::Rng rng_;
    std::optional<imsd::engine::CallMachine> call_;
    bool dropCall_ = false;
    pid_t mediaPid_ = -1;
    double deadline_ = 0;
    // reply_ answers the inbound request being dispatched right now;
    // inviteReply_ is the original INVITE server transaction's durable
    // response channel. In-dialog requests can arrive over a different
    // protected flow; their responses use reply_ without displacing the
    // channel needed by a later Accept() 200 or CANCEL-triggered 487.
    std::function<bool(const std::string&)> reply_;
    std::function<bool(const std::string&)> inviteReply_;
    std::string replyPath_;
    std::string inviteReplyPath_;
    std::string initialInviteCseq_;
    double callTraceStart_ = 0;

    // timers
    double lastRefresh_ = 0;
    double lastKa_ = 0;
    std::atomic<bool> quit_{false};
    bool devMode_ = false;

    // command queue
    std::mutex cmdLock_;
    std::deque<Cmd> cmds_;
    // atomic: Dial (D-Bus thread) and an inbound INVITE (engine thread) both mint unis
    std::atomic<std::uint64_t> callSeq_{0};

    static double Mono() {
        return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
    }

    void Trace(std::string_view event) const {
        if (callTraceStart_ <= 0) return;
        Log(std::format("call trace +{:.3f} ms: {}",
                        (Mono() - callTraceStart_) * 1000.0, event));
    }

    static std::string CseqMethod(std::string_view msg) {
        std::string cseq(imsd::sip::Header(msg, "CSeq").value_or("unknown"));
        std::size_t space = cseq.find_last_of(' ');
        return space == std::string::npos ? cseq : cseq.substr(space + 1);
    }

    std::optional<Cmd> PopCmd() {
        std::scoped_lock g(cmdLock_);
        if (cmds_.empty()) return std::nullopt;
        Cmd c = cmds_.front(); cmds_.pop_front();
        return c;
    }

    static std::string ResolveMediaBin() {
        if (const char* e = std::getenv("IMSD_MEDIA")) return e;
        // The packaged data plane is owned by libexec. Prefer it over the
        // daemon's directory so an obsolete bring-up copy beside /usr/sbin/imsd
        // cannot silently shadow the upgraded helper used for every call.
        if (access("/usr/libexec/imsd-media", X_OK) == 0)
            return "/usr/libexec/imsd-media";
        if (!SelfDir.empty()) {
            std::string p = std::format("{}/imsd-media", SelfDir);
            if (access(p.c_str(), X_OK) == 0) return p;
        }
        return "/usr/libexec/imsd-media";
    }

    static bool RegEventEnabled() {
        return EnvOr("REG_EVENT", "0") == "1";
    }

    // Refresh P-Access-Network-Info from the live serving cell (mmcli 3GPP
    // location). Serving cell changes as the phone moves, so this runs
    // before every REGISTER/INVITE-carrying step; on failure the previous
    // value (possibly empty = header omitted) stays.
    void RefreshPani() {
        auto loc = RunCapture({"mmcli", "-m", "any", "--location-get"});
        if (auto p = imsd::aka::ParsePani(loc.out)) {
            ctx_.panInfo = *p;
            Log("3GPP access-network metadata refreshed via ModemManager");
            return;
        }
        // Current FP6/libqmi Get Serving System reports registration/RAT but
        // omits TAC and ECI. Get System Info supplies the complete live LTE
        // serving-cell tuple required for TS 24.229 P-Access-Network-Info.
        auto nas = RunCapture({"qmicli", "-d", "qrtr://0",
                               "--nas-get-system-info"});
        if (auto p = imsd::aka::ParsePani(nas.out)) {
            ctx_.panInfo = *p;
            Log("3GPP access-network metadata refreshed via QMI NAS");
        } else {
            Log("3GPP access-network metadata unavailable");
        }
    }

    std::optional<std::vector<std::uint8_t>> TransmitIsimApdu(
        int slot, int channel, std::vector<std::uint8_t> apdu) {
        auto send = [&](std::span<const std::uint8_t> bytes)
            -> std::optional<std::vector<std::uint8_t>> {
            auto out = RunCapture({
                "qmicli", "-d", "qrtr://0",
                std::format("--uim-send-apdu={},{},{}", slot, channel,
                            imsd::util::ToHex(bytes))});
            auto encoded = CompletedHex(out.out);
            if (!encoded) return std::nullopt;
            return imsd::util::FromHex(*encoded);
        };
        auto response = send(apdu);
        if (!response || response->size() < 2) return std::nullopt;
        if ((*response)[response->size() - 2] == 0x6c) {
            apdu.back() = response->back();
            response = send(apdu);
            if (!response || response->size() < 2) return std::nullopt;
        }
        if ((*response)[response->size() - 2] == 0x61) {
            auto getResponse = imsd::util::FromHex(
                imsd::aka::BuildGetResponseApdu(channel, response->back()));
            if (!getResponse) return std::nullopt;
            response = send(*getResponse);
        }
        return response;
    }

    std::optional<std::string> ReadIsimEf(int slot, int channel,
                                           std::uint16_t file,
                                           bool record) {
        std::uint8_t cla = channel <= 3 ? static_cast<std::uint8_t>(channel)
                                        : static_cast<std::uint8_t>(0x40 | (channel - 4));
        std::vector<std::uint8_t> select = {
            cla, 0xa4, 0x00, 0x04, 0x02,
            static_cast<std::uint8_t>(file >> 8),
            static_cast<std::uint8_t>(file & 0xff)};
        auto selected = TransmitIsimApdu(slot, channel, std::move(select));
        if (!selected || selected->size() < 2 ||
            (*selected)[selected->size() - 2] != 0x90 || selected->back() != 0x00)
            return std::nullopt;
        std::vector<std::uint8_t> read = record
            ? std::vector<std::uint8_t>{cla, 0xb2, 0x01, 0x04, 0x00}
            : std::vector<std::uint8_t>{cla, 0xb0, 0x00, 0x00, 0x00};
        auto response = TransmitIsimApdu(slot, channel, std::move(read));
        return response ? imsd::aka::ParseIsimEf(*response) : std::nullopt;
    }

    std::optional<imsd::aka::Identity> ReadIsimIdentity(
        const imsd::aka::SimSelection& isim, imsd::aka::Identity fallback) {
        auto opened = RunCapture({
            "qmicli", "-d", "qrtr://0",
            std::format("--uim-open-logical-channel={},{}", isim.slot, isim.aid)});
        auto parsedChannel = CompletedInt(opened.out);
        if (!parsedChannel || *parsedChannel < 0 || *parsedChannel > 19)
            return std::nullopt;
        int channel = static_cast<int>(*parsedChannel);
        auto close = [&] {
            RunCapture({"qmicli", "-d", "qrtr://0",
                        std::format("--uim-close-logical-channel={},{}",
                                    isim.slot, channel)});
        };
        auto impi = ReadIsimEf(isim.slot, channel, 0x6f02, false);
        auto domain = ReadIsimEf(isim.slot, channel, 0x6f03, false);
        auto impu = ReadIsimEf(isim.slot, channel, 0x6f04, true);
        close();
        if (!impi || !domain || !impu) return std::nullopt;
        return imsd::aka::ApplyIsimIdentity(
            std::move(fallback), std::move(*impi), std::move(*domain), std::move(*impu));
    }

    // ---- bring-up: warm resume if an SA + state file exist, else fresh ----
    void BringUp() {
        if (pcscf_.empty()) throw std::runtime_error("PCSCF not set — configure the carrier's P-CSCF address " "(P-CSCF discovery from the PDN's PCO is not implemented yet)");
        auto card = RunCapture({"qmicli", "-d", "qrtr://0", "--uim-get-card-status"});
        auto sel = imsd::aka::ParseCardStatus(card.out);
        if (!sel) throw std::runtime_error("no ready USIM found");
        auto modem = RunCapture({"mmcli", "-m", "any"});
        auto simPath = imsd::aka::ParsePrimarySimPath(modem.out);
        if (!simPath) throw std::runtime_error("no primary SIM path");
        auto siminfo = RunCapture({"mmcli", "-i", *simPath});
        auto info = imsd::aka::ParseSimInfo(siminfo.out);
        if (!info) throw std::runtime_error("SIM IMSI/operator not found");
        auto identity = imsd::aka::MakeIdentity(info->imsi, info->mcc, info->mnc);
        bool provisionedIsim = false;
        auto authenticationApp = *sel;
        if (auto isim = imsd::aka::ParseIsimSelection(card.out)) {
            if (auto provisioned = ReadIsimIdentity(*isim, identity)) {
                identity = std::move(*provisioned);
                provisionedIsim = true;
                // TS 31.103 places the IMS AKA AUTHENTICATE command in the
                // ISIM ADF. Fall back to the ready USIM only when no usable
                // provisioned ISIM exists.
                authenticationApp = *isim;
            }
        }
        ctx_.id = std::move(identity);
        ctx_.local = local_;
        ctx_.ealgOffer = ealgOffer_;
        ctx_.outboundFlow = EnvOr("SIP_OUTBOUND", "0") == "1";
        Log(std::format("SIP outbound flow affinity {}",
                        ctx_.outboundFlow ? "enabled" : "disabled"));
        // USER_AGENT overrides (some networks fingerprint UAs — setting the
        // stock firmware's build string gives oracle parity, journal/ims.md
        // s53); USER_AGENT= (empty) omits the header.
        ctx_.userAgent = EnvOr("USER_AGENT", std::format("imsd/{}", Version));
        // Use an explicitly provisioned device identity when mainline QRTR
        // cannot read the per-device NV item. The value is validated and
        // converted in memory; it is never printed. Otherwise use
        // ModemManager's stable key/value schema (plus its legacy output).
        std::string configuredImei = EnvOr("DEVICE_IMEI", "");
        if (!configuredImei.empty()) {
            auto urn = imsd::aka::ImeiUrn(configuredImei);
            if (!urn) throw std::runtime_error("configured device IMEI is invalid");
            ctx_.instanceId = *urn;
        } else {
            auto modemKv = RunCapture({"mmcli", "-m", "any", "-K"});
            if (auto imei = imsd::aka::ParseEquipmentId(modemKv.out + modem.out))
                if (auto urn = imsd::aka::ImeiUrn(*imei)) ctx_.instanceId = *urn;
        }
        if (ctx_.instanceId.empty()) {
            // Qualcomm's QRTR DMS may report its IMEI as "unknown" on mainline
            // Linux. RFC 5626 explicitly permits a UUID URN. Derive an
            // app-specific ID instead of exposing raw /etc/machine-id or
            // fabricating an IMEI. The fixed 128-bit value is Luma IMS's
            // application namespace, not a device credential.
            auto uuid = RunCapture({"systemd-id128", "-u", "-a",
                                    "6a756d612d696d732d696e7374616e63",
                                    "machine-id"});
            if (auto urn = imsd::aka::UuidUrn(uuid.out)) ctx_.instanceId = *urn;
        }
        Log(std::format("SIP device instance {}", ctx_.instanceId.empty() ? "unavailable" : "enabled"));
        reg_.slot = authenticationApp.slot;
        reg_.aid = authenticationApp.aid;
        Log(std::format("SIM slot={} IMS identity source={}", sel->slot,
                        provisionedIsim ? "ISIM" : "derived-USIM"));
        RefreshPani();

        std::string resume = EnvOr("RESUME", "auto");
        if (resume != "0" && TryResume()) return;
        FreshRegister();
    }

    bool TryResume() {
        auto state = RunCapture({"ip", "xfrm", "state"});
        auto policy = RunCapture({"ip", "xfrm", "policy"});
        auto sa = imsd::ipsec::ParseExistingSa(state.out, policy.out, pcscf_, local_);
        if (!sa) return false;
        auto ps = LoadState();
        if (!ps || !ps->callid) { Log("no reg state file; resume falls back to fresh"); return false; }

        route_ = ps->route.value_or(std::format("<sip:{};lr>", imsd::util::HostPort(pcscf_, sa->portPs)));
        // No fallback identity: an empty ppi omits P-Preferred-Identity and
        // is re-learned from the next 200's P-Associated-URI.
        ppi_ = ps->ppi.value_or("");
        ss_ = sa->securityServer;
        ctx_.route = route_; ctx_.ppi = ppi_; ctx_.securityServer = ss_;
        ctx_.portUc = PortUc; ctx_.portUs = PortUs;
        Log(std::format("RESUME via existing SA (cseq {})", ps->cseq.value_or(1)));

        portPs_ = sa->portPs;
        portPc_ = static_cast<int>(IntAfter(ss_, "port-c=").value_or(0));
        if (!sip_.Connect(local_, PortUc, pcscf_, sa->portPs)) {
            Log("resume connect failed; falling back to fresh");
            return false;
        }
        reg_.callid = *ps->callid;
        reg_.ftag = ps->ftag.value_or("");
        // The refresh must carry the SAME Contact URI as the stored binding;
        // an absent key means the binding predates the oracle diff and used
        // the IMSI (the empty-contactUser fallback).
        reg_.contactUser = ps->contactUser.value_or("");
        ctx_.contactUser = reg_.contactUser;
        reg_.cseq = ps->cseq.value_or(1);
        // The kernel is authoritative after an interrupted fresh registration.
        // A failed protected REGISTER can leave a newly negotiated SA installed
        // while the state file still contains the previous binding's inbound
        // SPIs. Advertising those stale persisted values makes the registrar's
        // reply target SAs that no longer exist, producing an apparent timeout.
        // Always advertise the SPIs reconstructed from the live XFRM state.
        reg_.spiUc = sa->spiUc;
        reg_.spiUs = sa->spiUs;
        reg_.expiry = ps->expiry.value_or(0);
        auto [okMsg, used] = Reregister(reg_.callid, reg_.ftag, reg_.cseq + 1, reg_.spiUc, reg_.spiUs);
        reg_.cseq = used;
        if (auto e = imsd::sip::GrantedExpires(okMsg)) reg_.expiry = *e;
        UpdateRouteAndPpi(okMsg);
        LogBindings(okMsg);
        PersistState(reg_, route_, ppi_);
        Log("REGISTERED (resumed + true-refreshed)");
        return true;
    }

    // The registrar's 200 OK echoes EVERY current binding for the implicit
    // set — the network's own view. Log them: a stale binding at a dead
    // SLAAC address is still tried by terminating routing until it expires.
    void LogBindings(const std::string& ok) {
        for (auto ct : imsd::sip::Headers(ok, "Contact"))
            Log(std::format("binding: {}", ct));
    }

    void FreshRegister() {
        RefreshLocal();
        Log("FRESH registration");
        // Oracle diff (journal/ims.md s53): stock's Contact user-part is a
        // fresh UUID per registration, not the IMSI. Minted before reg1 so
        // both REGISTERs and the stored binding carry the same URI.
        reg_.contactUser = imsd::util::Uuid4(rng_);
        ctx_.contactUser = reg_.contactUser;
        Log(std::format("contact user-part: {}", reg_.contactUser));
        std::string callidReg = std::format("{}@{}", rng_.Token(16), local_);
        std::string ftag = rng_.Token(8);
        std::uint32_t spiUc = rng_.UInt(0x10000, 0xFFFFFFF);
        std::uint32_t spiUs = rng_.UInt(0x10000, 0xFFFFFFF);
        ctx_.portUc = PortUc; ctx_.portUs = PortUs; ctx_.initPort = InitPort;

        // reg1 over UDP from the init port -> 401
        int fam = Is6(local_) ? AF_INET6 : AF_INET;
        int us = socket(fam, SOCK_DGRAM | SOCK_CLOEXEC, 0);
        int one = 1; setsockopt(us, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
        sockaddr_storage la; socklen_t ll = MakeAddr(local_, InitPort, la);
        if (bind(us, reinterpret_cast<sockaddr*>(&la), ll) != 0) {
            close(us); throw std::runtime_error("bind init port failed");
        }
        timeval tv{8, 0}; setsockopt(us, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
        std::string reg1 = imsd::msg::BuildRegisterInitial(ctx_, callidReg, ftag, rng_.Token(16), spiUc, spiUs);
        sockaddr_storage pa; socklen_t pl = MakeAddr(pcscf_, pcscfPort_, pa);
        sendto(us, reg1.data(), reg1.size(), 0, reinterpret_cast<sockaddr*>(&pa), pl);
        char buf[65535];
        ssize_t n = recv(us, buf, sizeof buf, 0);
        close(us);
        if (n <= 0) throw std::runtime_error("no 401 to unprotected REGISTER");
        std::string resp(buf, static_cast<std::size_t>(n));
        auto initialStatus = imsd::sip::Status(resp);
        if (initialStatus != 401)
            throw std::runtime_error(std::format(
                "expected 401, got {}", initialStatus.value_or(0)));
        auto nonceB64 = QuotedAfter(resp, "nonce=\"");
        if (!nonceB64) throw std::runtime_error("no nonce in 401");
        auto raw = imsd::util::FromBase64(*nonceB64);
        if (!raw || raw->size() < 32) throw std::runtime_error("bad nonce");
        std::vector<std::uint8_t> rand16(raw->begin(), raw->begin() + 16);
        std::vector<std::uint8_t> autn16(raw->begin() + 16, raw->begin() + 32);
        auto ssv = imsd::sip::Header(resp, "Security-Server");
        if (!ssv) throw std::runtime_error("no Security-Server");
        ss_ = std::string(*ssv);
        int portPs = static_cast<int>(IntAfter(ss_, "port-s=").value_or(0));
        int portPc = static_cast<int>(IntAfter(ss_, "port-c=").value_or(0));
        std::uint32_t spiPs = static_cast<std::uint32_t>(IntAfter(ss_, "spi-s=").value_or(0));
        std::uint32_t spiPc = static_cast<std::uint32_t>(IntAfter(ss_, "spi-c=").value_or(0));
        std::string ealg = EalgAfter(ss_);
        if (spiPs == 0) throw std::runtime_error("fresh-SA throttle active (spi-s=0); retry later");

        auto aka = Authenticate(rand16, autn16);
        if (!aka) throw std::runtime_error("USIM AKA failed");

        // install the IPsec SA pair
        imsd::ipsec::SaParams sp;
        sp.local = local_; sp.pcscf = pcscf_;
        sp.ik = aka->ik; sp.ck = aka->ck;
        sp.spiUc = spiUc; sp.spiUs = spiUs; sp.spiPc = spiPc; sp.spiPs = spiPs;
        sp.portUc = PortUc; sp.portUs = PortUs; sp.portPs = portPs; sp.portPc = portPc;
        sp.ealg = ealg;
        for (auto& cmd : imsd::ipsec::BuildSetupCommands(sp))
            RunCmd(cmd);

        ctx_.securityServer = ss_;
        portPs_ = portPs;
        portPc_ = portPc;
        if (!sip_.Connect(local_, PortUc, pcscf_, portPs)) throw std::runtime_error("protected TCP connect failed");

        std::string cnonce = rng_.Token(16);
        std::string response = imsd::aka::DigestAkav1(*nonceB64, aka->res, cnonce, ctx_.id.regUri, ctx_.id.impi, ctx_.id.domain);
        std::string auth = imsd::msg::AuthAka(ctx_, *nonceB64, cnonce, response);
        std::string reg2 = imsd::msg::BuildRegisterProtected(ctx_, callidReg, ftag, rng_.Token(16), 2, spiUc, spiUs, auth);
        if (!sip_.Send(reg2)) throw std::runtime_error("send protected REGISTER failed");
        std::string ok;
        for (;;) {
            auto m = sip_.RecvMsg(12.0);
            if (!m) throw std::runtime_error("no reply to protected REGISTER");
            auto st = imsd::sip::Status(*m);
            if (st == 200) { ok = *m; break; }
            if (st && *st >= 300) throw std::runtime_error(std::format("REGISTER failed {}", *st));
        }
        reg_.callid = callidReg; reg_.ftag = ftag; reg_.cseq = 2;
        reg_.spiUc = spiUc; reg_.spiUs = spiUs;
        reg_.expiry = imsd::sip::GrantedExpires(ok).value_or(0);
        DumpRaw("imsd-register-200.raw", ok);
        UpdateRouteAndPpi(ok);
        LogBindings(ok);
        if (route_.empty()) route_ = std::format("<sip:{};lr>", imsd::util::HostPort(pcscf_, portPs));
        ctx_.route = route_; ctx_.ppi = ppi_;
        PersistState(reg_, route_, ppi_);
        Log("REGISTERED (fresh)");
    }

    // re-REGISTER over the (resumed or live) SA. Returns (200 msg, used cseq).
    std::pair<std::string, long> Reregister(const std::string& callid, const std::string& ftag, long startCseq, std::uint32_t spiUc, std::uint32_t spiUs) {
        std::string auth = imsd::msg::AuthEmpty(ctx_);
        long cseq = startCseq;
        bool challenged = false;
        for (;;) {
            std::string reg = imsd::msg::BuildRegisterProtected(ctx_, callid, ftag, rng_.Token(16), cseq, spiUc, spiUs, auth);
            if (!sip_.Send(reg)) throw std::runtime_error("send re-REGISTER failed");
            auto m = sip_.RecvMsg(12.0);
            if (!m) throw std::runtime_error("no reply to re-REGISTER");
            auto st = imsd::sip::Status(*m);
            if (st == 200) return {*m, cseq};
            if (st == 401 && !challenged) {
                challenged = true;
                auto nonce = QuotedAfter(*m, "nonce=\"");
                auto raw = nonce ? imsd::util::FromBase64(*nonce) : std::nullopt;
                if (!raw || raw->size() < 32) throw std::runtime_error("bad 401 nonce on refresh");
                std::vector<std::uint8_t> rnd(raw->begin(), raw->begin() + 16);
                std::vector<std::uint8_t> autn(raw->begin() + 16, raw->begin() + 32);
                auto aka = Authenticate(rnd, autn);
                if (!aka) throw std::runtime_error("AKA failed on refresh");
                std::string cnonce = rng_.Token(16);
                std::string response = imsd::aka::DigestAkav1(*nonce, aka->res, cnonce, ctx_.id.regUri, ctx_.id.impi, ctx_.id.domain);
                auth = imsd::msg::AuthAka(ctx_, *nonce, cnonce, response);
                cseq++;
                continue;
            }
            throw std::runtime_error(std::format("re-REGISTER failed {}", st.value_or(0)));
        }
    }

    void UpdateRouteAndPpi(const std::string& okMsg) {
        auto svc = imsd::sip::Headers(okMsg, "Service-Route");
        if (svc.empty()) Log("200: no Service-Route (route stays P-CSCF)");
        if (!svc.empty()) {
            std::string r;
            for (std::size_t i = 0; i < svc.size(); i++) { r += svc[i]; if (i + 1 < svc.size()) r += ", "; }
            route_ = r; ctx_.route = r;
            Log(std::format("200 Service-Route: {}", r));
        }
        for (auto p : imsd::sip::Headers(okMsg, "Path"))
            Log(std::format("200 Path: {}", p));
        auto pau = imsd::sip::Headers(okMsg, "P-Associated-URI");
        if (pau.empty()) Log("200: NO P-Associated-URI");
        for (auto a : pau)
            Log(std::format("200 P-Associated-URI: {}", a));
        for (auto a : imsd::sip::Headers(okMsg, "P-Associated-URI")) {
            std::size_t lt = a.find("<tel:");
            if (lt != std::string_view::npos) {
                std::size_t gt = a.find('>', lt);
                ppi_ = std::string(a.substr(lt + 1, gt - lt - 1));
                ctx_.ppi = ppi_;
                break;
            }
        }
        // T-Mobile US returns its callable public identities exclusively as
        // sip: URIs (including a preferred sip:+E.164 identity), with no
        // tel: entry. RFC 3325 permits either form. Prefer the public E.164
        // SIP URI, then fall back to the first non-private SIP identity, so
        // originating INVITEs carry the asserted identity hint the carrier
        // expects instead of silently omitting P-Preferred-Identity.
        if (ppi_.empty()) {
            std::optional<std::string> firstSip;
            for (auto a : imsd::sip::Headers(okMsg, "P-Associated-URI")) {
                std::size_t lt = a.find("<sip:");
                if (lt == std::string_view::npos) continue;
                std::size_t gt = a.find('>', lt);
                if (gt == std::string_view::npos) continue;
                std::string identity(a.substr(lt + 1, gt - lt - 1));
                if (!firstSip) firstSip = identity;
                if (identity.starts_with("sip:+")) {
                    ppi_ = std::move(identity);
                    break;
                }
            }
            if (ppi_.empty() && firstSip) ppi_ = std::move(*firstSip);
            ctx_.ppi = ppi_;
        }
        // default public identity for self-targeted requests (reg-event
        // SUBSCRIBE): first sip: P-Associated-URI entry, else the tel: one
        // (the temporary IMPU is barred for anything but REGISTER)
        for (auto a : imsd::sip::Headers(okMsg, "P-Associated-URI")) {
            std::size_t lt = a.find("<sip:");
            if (lt != std::string_view::npos) {
                std::size_t gt = a.find('>', lt);
                ctx_.aor = std::string(a.substr(lt + 1, gt - lt - 1));
                break;
            }
        }
        if (ctx_.aor.empty() && !ppi_.empty()) ctx_.aor = ppi_;
        if (ppi_.empty()) Log("200: no public P-Associated-URI — P-Preferred-Identity omitted");
        Log(std::format("self AOR: {}", ctx_.aor.empty() ? ctx_.id.impu : ctx_.aor));
    }

    // USIM AKA via qmicli UIM logical channel.
    std::optional<imsd::aka::AkaResult> Authenticate(std::span<const std::uint8_t> rand16, std::span<const std::uint8_t> autn16) {
        auto open = RunCapture({"qmicli", "-d", "qrtr://0",
            std::format("--uim-open-logical-channel={},{}", reg_.slot, reg_.aid)});
        auto ch = CompletedInt(open.out);
        if (!ch) return std::nullopt;
        int channel = static_cast<int>(*ch);
        auto closeCh = [&] {
            RunCapture({"qmicli", "-d", "qrtr://0",
                std::format("--uim-close-logical-channel={},{}", reg_.slot, channel)});
        };
        std::string apdu = imsd::aka::BuildAkaApdu(channel, rand16, autn16);
        auto r1 = RunCapture({"qmicli", "-d", "qrtr://0",
            std::format("--uim-send-apdu={},{},{}", reg_.slot, channel, apdu)});
        auto b1 = CompletedHex(r1.out);
        if (!b1) { closeCh(); return std::nullopt; }
        if (b1->size() >= 2 && b1->substr(0, 2) == "61") {
            std::string gr = imsd::aka::BuildGetResponseApdu(channel, std::stoi(b1->substr(2, 2), nullptr, 16));
            auto r2 = RunCapture({"qmicli", "-d", "qrtr://0",
                std::format("--uim-send-apdu={},{},{}", reg_.slot, channel, gr)});
            b1 = CompletedHex(r2.out);
            if (!b1) { closeCh(); return std::nullopt; }
        }
        closeCh();
        auto bytes = imsd::util::FromHex(*b1);
        if (!bytes) return std::nullopt;
        return imsd::aka::ParseAkaResponse(*bytes);
    }

    // ---- keepalive refresh (only while idle) ------------------------------
    void MaybeRefresh() {
        if (call_ || reg_.callid.empty()) return;
        if (!sip_.Alive()) return;   // MaybeReconnect owns dead-flow recovery
        long exp = reg_.expiry ? reg_.expiry : 3600;
        double interval;
        if (const char* o = std::getenv("REFRESH_INTERVAL")) interval = std::atof(o);
        else interval = std::max(120.0, std::min(exp * 0.5, 1800.0));
        if (Mono() - lastRefresh_ < interval) return;
        try {
            RefreshPani();
            auto [msg, used] = Reregister(reg_.callid, reg_.ftag, reg_.cseq + 1, reg_.spiUc, reg_.spiUs);
            reg_.cseq = used;
            if (auto e = imsd::sip::GrantedExpires(msg)) reg_.expiry = *e;
            PersistState(reg_, route_, ppi_);
            Log(std::format("keepalive re-REGISTER ok (cseq {})", used));
            if (RegEventEnabled()) SubscribeRegEvent();
            if (!registered_) { registered_ = true; EmitStatus(true); }
            else EmitStatus(false);
        } catch (const std::exception& e) {
            // Whatever failed (dead flow, silent P-CSCF, error status), the
            // recovery is the same as a daemon restart: drop the flow and
            // let MaybeReconnect re-run the connect + true-refresh sequence.
            Log(std::format("keepalive re-REGISTER failed: {}", e.what()));
            sip_.Close();
        }
        lastRefresh_ = Mono();
        lastKa_ = Mono();
    }

    // The protected client flow can die under us while the SA and the
    // registration stay valid — a CSFB excursion resets it, the P-CSCF drops
    // idle flows — which is exactly the state a daemon restart resumes from
    // (s51: "restart-to-recover"). Do what the restart does, in place:
    // reconnect the TCP leg over the existing SA and true-refresh. Backoff
    // doubles 5 s → 5 min so a dead PDN doesn't turn this into a hot loop.
    void MaybeReconnect() {
        if (call_ || sip_.Alive() || portPs_ == 0 || reg_.callid.empty()) return;
        if (Mono() < nextReconnect_) return;
        Log("client flow dead; reconnecting");
        sip_.Close();
        bool ok = sip_.Connect(local_, PortUc, pcscf_, portPs_, /*tries=*/2);
        if (ok) {
            try {
                RefreshPani();
                auto [msg, used] = Reregister(reg_.callid, reg_.ftag, reg_.cseq + 1, reg_.spiUc, reg_.spiUs);
                reg_.cseq = used;
                if (auto e = imsd::sip::GrantedExpires(msg)) reg_.expiry = *e;
                UpdateRouteAndPpi(msg);
                LogBindings(msg);
                PersistState(reg_, route_, ppi_);
                Log("reconnected + re-registered");
                if (RegEventEnabled()) SubscribeRegEvent();
                if (!registered_) { registered_ = true; EmitStatus(true); }
                else EmitStatus(false);
                lastRefresh_ = Mono(); lastKa_ = Mono();
                reconnectDelay_ = 5;
                nextReconnect_ = 0;
                reconnectFailures_ = 0;
                return;
            } catch (const std::exception& e) {
                Log(std::format("reconnect re-REGISTER failed: {}", e.what()));
                sip_.Close();
            }
        }
        if (registered_) { registered_ = false; EmitStatus(true); }

        // Escalate rather than retrying resume forever. Reconnect only ever
        // re-opens the protected flow over the EXISTING security association;
        // when that SA is stale the attempt can never succeed, and the daemon
        // would back off to a 5-minute loop and stay unregistered
        // indefinitely -- observed live, 25+ minutes with no service and no
        // path out but manual intervention. After a few failures, discard the
        // resume state and the SAs it refers to and register from scratch,
        // which is exactly what recovering this by hand amounts to.
        if (++reconnectFailures_ >= 3) {
            // Rate-limit: a failing escalation must not become a tight loop.
            // Observed overnight at ~45s intervals, 410 attempts, each dying
            // at the same bind -- churn that helps nobody and keeps the
            // network busy.
            if (Mono() - lastEscalation_ < 120.0) {
                nextReconnect_ = Mono() + 60;
                return;
            }
            lastEscalation_ = Mono();
            Log("reconnect failed 3x; escalating to fresh registration");
            reconnectFailures_ = 0;
            reconnectDelay_ = 5;
            nextReconnect_ = 0;
            sip_.Close();
            ::unlink(StatePath().c_str());
            RunCapture({"ip", "xfrm", "state", "flush"});
            RunCapture({"ip", "xfrm", "policy", "flush"});
            try {
                FreshRegister();
            } catch (const std::exception& e) {
                Log(std::format("fresh re-registration failed: {}", e.what()));
                nextReconnect_ = Mono() + 30;
            }
            return;
        }
        nextReconnect_ = Mono() + reconnectDelay_;
        reconnectDelay_ = std::min(reconnectDelay_ * 2, 300.0);
    }

    // (Re-)subscribe to the reg event package (TS 24.229 5.1.1.3): a fresh
    // dialog each time, sent over the registration flow; the S-CSCF's
    // immediate NOTIFY (logged in DispatchRequest) is the network's own view
    // of our bindings. Failure is non-fatal — registration is unaffected.
    void SubscribeRegEvent() {
        subCallid_ = std::format("{}@{}", rng_.Token(16), local_);
        std::string sub = imsd::msg::BuildSubscribeReg(ctx_, subCallid_, rng_.Token(8), rng_.Token(16));
        if (sip_.Send(sub)) Log("reg-event SUBSCRIBE sent");
        else { Log("reg-event SUBSCRIBE send failed"); subCallid_.clear(); }
    }

    // ---- command handling -------------------------------------------------
    void HandleCmd(const Cmd& c) {
        if (c.kind == Cmd::Kind::Stop) { quit_.store(true); return; }
        if (c.kind == Cmd::Kind::Dial) {
            bool emergency = imsd::msg::IsEmergency(c.number, emergencyExtra_);
            if (call_) {
                if (!emergency) {
                    Log(std::format("dial({}) refused: call in progress", c.number));
                    CallInfo bad{c.uni, c.number, "terminated", "error", NowEpoch(), 0};
                    PostAdded(bad);
                    PostState(c.uni, "terminated", "error");
                    PostDeleted(c.uni);
                    return;
                }
                // an emergency dial preempts whatever exists (TS 22.101):
                // release it; if it lingers awaiting a CANCEL's 487, abandon
                // it — the late final is ignored by Call-ID mismatch
                Log(std::format("emergency dial: preempting call {}", call_->Uni()));
                Execute(call_->OnHangup());
                MaybeDropCall();
                if (call_) { Execute(call_->Abandon("preempted by emergency dial")); MaybeDropCall(); }
            }
            // a dead client flow fails the INVITE instantly — give the
            // reconnect path one immediate shot first (user action beats
            // the backoff timer)
            if (!sip_.Alive()) { nextReconnect_ = 0; MaybeReconnect(); }
            RefreshPani();
            if (emergency)
                Log(std::format("EMERGENCY dial ({}): urn:service:sos over the live registration, "
                                "fallback plain INVITE — stage 1, no emergency registration/PDN; "
                                "carrier-side behavior UNVERIFIED", c.number));
            call_.emplace(ctx_, rng_, c.uni, c.number, rtpPort_, precond_, emergency);
            CallInfo info{c.uni, c.number, "dialing", "outgoing", NowEpoch(), 0};
            PostAdded(info);
            bool ok = Execute(call_->Start());
            if (!ok && call_ && !call_->Terminated()) { Execute(call_->Fail("error")); }
            MaybeDropCall();
        } else if (c.kind == Cmd::Kind::HangUp) {
            if (call_ && call_->Uni() == c.uni) { Execute(call_->OnHangup()); MaybeDropCall(); }
        } else if (c.kind == Cmd::Kind::Accept) {
            if (call_ && call_->Uni() == c.uni) {
                Trace("Accept invoked");
                Execute(call_->OnAccept());
                MaybeDropCall();
            }
            else Log(std::format("Accept({}) ignored: no such ringing call", c.uni));
        }
    }

    void DispatchRequest(const std::string& msg) {
        std::string callid(imsd::sip::Header(msg, "Call-ID").value_or(""));
        std::size_t sp = msg.find(' ');
        std::string method = msg.substr(0, sp == std::string::npos ? 0 : sp);
        if (call_ && callid == call_->CallId()) {
            // A retransmission of the original INVITE may arrive on a fresh
            // accepted flow. Refresh only that transaction's channel; PRACK,
            // UPDATE, BYE and other dialog requests must not overwrite it.
            std::string cseq(imsd::sip::Header(msg, "CSeq").value_or(""));
            if (method == "INVITE" && cseq == initialInviteCseq_) {
                inviteReply_ = reply_;
                inviteReplyPath_ = replyPath_;
            }
            if (method == "PRACK" || method == "CANCEL" || method == "BYE" || method == "ACK")
                Trace(std::format("{} received", method));
            Execute(call_->OnRequest(msg));
            MaybeDropCall();
            return;
        }
        // ACK is never answered — e.g. the caller ACKing the 486/487 of a
        // call machine we already dropped
        if (method == "ACK") return;
        // reg-event NOTIFY: log the S-CSCF's reginfo (every binding it holds
        // for the implicit set, feature tags as stored), then 200 it below.
        // MESSAGE (terminating SMS-over-IP while +g.3gpp.smsip is
        // advertised): log the whole body — the 200 acks delivery, so the
        // journal is the only place the payload survives until real SMSoIP
        // handling exists.
        if (method == "NOTIFY" || method == "MESSAGE") {
            std::size_t at = msg.find("\r\n\r\n");
            std::string_view body = at == std::string::npos
                ? std::string_view{}
                : std::string_view(msg).substr(at + 4);
            if (method == "MESSAGE") Log(std::format("MESSAGE from {} ({} bytes):\n{}", imsd::sip::CallerId(msg), body.size(), body));
            else if (auto ev = imsd::sip::Header(msg, "Event"); ev && ev->starts_with("reg")) Log(std::format("reg-event NOTIFY ({} bytes):\n{}", body.size(), body));
            if (reply_) reply_(imsd::msg::BuildResponse200(ctx_, msg));
            return;
        }
        if (method == "INVITE") {
            OnIncomingInvite(msg);
            return;
        }
        if (reply_)
            reply_(imsd::msg::BuildResponse200(ctx_, msg));  // unsolicited (OPTIONS etc.)
    }

    // An out-of-dialog INVITE: a terminating (incoming) call.
    void OnIncomingInvite(const std::string& msg) {
        if (call_) {
            Log("incoming INVITE while a call exists: 486");
            if (reply_) reply_(imsd::msg::BuildInviteResponse(ctx_, msg, 486, "Busy Here", rng_.Token(10)));
            return;
        }
        std::string uni = std::format("ims-call-{}", ++callSeq_);
        callTraceStart_ = Mono();
        Trace("INVITE received");
        DumpRaw("imsd-invite-in.raw", msg);
        call_.emplace(ctx_, rng_, uni, imsd::engine::IncomingInvite{msg}, rtpPort_);
        inviteReply_ = reply_;
        inviteReplyPath_ = replyPath_;
        initialInviteCseq_ = std::string(imsd::sip::Header(msg, "CSeq").value_or(""));
        Log(std::format("incoming call {} from {}", uni, call_->Number()));
        CallInfo info{uni, call_->Number(), "incoming", "incoming", NowEpoch(), 0,
                      "incoming"};
        PostAdded(info);
        Execute(call_->OnInvite());
        if (call_ && autoAnswer_) {
            Log("bounded synchronous auto-answer");
            Trace("Accept invoked (synchronous diagnostic)");
            Execute(call_->OnAccept());
        }
        MaybeDropCall();
    }

    // ---- action execution -------------------------------------------------
    // Returns false if a client-flow send failed (used to fail a dial's INVITE).
    bool Execute(const std::vector<imsd::engine::Action>& actions) {
        using T = imsd::engine::Action::Type;
        bool sendOk = true;
        for (const auto& a : actions) {
            switch (a.type) {
                case T::SendClient:
                    if (a.text.starts_with("INVITE "))
                        DumpRaw("imsd-invite-out.raw", a.text);
                    else if (a.text.starts_with("PRACK "))
                        DumpRaw("imsd-prack-out.raw", a.text);
                    else if (a.text.starts_with("UPDATE "))
                        DumpRaw("imsd-update-out.raw", a.text);
                    if (!sip_.Send(a.text)) { sendOk = false; Log("client send failed"); }
                    break;
                case T::SendResponse:
                    {
                        auto status = imsd::sip::Status(a.text);
                        std::string method = CseqMethod(a.text);
                        if (status)
                            Trace(std::format("response {}/{} queued", *status, method));
                        bool ok = false;
                        std::string path = replyPath_;
                        std::string cseq(imsd::sip::Header(a.text, "CSeq").value_or(""));
                        if (method == "INVITE" && cseq == initialInviteCseq_ && inviteReply_) {
                            ok = inviteReply_(a.text);
                            path = inviteReplyPath_;
                        } else if (reply_) {
                            ok = reply_(a.text);
                        }
                        if (status)
                            Trace(std::format("response {}/{} path={} socket={}", *status,
                                              method, path.empty() ? "unknown" : path,
                                              ok ? "ok" : "failed"));
                        if (!ok) Log(std::format("response send failed ({}/{})",
                                                status.value_or(0), method));
                    }
                    break;
                case T::StartMedia: StartMedia(a.media); break;
                case T::StopMedia: StopMedia(); break;
                case T::State: PostState(call_->Uni(), a.state, a.reason); break;
                case T::Deleted: PostDeleted(call_->Uni()); dropCall_ = true; break;
                case T::SetDeadline: deadline_ = Mono() + a.seconds; break;
                case T::Log: Log(a.text); break;
            }
        }
        return sendOk;
    }

    void MaybeDropCall() {
        if (dropCall_) {
            call_.reset(); inviteReply_ = nullptr; dropCall_ = false;
            inviteReplyPath_.clear();
            initialInviteCseq_.clear();
            callTraceStart_ = 0;
        }
    }

    void StartMedia(const imsd::engine::MediaLeg& leg) {
        if (!leg.Valid()) { Log("SDP answer missing media endpoint; no media leg"); return; }
        Log(std::format("media {} pt={} octet={} -> [{}]:{}", leg.codec, leg.payloadType, leg.octetAlign, leg.remoteIp, leg.remotePort));
        if (Is6(leg.remoteIp)) RunCmd({"ip", "-6", "route", "replace", leg.remoteIp, "dev", dev_});
        else
            RunCmd({"ip", "route", "replace", leg.remoteIp, "dev", dev_});
        std::string outBase = std::format("{}/dialer_{}", outDir_, call_ ? call_->Uni() : std::string("x"));
        std::vector<std::string> argv = {
            mediaBin_, local_, std::to_string(rtpPort_), leg.remoteIp,
            std::to_string(leg.remotePort), std::to_string(leg.payloadType),
            "86400", outBase};
        pid_t pid = fork();
        if (pid == 0) {
            // Normal early media is receive-only. EARLY_MIC is an explicit
            // lab-only override used with a deterministic fixture to verify
            // uplink packetization before this carrier delivers final answer.
            bool transmit = leg.transmit || EnvOr("EARLY_MIC", "0") == "1";
            setenv("MIC", transmit ? EnvOr("MIC", "1").c_str() : "0", 1);
            setenv("PLAY", EnvOr("PLAY", "1").c_str(), 1);
            setenv("GAIN", EnvOr("GAIN", "10").c_str(), 1);
            setenv("PLAY_GAIN", EnvOr("PLAY_GAIN", "1.0").c_str(), 1);
            setenv("AMR_MODE", EnvOr("AMR_MODE", "2").c_str(), 1);
            setenv("DTX", EnvOr("DTX", "0").c_str(), 1);
            setenv("OCTET_ALIGN", leg.octetAlign ? "1" : "0", 1);
            std::vector<char*> c;
            for (auto& s : argv) c.push_back(const_cast<char*>(s.c_str()));
            c.push_back(nullptr);
            execvp(c[0], c.data());
            _exit(127);
        }
        mediaPid_ = pid;
    }

    void StopMedia() {
        if (mediaPid_ <= 0) return;
        kill(mediaPid_, SIGTERM);
        for (int i = 0; i < 100; i++) {
            if (waitpid(mediaPid_, nullptr, WNOHANG) != 0) { mediaPid_ = -1; return; }
            usleep(100000);
        }
        kill(mediaPid_, SIGKILL);
        waitpid(mediaPid_, nullptr, 0);
        mediaPid_ = -1;
    }

    void ReapMedia() {
        if (!call_ || mediaPid_ <= 0) return;
        if (call_->State() != imsd::engine::CallState::Active) return;
        int status = 0;
        pid_t r = waitpid(mediaPid_, &status, WNOHANG);
        if (r == 0) return;  // still running
        int code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
        mediaPid_ = -1;  // reaped; don't re-terminate
        Execute(call_->OnMediaExit(code));
        MaybeDropCall();
    }

    // ---- events -----------------------------------------------------------
    void EmitStatus(bool registrationSignal) {
        auto ev = std::make_unique<Event>();
        ev->type = registrationSignal ? Event::Type::Registration : Event::Type::Status;
        ev->status = StatusSnapshot{registered_, local_, pcscf_, ppi_, reg_.expiry, reg_.cseq};
        PostEvent(std::move(ev));
    }
    void PostAdded(const CallInfo& info) {
        auto ev = std::make_unique<Event>(); ev->type = Event::Type::Added; ev->info = info;
        PostEvent(std::move(ev));
    }
    void PostState(const std::string& uni, const std::string& state, const std::string& reason) {
        auto ev = std::make_unique<Event>(); ev->type = Event::Type::State;
        ev->uni = uni; ev->state = state; ev->reason = reason;
        PostEvent(std::move(ev));
    }
    void PostDeleted(const std::string& uni) {
        auto ev = std::make_unique<Event>(); ev->type = Event::Type::Deleted; ev->uni = uni;
        PostEvent(std::move(ev));
    }
};

Engine* TheEngine = nullptr;

// ================= GDBus ===================================================
constexpr const char* IntrospectionXml = R"xml(
<node>
  <interface name="net.catcrafts.IMS1">
    <method name="Dial">
      <arg type="s" name="number" direction="in"/>
      <arg type="s" name="callUni" direction="out"/>
    </method>
    <method name="DialEmergency">
      <arg type="s" name="number" direction="in"/>
      <arg type="s" name="callUni" direction="out"/>
    </method>
    <method name="HangUpEmergency"><arg type="s" name="callUni" direction="in"/></method>
    <method name="GetEmergencyCallState">
      <arg type="s" name="callUni" direction="in"/>
      <arg type="s" name="state" direction="out"/>
      <arg type="s" name="reason" direction="out"/>
    </method>
    <method name="HangUp"><arg type="s" name="callUni" direction="in"/></method>
    <method name="Accept"><arg type="s" name="callUni" direction="in"/></method>
    <method name="SendDtmf">
      <arg type="s" name="callUni" direction="in"/>
      <arg type="s" name="tones" direction="in"/>
    </method>
    <method name="GetCalls"><arg type="aa{sv}" name="calls" direction="out"/></method>
    <method name="GetStatus"><arg type="a{sv}" name="status" direction="out"/></method>
    <signal name="CallAdded"><arg type="s" name="callUni"/><arg type="a{sv}" name="info"/></signal>
    <signal name="CallStateChanged">
      <arg type="s" name="callUni"/><arg type="s" name="state"/><arg type="s" name="reason"/>
    </signal>
    <signal name="CallDeleted"><arg type="s" name="callUni"/></signal>
    <signal name="RegistrationChanged"><arg type="b" name="registered"/></signal>
  </interface>
</node>)xml";

void HandleMethodCall(GDBusConnection*, const gchar*, const gchar*, const gchar*, const gchar* method, GVariant* params, GDBusMethodInvocation* inv, gpointer) {
    std::string_view m = method;
    if (m == "Dial") {
        const gchar* number = nullptr;
        g_variant_get(params, "(&s)", &number);
        std::string uni = TheEngine->Dial(number ? number : "");
        g_dbus_method_invocation_return_value(inv, g_variant_new("(s)", uni.c_str()));
        return;
    }
    if (m == "DialEmergency") {
        const gchar* number = nullptr;
        g_variant_get(params, "(&s)", &number);
        std::string target = number ? number : "";
        if (!TheEngine->IsEmergencyNumber(target)) {
            g_dbus_method_invocation_return_dbus_error(
                inv, "net.catcrafts.IMS1.Error.NotEmergencyNumber",
                "the restricted pre-session interface accepts only emergency numbers");
            return;
        }
        std::string uni = TheEngine->Dial(target);
        EmergencyCalls.insert(uni);
        EmergencyFinalReasons.erase(uni);
        g_dbus_method_invocation_return_value(inv, g_variant_new("(s)", uni.c_str()));
        return;
    }
    if (m == "HangUpEmergency") {
        const gchar* uni = nullptr;
        g_variant_get(params, "(&s)", &uni);
        std::string target = uni ? uni : "";
        if (!EmergencyCalls.contains(target)) {
            g_dbus_method_invocation_return_dbus_error(
                inv, "net.catcrafts.IMS1.Error.NotEmergencyCall",
                "the call was not created by the restricted emergency interface");
            return;
        }
        TheEngine->HangUp(target);
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "GetEmergencyCallState") {
        const gchar* uni = nullptr;
        g_variant_get(params, "(&s)", &uni);
        std::string target = uni ? uni : "";
        if (!EmergencyCalls.contains(target)) {
            g_dbus_method_invocation_return_dbus_error(
                inv, "net.catcrafts.IMS1.Error.NotEmergencyCall",
                "the call was not created by the restricted emergency interface");
            return;
        }
        auto active = Calls.find(target);
        if (active != Calls.end()) {
            g_dbus_method_invocation_return_value(
                inv, g_variant_new("(ss)", active->second.state.c_str(),
                                   active->second.reason.c_str()));
            return;
        }
        auto final = EmergencyFinalReasons.find(target);
        const char* reason = final == EmergencyFinalReasons.end()
                                 ? ""
                                 : final->second.c_str();
        const char* state = final == EmergencyFinalReasons.end()
                                ? "dialing"
                                : "terminated";
        g_dbus_method_invocation_return_value(inv,
                                               g_variant_new("(ss)", state, reason));
        return;
    }
    if (m == "HangUp") {
        const gchar* uni = nullptr;
        g_variant_get(params, "(&s)", &uni);
        TheEngine->HangUp(uni ? uni : "");
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "Accept") {
        const gchar* uni = nullptr;
        g_variant_get(params, "(&s)", &uni);
        TheEngine->Accept(uni ? uni : "");
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "SendDtmf") {
        Log("SendDtmf accepted but not yet sent (TODO RFC4733)");
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "GetCalls") {
        GVariantBuilder b;
        g_variant_builder_init(&b, G_VARIANT_TYPE("aa{sv}"));
        for (auto& [uni, info] : Calls)
            g_variant_builder_add_value(&b, CallVariant(info));
        g_dbus_method_invocation_return_value(inv, g_variant_new("(aa{sv})", &b));
        return;
    }
    if (m == "GetStatus") {
        GVariantBuilder b;
        g_variant_builder_init(&b, G_VARIANT_TYPE("a{sv}"));
        g_variant_builder_add(&b, "{sv}", "registered", g_variant_new_boolean(StatusSnap.registered));
        g_variant_builder_add(&b, "{sv}", "local", g_variant_new_string(StatusSnap.local.c_str()));
        g_variant_builder_add(&b, "{sv}", "pcscf", g_variant_new_string(StatusSnap.pcscf.c_str()));
        g_variant_builder_add(&b, "{sv}", "expiry", g_variant_new_int64(StatusSnap.expiry));
        g_variant_builder_add(&b, "{sv}", "cseq", g_variant_new_int64(StatusSnap.cseq));
        g_variant_builder_add(&b, "{sv}", "ppi", g_variant_new_string(StatusSnap.ppi.c_str()));
        g_dbus_method_invocation_return_value(inv, g_variant_new("(a{sv})", &b));
        return;
    }
    g_dbus_method_invocation_return_dbus_error(inv, "org.freedesktop.DBus.Error.UnknownMethod", "no such method");
}

const GDBusInterfaceVTable Vtable = { HandleMethodCall, nullptr, nullptr, {} };

void OnBusAcquired(GDBusConnection* conn, const gchar*, gpointer) {
    DbusConn = conn;
    GDBusNodeInfo* node = g_dbus_node_info_new_for_xml(IntrospectionXml, nullptr);
    g_dbus_connection_register_object(conn, ObjPath, node->interfaces[0], &Vtable, nullptr, nullptr, nullptr);
    g_dbus_node_info_unref(node);
    Log(std::format("object registered at {}", ObjPath));
}

gboolean OnTerm(gpointer loop) {
    Log("SIGTERM — stopping engine (SA kept)");
    if (TheEngine) TheEngine->Stop();
    g_timeout_add_seconds(2, [](gpointer l) -> gboolean {
        g_main_loop_quit(static_cast<GMainLoop*>(l)); return G_SOURCE_REMOVE; }, loop);
    return G_SOURCE_REMOVE;
}

}  // namespace

int main(int argc, char** argv) {
    bool session = false;
    for (int i = 1; i < argc; i++)
        if (std::string_view(argv[i]) == "--session") session = true;

    if (argv[0]) {
        std::string p = argv[0];
        auto slash = p.find_last_of('/');
        if (slash != std::string::npos) SelfDir = p.substr(0, slash);
    }

    if (!session && geteuid() != 0) {
        std::println(std::cerr, "imsd: must run as root (xfrm / qmicli)");
        return 1;
    }

    std::string dev = EnvOr("DEV", "qmapmux0.0");
    std::string local = EnvOr("LOCAL", "");
    if (local.empty()) {
        if (auto l = DetectLocal(dev)) local = *l;
    }
    if (local.empty() && !session) {
        std::println(std::cerr, "imsd: no global IPv6 on {} — is the ims PDN up?", dev);
        return 1;
    }
    Log(std::format("LOCAL={} P-CSCF=[{}]:{}", local, EnvOr("PCSCF", "(unset)"), EnvOr("PCSCF_PORT", "5060")));

    Engine engine;
    engine.SetLocal(local);
    engine.SetDevMode(session);
    TheEngine = &engine;

    MainLoop = g_main_loop_new(nullptr, FALSE);
    guint owner = g_bus_own_name(
        session ? G_BUS_TYPE_SESSION : G_BUS_TYPE_SYSTEM, BusName,
        G_BUS_NAME_OWNER_FLAGS_NONE, OnBusAcquired,
        [](GDBusConnection*, const gchar* name, gpointer) { Log(std::format("owning {}", name)); },
        [](GDBusConnection*, const gchar* name, gpointer lp) {
            Log(std::format("lost {} — exiting", name));
            g_main_loop_quit(static_cast<GMainLoop*>(lp));
        },
        MainLoop, nullptr);

    g_unix_signal_add(SIGTERM, OnTerm, MainLoop);
    g_unix_signal_add(SIGINT, OnTerm, MainLoop);

    // Run the engine on a pthread with an explicit 8 MiB stack: musl's default
    // thread stack is only 128 KiB, and the registration paths hold 64 KiB SIP
    // recv buffers on the stack (a std::thread here segfaults on entry). The
    // registration is bus-independent, so start it immediately.
    pthread_t engineTid{};
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setstacksize(&attr, 8 * 1024 * 1024);
    pthread_create(&engineTid, &attr, [](void*) -> void* { TheEngine->Run(); return nullptr; }, nullptr);
    pthread_attr_destroy(&attr);

    Log(std::format("starting on the {} bus", session ? "session" : "system"));
    g_main_loop_run(MainLoop);

    engine.Stop();
    pthread_join(engineTid, nullptr);
    g_bus_unown_name(owner);
    g_main_loop_unref(MainLoop);
    return FatalExit ? 1 : 0;
}
