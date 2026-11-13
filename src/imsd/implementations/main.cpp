// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types no-char-pointer
/*
imsd — the userspace IMS/VoLTE daemon (control plane).

Owns net.catcrafts.IMS1 on the system bus with the project's frozen D-Bus ABI
(AGENTS.md §8.1) and drives it with the engine core: register on the IMS PDN (USIM AKA
+ IPsec sec-agree, fresh or warm-resume, with a keepalive re-REGISTER refresh),
run outgoing and incoming calls through the Imsd:Engine call machine (inbound
INVITEs on the protected-port listeners become ringing calls a dialer answers
via Accept), and spawn one imsd-media data-plane process per answered call.
GLib/GDBus lives only here and in ImsdBus (the polkit gate in front of call
control, shared with imsd-ofonod) — the integration daemons imsd-dialerd and
imsd-ofonod carry their own; every message, digest, SA command, and
call-state decision comes from the GLib-free core.

Threads: a dedicated engine thread runs the SIP loop (blocking framed recv,
protected-port listeners, media reaping, keepalive); the GLib main loop owns
the bus. D-Bus method calls push commands to the engine queue; engine events
reach the bus via g_idle_add, where the calls table + status snapshot live
(touched only on the main thread).

  --session   own the name on the session bus (development; no policy, no
              polkit)
*/

#include <arpa/inet.h>
#include <fcntl.h>
#include <gio/gio.h>
#include <glib-unix.h>
#include <ifaddrs.h>
#include <limits.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <pthread.h>
#include <pwd.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

import std;
import Imsd;
import ImsdBus;

namespace {

// ---- static config (env-overridable, same knobs as imsd.py) ---------------
constexpr const char* Version = "1.1.0";
constexpr const char* BusName = "net.catcrafts.IMS1";
constexpr const char* ObjPath = "/net/catcrafts/IMS1";
constexpr const char* Iface = "net.catcrafts.IMS1";
constexpr int PortUc = imsd::util::PortUc;
constexpr int PortUs = imsd::util::PortUs;
constexpr int PortUcNext = imsd::util::PortUcNext;
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

// argv as one journal line with key material (the "0x…" tokens) masked.
std::string RedactKeys(const std::vector<std::string>& argv) {
    std::string s;
    for (const auto& a : argv) {
        if (!s.empty()) s += ' ';
        s += a.starts_with("0x") ? "<key>" : a;
    }
    return s;
}

// imsd's own xfrm entries out of the kernel, each by its key (the
// OwnXfrmDeleteCommands list — never `ip xfrm … flush`: another IPsec user's
// entries, a policy-based VPN's, stay, §12); a delete that fails (an entry
// gone between the read and the delete) is logged and the rest go on. At
// file scope, as LoadHolds and PersistHolds are: the Engine and main() both
// run it (main() through RemoveDeadPair).
void RunOwnXfrmDeletes(const std::vector<std::vector<std::string>>& cmds) {
    for (auto& cmd : cmds) {
        int rc = RunCmd(cmd);
        if (rc != 0) Log(std::format("{} -> rc {}", RedactKeys(cmd), rc));
    }
}

// The kernel's SA pair when nothing can resume it and no fresh registration
// replaces it, and whatever else of imsd's own is left (a policy-less state
// on the UE's address): taken out by exact key, as the SA setup takes imsd's
// previous pair out (RunOwnXfrmDeletes — nothing else in the kernel is
// touched, §12), so the next bring-up finds nothing to resume. Run by the
// no-target bring-up and by main()'s idle before the bus name.
void RemoveDeadPair(std::string_view local) {
    auto state = RunCapture({"ip", "xfrm", "state"});
    auto policy = RunCapture({"ip", "xfrm", "policy"});
    auto cmds = imsd::ipsec::OwnXfrmDeleteCommands(state.out, policy.out, local);
    if (cmds.empty()) return;
    if (auto peer = imsd::ipsec::SaPeer(policy.out)) Log(std::format("the SA pair to {} is nothing to resume: removing it", *peer));
    else Log(std::format("{} xfrm entr{} of imsd's own with no pair to resume: removing them", cmds.size(), cmds.size() == 1 ? "y" : "ies"));
    RunOwnXfrmDeletes(cmds);
}

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

// The UE address on the ims PDN when LOCAL is not given: a global IPv6 on
// <dev> that is neither temporary nor RA-derived (`dynamic`) — the one
// ims-pdn-up.sh assigned from ModemManager — else the first non-temporary
// one, else the first. The kernel lists the temporary (privacy) address
// first, and registering on it binds Contact, Via and the SAs to an address
// that is deprecated after a day and removed after two (ims ledger F18).
std::optional<std::string> DetectLocal(const std::string& dev) {
    auto r = RunCapture({"ip", "-6", "addr", "show", "dev", dev, "scope", "global"});
    std::optional<std::string> first, nonTemporary, assigned;
    std::size_t pos = 0;
    while ((pos = r.out.find("inet6 ", pos)) != std::string::npos) {
        std::size_t start = pos + 6;
        pos = start;
        std::size_t eol = r.out.find('\n', start);
        if (eol == std::string::npos) eol = r.out.size();
        std::size_t slash = r.out.find('/', start);
        if (slash == std::string::npos || slash > eol) continue;
        std::string addr = r.out.substr(start, slash - start);
        std::string_view flags = std::string_view(r.out).substr(slash, eol - slash);
        bool temporary = flags.contains(" temporary");
        bool dynamic = flags.contains(" dynamic");
        if (!first) first = addr;
        if (!temporary && !nonTemporary) nonTemporary = addr;
        if (!temporary && !dynamic && !assigned) assigned = addr;
    }
    if (assigned) return assigned;
    if (nonTemporary) return nonTemporary;
    return first;
}

// Whether `addr` is configured on `dev` now. Compared as bytes, so any
// spelling of the address matches; when the interface list cannot be read
// the answer is yes — nothing here acts on a guess that it is gone.
bool OnLink(const std::string& dev, const std::string& addr) {
    int fam = Is6(addr) ? AF_INET6 : AF_INET;
    unsigned char want[16] = {};
    if (inet_pton(fam, addr.c_str(), want) != 1) return false;
    ifaddrs* ifs = nullptr;
    if (getifaddrs(&ifs) != 0) return true;
    bool found = false;
    for (ifaddrs* i = ifs; i && !found; i = i->ifa_next) {
        if (!i->ifa_addr || i->ifa_addr->sa_family != fam || dev != i->ifa_name) continue;
        const void* have = fam == AF_INET6 ? static_cast<const void*>(&reinterpret_cast<sockaddr_in6*>(i->ifa_addr)->sin6_addr)
                                           : static_cast<const void*>(&reinterpret_cast<sockaddr_in*>(i->ifa_addr)->sin_addr);
        found = std::memcmp(have, want, fam == AF_INET6 ? 16 : 4) == 0;
    }
    freeifaddrs(ifs);
    return found;
}

// The account whose PipeWire session carries call audio: AUDIO_USER when
// it names an account, else logind's seat0 session (`loginctl show-seat
// seat0` -> `show-session`) when that is a user session — not phrog's
// greeter — with a PipeWire socket. imsd-media gets the answer as
// AUDIO_USER. As packaged (root, on the system bus) imsd resolves it at
// start, so a wrong name or a missing session is in the journal before the
// first call rather than after two silent rounds of trial (alyx report 14,
// 2026-09-20), and again when a call's media starts, because the answer
// changes when someone logs in.
struct AudioSession { std::string name; unsigned uid = 0; std::string how; };

std::string LoginctlProp(std::string_view text, std::string_view key) {
    for (auto line : std::views::split(text, '\n')) {
        std::string_view l(line);
        if (l.size() > key.size() && l.starts_with(key) && l[key.size()] == '=') return std::string(l.substr(key.size() + 1));
    }
    return {};
}

AudioSession ResolveAudioSession() {
    AudioSession s;
    passwd pw{};
    passwd* res = nullptr;
    char buf[4096];
    std::string want = EnvOr("AUDIO_USER", "");
    if (!want.empty()) {
        if (getpwnam_r(want.c_str(), &pw, buf, sizeof buf, &res) == 0 && res) {
            s.name = want; s.uid = res->pw_uid; s.how = "AUDIO_USER";
            return s;
        }
        Log(std::format("AUDIO_USER '{}' is not an account; using logind's seat0 session instead", want));
    }
    auto seat = RunCapture({"loginctl", "show-seat", "seat0", "-p", "ActiveSession", "--value"});
    std::string id = seat.out;
    while (!id.empty() && (id.back() == '\n' || id.back() == ' ')) id.pop_back();
    if (seat.rc != 0 || id.empty()) { s.how = "no session on seat0"; return s; }
    auto sess = RunCapture({"loginctl", "show-session", id, "-p", "Name", "-p", "Class", "-p", "User"});
    std::string name = LoginctlProp(sess.out, "Name"), cls = LoginctlProp(sess.out, "Class"), uid = LoginctlProp(sess.out, "User");
    unsigned u = 0;
    auto [end, err] = std::from_chars(uid.data(), uid.data() + uid.size(), u);
    if (sess.rc != 0 || name.empty() || err != std::errc{} || end != uid.data() + uid.size()) { s.how = std::format("seat0 session {} not readable", id); return s; }
    if (!cls.starts_with("user")) { s.how = std::format("seat0 session {} is {}'s {} session, nobody logged in", id, name, cls); return s; }
    std::error_code ec;
    if (!std::filesystem::exists(std::format("/run/user/{}/pipewire-0", u), ec)) { s.how = std::format("seat0 session {} ({}) has no PipeWire socket in /run/user/{}", id, name, u); return s; }
    s.name = name; s.uid = u; s.how = std::format("logind seat0 session {}", id);
    return s;
}

std::string AudioSessionLine(const AudioSession& s) {
    if (!s.name.empty()) return std::format("audio session: {} (uid {}, {})", s.name, s.uid, s.how);
    return std::format("audio session: none — {}", s.how);
}

void LogAudioSession() {
    AudioSession s = ResolveAudioSession();
    Log(AudioSessionLine(s) + (s.name.empty() ? "; looked up again at each call, set AUDIO_USER to pin one" : ""));
}

// The P-CSCF a fresh registration goes to, from the environment: PCSCF=
// when set (one address, any spelling — a list is left as it came and
// refused at bring-up), else the first address of PCSCF_DISCOVERED=, the
// list the bring-up read from the PDN's PCO (comma-separated IPv6, PCO
// order). `discovered` and `rejected` get the list and what was not an
// IPv6 literal, for the caller's log lines.
std::string FreshPcscfFromEnv(std::vector<std::string>* discovered = nullptr, std::vector<std::string>* rejected = nullptr) {
    std::string configured = imsd::util::CanonicalAddr(EnvOr("PCSCF", ""));
    std::vector<std::string> list = imsd::util::ParseAddressList(EnvOr("PCSCF_DISCOVERED", ""), rejected);
    if (discovered) *discovered = list;
    if (!configured.empty()) return configured;
    return list.empty() ? std::string() : list.front();
}

// The no-target bring-up's two outcomes, told apart by the script's word in
// /run/imsd.env (§7.3). The PCO naming no P-CSCF is a fact
// (PCSCF_DISCOVERY=none): a daemon with nothing to resume idles on it until
// a boot or a start. A reader that did not answer is not
// (PCSCF_DISCOVERY=failed): exit 1, and the unit's restart runs discovery
// again. Nothing here depends on a configuration file existing — installing
// the package and inserting a SIM is the opt-in.
bool DiscoveryFailed() { return EnvOr("PCSCF_DISCOVERY", "") == "failed"; }
constexpr std::string_view NoPcscfIdleText = "no P-CSCF — none configured (PCSCF=), none discovered (see the ims-pdn-up lines) — and nothing to resume; set PCSCF= in /etc/imsd.env to name one";
constexpr std::string_view NoPcscfDiscoveryFailed = "no P-CSCF: discovery failed (see the ims-pdn-up lines) and nothing to resume";

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
enum class Transport { Tcp, Udp };
constexpr std::string_view TransportName(Transport t) { return t == Transport::Udp ? "UDP" : "TCP"; }

// The protected client flow: our requests to the P-CSCF's protected server
// port and their responses. TCP is the shape every carrier that works so far
// serves; UDP is the alternative for a P-CSCF that never answers the TCP
// connect (O2 UK, 2026-09-17). Both run through the same SA pair — the xfrm
// policies select on ports only — and the same Via port.
// A P-CSCF refusing a fresh security association (KPN: Security-Server
// spi-s=0 to a fresh REGISTER on an address whose registration is still
// live). Handled in-process: a
// systemd restart every RestartSec would never clear it.
struct ThrottledError : std::runtime_error { using std::runtime_error::runtime_error; };
// A REGISTER the network refused: the protected one, or a reregistration,
// answered with a final other than 200 (a reregistration's 401 asks for a
// re-authentication imsd does not do over the old SAs: a refusal carrying
// 401); the unprotected initial one answered with a final from 300 up (its
// 401 is the challenge). The status and its Retry-After feed
// imsd::engine::RegRecovery.
struct RefusedError : std::runtime_error {
    int status;
    long retryAfter;
    RefusedError(const std::string& what, int st, long ra) : std::runtime_error(what), status(st), retryAfter(ra) {}
};
// Bytes of SIP that fit one ESP-protected IPv6 packet at the ims PDN's
// 1280-byte MTU (40 IPv6 + 8 ESP + 16 IV + 8 UDP + padding + 12 ICV).
constexpr std::size_t UdpSinglePacketBudget = 1190;

class SipFlow {
public:
    // TCP: connect up to `tries` times (12-s timeout each, 10 s apart). An
    // attempt the network never answers counts towards `silentLimit` (0 =
    // unlimited); an attempt refused locally — connect() itself failing:
    // EADDRNOTAVAIL (the 4-tuple still in TIME_WAIT from the previous flow),
    // ENETUNREACH (no route yet) — does not, so a restart within 60 s of the
    // last one keeps retrying instead of concluding the P-CSCF is silent. UDP: bind the same client port and connect() the
    // datagram socket to the same peer — the kernel then delivers that peer's
    // datagrams here in preference to the unconnected ServerPorts socket on
    // the same port. A UDP connect() cannot be refused by the peer, so it is
    // one attempt.
    bool Connect(const std::string& local, int lport, const std::string& pcscf, int pport, Transport t, int tries = 10, int silentLimit = 0) {
        transport_ = t;
        lastConnectLocal_ = false;
        int fam = Is6(local) ? AF_INET6 : AF_INET;
        if (t == Transport::Udp) {
            int fd = socket(fam, SOCK_DGRAM | SOCK_CLOEXEC, 0);
            if (fd < 0) return SocketFailed();
            int one = 1;
            setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
            setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &one, sizeof one);
            sockaddr_storage la; socklen_t ll = MakeAddr(local, lport, la);
            if (!Bind(fd, la, ll, local, lport)) return false;
            sockaddr_storage pa; socklen_t pl = MakeAddr(pcscf, pport, pa);
            if (connect(fd, reinterpret_cast<sockaddr*>(&pa), pl) != 0) {
                // a UDP connect() cannot be refused by the peer: no route
                // yet, and the like — local, like the TCP path's ENETUNREACH
                Log(std::format("UDP connect: {}", std::strerror(errno)));
                close(fd); lastConnectLocal_ = true; return false;
            }
            fd_ = fd; alive_ = true; fb_ = {}; sizeWarned_ = false;
            return true;
        }
        int silent = 0;
        for (int attempt = 0; attempt < tries; attempt++) {
            int fd = socket(fam, SOCK_STREAM | SOCK_CLOEXEC, 0);
            if (fd < 0) return SocketFailed();
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
            if (!Bind(fd, la, ll, local, lport)) return false;
            sockaddr_storage pa; socklen_t pl = MakeAddr(pcscf, pport, pa);
            // non-blocking connect + poll: Linux connect() ignores SO_SNDTIMEO,
            // so bound it ourselves (a silent P-CSCF must not hang the engine).
            fcntl(fd, F_SETFL, O_NONBLOCK);
            int rc = connect(fd, reinterpret_cast<sockaddr*>(&pa), pl);
            bool connected = (rc == 0);
            bool localRefusal = (rc < 0 && errno != EINPROGRESS);
            int err = localRefusal ? errno : 0;
            if (rc < 0 && errno == EINPROGRESS) {
                pollfd p{fd, POLLOUT, 0};
                if (poll(&p, 1, 12000) > 0) {
                    socklen_t el = sizeof err;
                    getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &el);
                    connected = (err == 0);
                } else err = ETIMEDOUT;
            }
            if (connected) {
                fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) & ~O_NONBLOCK);
                fd_ = fd; alive_ = true; fb_ = {}; sizeWarned_ = false;
                return true;
            }
            close(fd);
            lastConnectLocal_ = localRefusal;
            if (!localRefusal) silent++;
            // An answer (RST → ECONNREFUSED, ICMP → EHOSTUNREACH/ENETUNREACH)
            // counts towards the silent limit like a timeout: either way the
            // P-CSCF does not serve TCP on that port. The log says which.
            const char* why = err == ETIMEDOUT ? "no answer" : (err == EADDRNOTAVAIL ? "TIME_WAIT?" : std::strerror(err));
            if (attempt == tries - 1) {
                Log(std::format("connect failed ({}) [{}/{}]", why, attempt + 1, tries));
                return false;
            }
            if (silentLimit > 0 && silent >= silentLimit) {
                Log(std::format("connect: no answer from the P-CSCF {} times ({})", silent, why));
                return false;
            }
            Log(std::format("connect failed; retry in 10s ({}) [{}/{}]", why, attempt + 1, tries));
            sleep(10);
        }
        return false;
    }
    // socket() failing is ours, not the network's: logged, and a local
    // refusal like a connect() the kernel turns down.
    bool SocketFailed() {
        Log(std::format("connect failed: socket: {}", std::strerror(errno)));
        lastConnectLocal_ = true;
        return false;
    }
    // A bind the kernel refuses (the address is not on the link:
    // EADDRNOTAVAIL) is logged and counts like the network's silence — it is
    // not a transient refusal to retry every 5 s. Closes fd on failure.
    bool Bind(int fd, const sockaddr_storage& la, socklen_t ll, const std::string& local, int lport) {
        if (bind(fd, reinterpret_cast<const sockaddr*>(&la), ll) == 0) return true;
        int err = errno;
        close(fd);
        lastConnectLocal_ = false;
        Log(std::format("connect failed: cannot bind {} ({})", imsd::util::HostPort(local, lport), std::strerror(err)));
        return false;
    }
    // Whether the last failed Connect() ended on a local refusal (our own
    // 4-tuple still in TIME_WAIT, no route yet) rather than on the network's
    // silence or answer — the recovery policy counts only the latter.
    bool LastConnectLocal() const { return lastConnectLocal_; }
    // MSG_NOSIGNAL: a flow the network killed (CSFB excursion, P-CSCF idle
    // drop) must surface as a send error, not a SIGPIPE.
    bool Send(std::string_view msg) {
        if (transport_ == Transport::Udp && msg.size() > UdpSinglePacketBudget && !sizeWarned_) {
            sizeWarned_ = true;
            Log(std::format("UDP: {} bytes of SIP exceed the single-packet ESP budget (~{}) at MTU 1280; the kernel sends it as IPv6 fragments — a P-CSCF that cannot reassemble ESP fragments drops it silently", msg.size(), UdpSinglePacketBudget));
        }
        std::size_t off = 0;
        while (off < msg.size()) {
            ssize_t w = send(fd_, msg.data() + off, msg.size() - off, MSG_NOSIGNAL);
            if (w <= 0) { alive_ = false; return false; }
            off += static_cast<std::size_t>(w);
        }
        return true;
    }
    void SendKeepalive() {
        if (transport_ == Transport::Udp) return;   // RFC 5626 4.4.1: CRLF keepalives are for connection-oriented transports
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
            if (transport_ == Transport::Udp) {
                // One datagram = one message (RFC 3261 18.3): never accumulated
                // across datagrams. An empty datagram is not EOF. A recv error
                // is the kernel relaying an ICMP for the inner (decrypted) UDP
                // — port unreachable → ECONNREFUSED, prohibited/policy → EACCES
                // — i.e. the peer answering "no"; an ICMP quoting the ESP
                // packet itself never reaches us (esp6_err ignores all but
                // PKT_TOOBIG), so UDP has no dead-flow signal beyond these and
                // the refresh timeouts.
                if (n < 0) {
                    if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) continue;
                    Log(std::format("UDP flow: recv error: {}", std::strerror(errno)));
                    alive_ = false; return std::nullopt;
                }
                if (n == 0) continue;
                std::string why;
                if (auto m = imsd::sip::DatagramMessage(std::string_view(buf, static_cast<std::size_t>(n)), &why)) return m;
                if (!why.empty()) Log(std::format("UDP: {} — ignored", why));
                continue;
            }
            // EOF/RST is not a timeout: mark the flow dead so the engine's
            // reconnect path notices within one loop turn instead of at the
            // next (possibly 30-min-away) keepalive refresh.
            if (n <= 0) { alive_ = false; return std::nullopt; }
            fb_.Append(std::string_view(buf, static_cast<std::size_t>(n)));
        }
    }
    int Fd() const { return fd_; }
    bool Alive() const { return fd_ >= 0 && alive_; }
    Transport CurrentTransport() const { return transport_; }
    // What was read and not yet dispatched goes with the flow: nothing
    // read from it can be answered once it is closed.
    void Close() { if (fd_ >= 0) { close(fd_); fd_ = -1; } alive_ = false; fb_ = {}; }
private:
    int fd_ = -1;
    bool alive_ = false;
    bool lastConnectLocal_ = false;
    Transport transport_ = Transport::Tcp;
    bool sizeWarned_ = false;
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
                // paired with SipFlow::Connect's REUSEPORT (reconnect binds
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
        udpUc_ = -1;
        udpUs_ = -1;
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
    // The answer to the last challenge — the 401's nonce, our cnonce and
    // the response — which every reregistration carries verbatim (TS
    // 24.229 5.1.1.4.2 a). Empty = a registration resumed from a pre-0.3.11
    // state file; its reregistrations carry an empty digest, the pre-0.3.11
    // shape, until the next fresh registration.
    std::string authNonce, authCnonce, authResponse;
    // The Security-Verify this registration's protected REGISTERs, its
    // reg-event SUBSCRIBE and its INVITEs echo: the 401's whole
    // Security-Server list (RFC 3329 §2.3.1). Set on the fresh 200 and at
    // the resume, never from an attempt in flight:
    // the member ss_ holds a fresh attempt's 401 before that attempt can
    // still abort (the throttle, an unsupported mechanism), and the
    // standing registration's state writes must not store it.
    std::string securityServer;
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

// `security_server` is the registration's Security-Verify (RegState's
// securityServer: the 401's joined list); a resume takes it from here
// since 0.3.12. The file's values are quoted without escaping; a value
// with a quote, a backslash or a newline in it (none RFC 3329's token
// syntax produces) is written empty, and the resume falls back to the SA.
void PersistState(const RegState& r, const std::string& route, const std::string& ppi, const std::string& aor, const std::string& transport, const std::string& pcscf) {
    std::string ss = r.securityServer.find_first_of("\"\\\n") == std::string::npos ? r.securityServer : std::string();
    std::string j = std::format(
        "{{\"callid\": \"{}\", \"ftag\": \"{}\", \"cseq\": {}, \"spi_uc\": {}, "
        "\"spi_us\": {}, \"expiry\": {}, \"route\": \"{}\", \"ppi\": \"{}\", "
        "\"aor\": \"{}\", \"contact_user\": \"{}\", \"transport\": \"{}\", "
        "\"auth_nonce\": \"{}\", \"auth_cnonce\": \"{}\", \"auth_response\": \"{}\", "
        "\"security_server\": \"{}\", \"pcscf\": \"{}\"}}",
        r.callid, r.ftag, r.cseq, r.spiUc, r.spiUs, r.expiry, route, ppi, aor,
        r.contactUser, transport, r.authNonce, r.authCnonce, r.authResponse, ss, pcscf);
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
    std::optional<std::string> callid, ftag, route, ppi, aor, contactUser, transport, authNonce, authCnonce, authResponse, securityServer, pcscf;
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
    ps.aor = QuotedAfter(s, "\"aor\": \"");
    ps.contactUser = QuotedAfter(s, "\"contact_user\": \"");
    ps.transport = QuotedAfter(s, "\"transport\": \"");   // absent in pre-0.3.5 files: TCP
    ps.authNonce = QuotedAfter(s, "\"auth_nonce\": \"");       // the three absent in pre-0.3.11 files
    ps.authCnonce = QuotedAfter(s, "\"auth_cnonce\": \"");
    ps.authResponse = QuotedAfter(s, "\"auth_response\": \"");
    ps.securityServer = QuotedAfter(s, "\"security_server\": \"");   // absent in pre-0.3.12 files
    ps.pcscf = QuotedAfter(s, "\"pcscf\": \"");   // absent in pre-1.0.0 files
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
    enum class Type { Added, State, Deleted, Registration, Status, Fatal, Idle } type;
    CallInfo info;                 // Added
    std::string uni, state, reason;  // State/Deleted
    StatusSnapshot status;         // Registration/Status
    std::string text;              // Fatal, Idle
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
bool FatalExit = false;   // set by an engine-fatal event: main() exits non-zero so systemd restarts us

std::int64_t NowEpoch() {
    return std::chrono::duration_cast<std::chrono::seconds>(std::chrono::system_clock::now().time_since_epoch()).count();
}

// The recovery policy's holds, kept for the next process: the network's
// word (a Retry-After, the schedule after a refused or throttled fresh
// attempt, a terminated binding's wait) outlives a restart of imsd, and
// an upgrade restarts it. They are filed under the PDN's address —
// another one, or a reboot, starts clean — so the file names that
// address and the boot, and lives in /run. Seconds left at
// `written_at`, which is on the boot clock: no clock step moves it, and
// it runs through a suspend as the network's timers do. Whether the old
// registration is gone travels with them: a restart inside a hold must
// not resume over SAs the network has written off.
std::int64_t BootSeconds() {
    timespec ts{};
    clock_gettime(CLOCK_BOOTTIME, &ts);
    return ts.tv_sec;
}

std::string BootId() {
    std::string id;
    if (std::FILE* f = std::fopen("/proc/sys/kernel/random/boot_id", "r")) {
        char buf[64];
        id.assign(buf, std::fread(buf, 1, sizeof buf, f));
        std::fclose(f);
    }
    while (!id.empty() && std::isspace(static_cast<unsigned char>(id.back()))) id.pop_back();
    return id;
}

// The recovery policy runs on the same clock: a hold in the process and
// its copy in the file age alike, through a suspend as the network's
// timers do.
double Boot() {
    timespec ts{};
    clock_gettime(CLOCK_BOOTTIME, &ts);
    return static_cast<double>(ts.tv_sec) + static_cast<double>(ts.tv_nsec) / 1e9;
}

std::string HoldsPath() {
    if (const char* o = std::getenv("HOLDS_FILE")) return o;
    if (geteuid() == 0) return "/run/imsd.holds";
    return std::format("{}/imsd.holds", EnvOr("XDG_RUNTIME_DIR", "/tmp"));
}

void PersistHolds(const imsd::engine::RegRecovery::Held& h, const std::string& pdn) {
    std::string path = HoldsPath();
    if (!h.Any()) {
        // nothing to keep: the file goes — when it is ours. A device node or a
        // link the HOLDS_FILE knob points at (/dev/null switches the file off) is
        // emptied instead, never unlinked.
        std::error_code ec;
        auto st = std::filesystem::symlink_status(path, ec);
        if (std::filesystem::is_regular_file(st)) std::remove(path.c_str());
        else if (std::filesystem::exists(st)) { if (std::FILE* f = std::fopen(path.c_str(), "w")) std::fclose(f); }
        return;
    }
    std::string j = std::format("{{\"pdn\": \"{}\", \"boot\": \"{}\", \"written_at\": {}, \"hold\": {}, \"fresh_hold\": {}, \"throttle_minutes\": {}, \"old_gone\": {}, \"refusals\": {}}}\n",
                                pdn, BootId(), BootSeconds(), static_cast<long>(std::ceil(h.hold)), static_cast<long>(std::ceil(h.freshHold)), h.throttleMinutes, h.oldGone ? "true" : "false", h.refusals);
    if (std::FILE* f = std::fopen(path.c_str(), "w")) {
        std::fwrite(j.data(), 1, j.size(), f);
        std::fclose(f);
    }
}

std::optional<imsd::engine::RegRecovery::Held> LoadHolds(const std::string& pdn) {
    std::FILE* f = std::fopen(HoldsPath().c_str(), "r");
    if (!f) return std::nullopt;
    std::string s;
    char buf[512]; std::size_t n;
    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) s.append(buf, n);
    std::fclose(f);
    if (imsd::util::CanonicalAddr(QuotedAfter(s, "\"pdn\": \"").value_or("")) != imsd::util::CanonicalAddr(pdn)) return std::nullopt;
    if (QuotedAfter(s, "\"boot\": \"").value_or("") != BootId()) return std::nullopt;
    long elapsed = BootSeconds() - IntAfter(s, "\"written_at\": ").value_or(0);
    imsd::engine::RegRecovery::Held h;
    h.hold = std::max<long>(0, IntAfter(s, "\"hold\": ").value_or(0) - elapsed);
    h.freshHold = std::max<long>(0, IntAfter(s, "\"fresh_hold\": ").value_or(0) - elapsed);
    h.throttleMinutes = static_cast<int>(IntAfter(s, "\"throttle_minutes\": ").value_or(h.throttleMinutes));
    h.oldGone = s.find("\"old_gone\": true") != std::string::npos;
    h.refusals = static_cast<int>(IntAfter(s, "\"refusals\": ").value_or(0));
    if (!h.Any()) return std::nullopt;
    return h;
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
            FatalExit = true;   // non-zero exit, or Restart=on-failure never fires
            g_main_loop_quit(MainLoop);
            break;
        case Event::Type::Idle:
            // nothing to do (§7.3): a clean exit, which Restart=on-failure
            // leaves alone
            Log(std::format("idle: {} — nothing to do; systemctl start imsd when that changes", ev->text));
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
        // The P-CSCF a fresh registration goes to: PCSCF= when set, else the
        // first address the bring-up read from the PDN's PCO
        // (PCSCF_DISCOVERED=); no baked-in default — carrier infrastructure.
        // A resume follows the kernel's SAs instead (TryResume), whatever
        // this says today.
        {
            std::vector<std::string> rejected;
            pcscfTarget_ = FreshPcscfFromEnv(&pcscfCandidates_, &rejected);
            pcscf_ = pcscfTarget_;
            std::string configured = imsd::util::CanonicalAddr(EnvOr("PCSCF", ""));
            std::string list;
            for (const std::string& c : pcscfCandidates_) list += (list.empty() ? "" : ", ") + c;
            if (!configured.empty())
                Log(pcscfCandidates_.empty() ? std::format("pcscf: configured {}", configured)
                                             : std::format("pcscf: configured {}; discovery gave {} (ignored: PCSCF= wins)", configured, list));
            else if (!pcscfCandidates_.empty())
                Log(std::format("pcscf: discovered {} (using {})", list, pcscfTarget_));
            for (const std::string& r : rejected) Log(std::format("pcscf: PCSCF_DISCOVERED entry '{}' is not an IPv6 address; ignored", r));
        }
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
        // SIP_TRANSPORT: the protected leg's transport. auto (default) = TCP,
        // and when the P-CSCF never answers the TCP connect, a fresh
        // registration over UDP; tcp / udp force one. The challenge (first
        // REGISTER) is UDP regardless.
        sipPolicy_ = EnvOr("SIP_TRANSPORT", "auto");
        if (sipPolicy_ != "auto" && sipPolicy_ != "tcp" && sipPolicy_ != "udp") {
            Log(std::format("SIP_TRANSPORT={} unknown; using auto", sipPolicy_));
            sipPolicy_ = "auto";
        }
        transport_ = sipPolicy_ == "udp" ? Transport::Udp : Transport::Tcp;
        mediaBin_ = ResolveMediaBin();
        // CODECS: restrict + reorder the codecs we offer and accept (bench
        // knob — a gateway that transcodes every caller up to AMR-WB never
        // lets the narrowband path run otherwise). Empty = defaults.
        codecs_ = imsd::sdp::ParseCodecList(EnvOr("CODECS", ""));
        if (!codecs_.empty()) {
            std::string list;
            for (const auto& c : codecs_) list += (list.empty() ? "" : ",") + c;
            Log(std::format("CODECS override active: {}", list));
        }
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

    void SetLocal(std::string l) { local_ = imsd::util::CanonicalAddr(l); pdnLocal_ = local_; }
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
    void SendDtmf(const std::string& uni, const std::string& tones) {
        std::scoped_lock g(cmdLock_);
        cmds_.push_back({Cmd::Kind::Dtmf, uni, tones});
    }
    void Stop() {
        { std::scoped_lock g(cmdLock_); cmds_.push_back({Cmd::Kind::Stop, {}, {}}); }
        quit_.store(true);
    }

    void Run() {
        if (auto h = LoadHolds(pdnLocal_)) {
            recovery_.Restore(Boot(), *h);
            auto kept = recovery_.Holds(Boot());   // what the policy took over: the file's values after the clamp
            if (kept.hold > 0 || kept.freshHold > 0)
                Log(std::format("a hold the network set before this start stands ({:.0f} s for any attempt, {:.0f} s for a fresh registration; the refusal schedule at {} min)", kept.hold, kept.freshHold, kept.throttleMinutes));
            else if (kept.throttleMinutes > imsd::engine::RegRecovery::ThrottleMinutesMin)
                Log(std::format("the refusal schedule stands at {} min from before this start: a refused or throttled fresh registration waits that long", kept.throttleMinutes));
            if (kept.oldGone) Log("the old registration was gone before this start: no resume over its SAs, the fresh registration once its wait has run");
        }
        for (;;) {
        try {
            BringUp();
            break;
        } catch (const Stopping&) {
            return;
        } catch (const ThrottledError& e) {
            DeferBringUp(e.what(), imsd::engine::RegRecovery::Failure::Throttled, 0);
            if (quit_.load()) return;
            continue;
        } catch (const RefusedError& e) {
            // A refused initial registration (403, 5xx) waits like a
            // throttle: a fatal would restart imsd every RestartSec with an
            // initial REGISTER each time.
            DeferBringUp(e.what(), imsd::engine::RegRecovery::Failure::Refused, e.retryAfter);
            if (quit_.load()) return;
            continue;
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
        }
        try { server_.Open(local_); }
        catch (...) { Log("server ports unavailable; remote hangup/incoming not seen"); }
        server_.PrimeServerSa(pcscf_, portPc_);
        OnRegistered();

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
                    } else {
                        OnSubscribeResponse(in.msg, *st);
                    }
                    continue;
                }
                DispatchRequest(in.msg);
            }
            MaybeRefresh();
            MaybeReconnect();
            MaybeResubscribe();
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
                replyPath_ = "registered-flow";
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
            } else {
                OnSubscribeResponse(*msg, *st);
            }
        }
        // shutdown: release an active call, keep the SA/registration for resume
        if (call_) { Execute(call_->OnHangup()); MaybeDropCall(); }
        sip_.Close();
        Log("engine stopped (SA left up for resume)");
    }

private:
    struct Cmd { enum class Kind { Dial, HangUp, Accept, Dtmf, Stop } kind; std::string uni, arg; };   // arg: the number to dial, or the tones
    struct Stopping {};   // the engine was told to stop while a bring-up waited

    // A bring-up the network deferred (a fresh-SA throttle) or refused: wait
    // it out here, in process, on the recovery policy's schedule, then try
    // the bring-up again. There is nothing to reconnect over meanwhile (the
    // resume just found the stored SAs unusable, or there are none) and no
    // recovery step runs from inside the wait — portPs_ says so to
    // MaybeReconnect, whatever the attempt left in it — so a Dial
    // meanwhile collapses at once, as in dev mode.
    void DeferBringUp(std::string_view why, imsd::engine::RegRecovery::Failure f, long retryAfter) {
        FreshAttemptFailed(f, retryAfter, std::format("bring-up deferred: {}", why));
        portPs_ = 0;
        registered_ = false;
        EmitStatus(true);
        WaitForPolicy();
    }

    // Wait until the policy's next step is due. A Dial/Accept/HangUp
    // arriving meanwhile must not wait in the queue and run stale once
    // registration succeeds; with no flow the call collapses to terminated
    // at once (DevLoop's rule). A wait can be long (an hour on the
    // schedule), so the REGISTER after one names the access network read
    // after it, not before. False when the engine is stopping.
    bool WaitForPolicy() {
        bool waited = false;
        while (!quit_.load() && recovery_.Next(Boot()).at > Boot()) {
            waited = true;
            if (auto cmd = PopCmd()) { HandleCmd(*cmd); MaybeDropCall(); continue; }
            std::this_thread::sleep_for(std::chrono::seconds(1));
        }
        if (waited && !quit_.load()) RefreshPani();
        return !quit_.load();
    }

    void KeepHolds() { PersistHolds(recovery_.Holds(Boot()), pdnLocal_); }
    // what follows a resume that failed: with a fresh target the fresh
    // registration, without one the restart's new resume attempt
    std::string ResumeFailedNext() const { return pcscfTarget_.empty() ? "the restart tries the resume again" : "the next registration is a fresh one"; }
    // a pair nothing can resume (no state file, the file another attempt's): with a fresh target the fresh registration replaces it; with none BringUp writes it off and takes it out of the kernel
    std::string ResumeDeadNext() const { return pcscfTarget_.empty() ? "nothing can resume it: the pair is removed" : "resume falls back to fresh"; }
    // the failed resume's line tail by the engine's verdict: a registration it
    // wrote off is removed (no fresh target) or replaced by the fresh one; with
    // no fresh target a resume it gives up (a 401, a refusal with a Retry-After,
    // the second refusal) is removed too; else what ResumeFailedNext says
    std::string ResumeFailedTail(const std::string& next) {
        if (!recovery_.Reconnectable()) return pcscfTarget_.empty() ? "written off by the network: the pair is removed" : "written off by the network: the next registration is a fresh one";
        if (pcscfTarget_.empty() && recovery_.Fresh()) return "the resume is given up (the engine's rule): the pair is removed";
        return next;
    }

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
    // The P-CSCF a fresh registration goes to (PCSCF=, else the first of
    // PCSCF_DISCOVERED=) and the whole discovered list. pcscf_ is the one in
    // use: the same, or the kernel's SA peer while a resumed registration
    // made to another one lives (TryResume) — the next fresh registration
    // moves to the target, and back when it fails before the SA setup
    // (FreshRecover), as local_ does against pdnLocal_.
    std::string pcscfTarget_;
    std::vector<std::string> pcscfCandidates_;
    static constexpr std::string_view NoFreshPcscf = "no P-CSCF for a fresh registration: none configured (PCSCF=) and none discovered (PCSCF_DISCOVERED empty — see the ims-pdn-up lines)";
    // The PDN's own address (LOCAL, else DetectLocal) — every fresh
    // registration's. local_ differs from it only while a resume runs on the
    // address an older registration was made on (TryResume).
    std::string pdnLocal_;
    std::string sipPolicy_;              // SIP_TRANSPORT: auto | tcp | udp
    Transport transport_ = Transport::Tcp;   // the protected leg's transport in effect
    void SetTransport(Transport t) { transport_ = t; ctx_.transport = std::string(TransportName(t)); }
    std::vector<std::string> emergencyExtra_;
    int pcscfPort_ = 5060;
    int rtpPort_ = 50004;
    bool precond_ = false;
    bool autoAnswer_ = false;
    std::vector<std::string> codecs_;   // CODECS override; empty = defaults

    // registration
    imsd::msg::Context ctx_;
    RegState reg_;
    std::string route_, ppi_, ss_;
    bool registered_ = false;
    SipFlow sip_;
    ServerPorts server_;
    std::string subCallid_;   // live reg-event subscription dialog (empty: none)
    bool subPending_ = false;      // SUBSCRIBE sent, no final yet
    double subSentAt_ = 0;
    double subRetryAt_ = 0;        // a failed/ended subscription is renewed at this time (0: none due)
    double subRenewAt_ = 0;        // the live subscription is subscribed anew at this time, ahead of its granted expiry (0: none)
    double subRetryDelay_ = 60;    // 60 s doubling to 10 min between failed SUBSCRIBEs
    int portPs_ = 0;          // P-CSCF protected server port (reconnect target)
    int portPc_ = 0;          // P-CSCF protected client port (server-SA keepalive)
    imsd::engine::RegRecovery recovery_;   // what to do when the registration stops working
    bool saReplaced_ = false;      // the running fresh attempt got past the SA setup (the old registration's SAs are gone)

    // call
    imsd::util::Rng rng_;
    std::optional<imsd::engine::CallMachine> call_;
    bool dropCall_ = false;
    bool closeFlowWhenIdle_ = false;   // a binding was terminated under a call: its flow closes with the call
    pid_t mediaPid_ = -1;
    int mediaCtl_ = -1;       // our end of the leg's control pipe (its stdin)
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
        if (pcscfTarget_.contains(',')) throw std::runtime_error(std::format("PCSCF='{}' must be one address", pcscfTarget_));
        // PCSCF= is accepted in one spelling only: the one `ip` prints (RFC
        // 5952 — what the journal and `ip xfrm` show). Another spelling is
        // refused with the one to use rather than normalised, so the bring-up
        // script, which routes the value as typed, and the daemon agree on it.
        if (std::string raw = EnvOr("PCSCF", ""); !raw.empty()) {
            auto canon = imsd::util::CanonicalIp6(raw);
            if (!canon || *canon != raw) throw std::runtime_error(std::format("PCSCF='{}' is not written as ip prints it; use PCSCF={}", raw, canon.value_or("<one bare IPv6 address, RFC 5952 form>")));
        }
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
        SetTransport(transport_);
        Log(std::format("SIP transport: {} ({})", TransportName(transport_), sipPolicy_));
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
        // A resume that the network refuses (403 after another process
        // registered fresh for the same IMPI, a stale binding, …) is not
        // fatal while there is a fresh target: the fresh registration below
        // replaces its SAs, and only a failed FRESH registration is. With no
        // fresh target the bring-up ends as §7.2 step 1 says.
        // The network's word holds at bring-up as it does in recovery: a
        // hold from before this start is waited out before the resume (a
        // refresh's Retry-After) or the fresh registration (a fresh attempt's
        // schedule or Retry-After, a terminated binding's wait), and so is
        // one the resume itself earns — a refusal's Retry-After, a binding
        // a NOTIFY terminated under it.
        ResumeOutcome outcome = ResumeOutcome::Dead;   // not tried (RESUME=0, or written off before this start): a pair nothing resumes
        if (resume != "0") {
            if (!WaitForPolicy()) throw Stopping{};
            // a registration the network wrote off before this start (the
            // holds carry it) is not resumed: the wait above was the fresh one
            const std::string next = ResumeFailedNext();
            try { if (recovery_.Reconnectable()) { outcome = TryResume(); if (outcome == ResumeOutcome::Done) return; } }
            catch (const RefusedError& e) {
                outcome = ResumeOutcome::Failed;
                CloseFlowAfterWait();
                // the network's word (a Retry-After) reaches the reducer as before;
                // with no fresh target every refusal does — its count travels in
                // the holds, so a restart's refusal is the second (§7.4). With a
                // target a refusal without one feeds nothing: the fresh
                // registration follows at once, not after a self-imposed backoff
                // the fresh path would log as the network's hold
                if (e.retryAfter > 0 || pcscfTarget_.empty()) recovery_.OnReconnectFailure(Boot(), imsd::engine::RegRecovery::Failure::Refused, e.retryAfter, e.status);
                Log(std::format("resume failed: {} — {}", e.what(), ResumeFailedTail(next)));
            }
            catch (const std::exception& e) { outcome = ResumeOutcome::Failed; CloseFlowAfterWait(); Log(std::format("resume failed: {} — {}", e.what(), ResumeFailedTail(next))); }
            // the engine's verdict decides, in one place for both arms: a
            // registration it wrote off (a 403 once a fresh registration is
            // due; a binding a NOTIFY terminated under the re-REGISTER wait,
            // Imsd-Engine.cppm) is nothing to resume any more, and so, with no
            // fresh target, is one whose resume it gives up (a 401, a refusal
            // with a Retry-After, the second refusal — RefusalsBeforeFresh,
            // the count surviving the restart): Dead, taken out below. With a
            // target the fresh registration follows either way.
            if (outcome == ResumeOutcome::Failed && (!recovery_.Reconnectable() || (pcscfTarget_.empty() && recovery_.Fresh()))) outcome = ResumeOutcome::Dead;
        }
        if (pcscfTarget_.empty()) {
            // No fresh registration follows, so a resumable pair is not written
            // off: one whose attempt failed (a silent connect, an
            // unanswered or refused refresh — its Retry-After is in the
            // holds) stays for the restart to resume (the fatal and the
            // retry) — unless the engine's rule wrote the registration off or
            // gave the resume up (a 403 once fresh is due, a NOTIFY-terminated
            // binding; a 401, a refusal with a Retry-After, the second refusal —
            // the count travels in the holds; Dead above); a pair nothing can
            // resume (its UE address left the link, no
            // state file, the file another attempt's, written off before this
            // start) is written off and taken out of the kernel by exact key
            // — the fresh registration that would have replaced it does not
            // come — so the next bring-up finds nothing to resume, and the
            // daemon idles (RequestIdle: a clean exit, §7.3): the PCO named no
            // P-CSCF and nothing is configured, a fact until a boot or a
            // start. A discovery that FAILED (the reader did not answer) is
            // no fact: the fatal, and the restart runs it again.
            if (outcome == ResumeOutcome::Dead) { recovery_.OldGone(); RemoveDeadPair(pdnLocal_); }
            KeepHolds();
            if (outcome == ResumeOutcome::Failed || DiscoveryFailed()) throw std::runtime_error(std::string(NoFreshPcscf));
            RequestIdle(std::string(NoPcscfIdleText)); throw Stopping{};
        }
        // a resume that failed found the stored SAs unusable (and after a
        // reboot there are none): nothing to reconnect over while a deferred
        // fresh registration waits
        recovery_.OldGone();
        portPs_ = 0;
        local_ = pdnLocal_; ctx_.local = local_;
        KeepHolds();
        pcscf_ = pcscfTarget_;
        // the target's host route is imsd's own: the bring-up script routes the
        // first address of its own read, not a list set in /etc/imsd.env nor a
        // route lost with the PDN — the same idempotent replace as a media peer's
        RunCmd({"ip", Is6(pcscf_) ? "-6" : "-4", "route", "replace", pcscf_, "dev", dev_});
        if (recovery_.Next(Boot()).at > Boot()) Log(std::format("the network's hold stands; {}", NextStep()));
        if (!WaitForPolicy()) throw Stopping{};
        FreshRegister();
    }

    // What a bring-up's resume attempt found: the registration (resumed),
    // no pair of ours in the kernel, a pair nothing can resume, or a
    // resumable pair whose attempt failed — worth a retry.
    enum class ResumeOutcome { Done, None, Dead, Failed };

    ResumeOutcome TryResume() {
        auto state = RunCapture({"ip", "xfrm", "state"});
        auto policy = RunCapture({"ip", "xfrm", "policy"});
        // The registration was made to whatever P-CSCF the kernel's SAs name
        // (the SA setup takes imsd's previous pair out), whatever PCSCF= says
        // today: resume there and move at the next fresh registration — a
        // fresh one now would cost the registration, on KPN with the
        // 21-minute refusal. A route to it exists while the netdev lives;
        // the replace covers a target that changed under a running phone.
        auto peer = imsd::ipsec::SaPeer(policy.out);
        if (!peer) return ResumeOutcome::None;
        if (*peer != pcscf_) {
            bool listed = *peer == pcscfTarget_ || std::ranges::find(pcscfCandidates_, *peer) != pcscfCandidates_.end();
            Log(pcscfTarget_.empty()
                ? std::format("pcscf: resuming on {}, the registration's SA peer (no P-CSCF configured or discovered)", *peer)
                : std::format("pcscf: resuming on {}, the registration's SA peer; the next fresh registration goes to {}{}", *peer, pcscfTarget_, listed ? "" : " (the peer is not in today's list)"));
            pcscf_ = *peer;
        }
        auto sa = imsd::ipsec::ParseExistingSa(state.out, policy.out, pcscf_, local_);
        // The registration may sit on another address of the link: before
        // 0.3.10 imsd registered on the kernel's temporary one (ims ledger
        // F18). Resume there while it is still up — a restart, an upgrade
        // among them, keeps the registration for the price of a re-REGISTER,
        // where a fresh one is an AKA run, a new SA pair and, when KPN
        // refuses it, the old registration and a 21-minute wait (KPN
        // refuses a fresh pair on an address whose registration is live and
        // has accepted one on another address of the link meanwhile; other
        // carriers' rules are unknown) — and move at the next fresh
        // registration.
        if (!sa) {
            for (const std::string& other : imsd::ipsec::SaLocals(state.out, pcscf_)) {
                if (other == local_ || !OnLink(dev_, other)) continue;
                sa = imsd::ipsec::ParseExistingSa(state.out, policy.out, pcscf_, other);
                if (!sa) continue;
                Log(std::format("the registration's SAs are on {}, not the PDN's own address {}: resuming there until the next fresh registration", other, local_));
                local_ = other; ctx_.local = other;
                break;
            }
        }
        if (!sa) { Log(std::format("the SA pair to {} has no SA on an address of {}: nothing to resume", pcscf_, dev_)); return ResumeOutcome::Dead; }
        // the peer's host route is imsd's own once the SAs are confirmed (a peer
        // named by a pair that is not ours gets no route into the PDN), whether
        // or not the bring-up script routed the same address — idempotent
        RunCmd({"ip", Is6(pcscf_) ? "-6" : "-4", "route", "replace", pcscf_, "dev", dev_});
        auto ps = LoadState();
        if (!ps || !ps->callid) { Log(std::format("no reg state file; {}", ResumeDeadNext())); return ResumeOutcome::Dead; }
        // A file another attempt left behind — one that got through the SA
        // setup and did not live to record, to this or another P-CSCF — names
        // another SA pair than the kernel's (its spi_uc/spi_us; a 0.3.x file
        // carries them too): its dialog is that attempt's, and a Call-ID
        // cannot be rebuilt from an SA (the Security-Verify can —
        // ChooseResumeSecurityVerify).
        if (imsd::ipsec::FileNamesAnotherPair(ps->spiUc, ps->spiUs, sa->spiUc, sa->spiUs)) {
            Log(std::format("the state file names another SA pair ({:#x}/{:#x}{}) than the kernel's ({:#x}/{:#x} to {}): another attempt's; {}",
                            static_cast<std::uint32_t>(*ps->spiUc), static_cast<std::uint32_t>(*ps->spiUs),
                            ps->pcscf && !ps->pcscf->empty() ? std::format(" to {}", imsd::util::CanonicalAddr(*ps->pcscf)) : std::string(),
                            sa->spiUc, sa->spiUs, pcscf_, ResumeDeadNext()));
            return ResumeOutcome::Dead;
        }
        // The registration's transport is what the P-CSCF knows; a resume
        // must reuse it. A forced policy that disagrees means a fresh start
        // — decided before anything from the file is adopted.
        Transport t = ps->transport.value_or("TCP") == "UDP" ? Transport::Udp : Transport::Tcp;
        if (sipPolicy_ != "auto" && t != transport_) { Log(std::format("state file transport differs from SIP_TRANSPORT; {}", ResumeFailedNext())); return ResumeOutcome::Failed; }

        route_ = ps->route.value_or(std::format("<sip:{};lr>", imsd::util::HostPort(pcscf_, sa->portPs)));
        // No fallback identity: an empty ppi omits P-Preferred-Identity and
        // is re-learned from the next 200's P-Associated-URI.
        ppi_ = ps->ppi.value_or("");
        // The Security-Verify the protected REGISTERs, the reg-event
        // SUBSCRIBE and the INVITEs echo: the 401's whole Security-Server
        // list (RFC 3329 §2.3.1), from the state file since 0.3.12, or the
        // one-mechanism entry rebuilt from the SA — Imsd:Ipsec chooses
        // (ChooseResumeSecurityVerify). The rebuilt entry is KPN's answer to
        // the default offer in the same bytes, not a list such as O2's six,
        // which the RFC has the server answer with a 494; a refused resume
        // registers fresh where there is a fresh target (§7.2 step 1 otherwise).
        auto rsv = imsd::ipsec::ChooseResumeSecurityVerify(ps->securityServer.value_or(""), *sa, ealgOffer_);
        ss_ = rsv.value;
        using Source = imsd::ipsec::ResumeSecurityVerify::Source;
        switch (rsv.source) {
        case Source::Stored:
            Log(std::format("Security-Verify from the state file ({} mechanism{})", rsv.mechanisms, rsv.mechanisms == 1 ? "" : "s"));
            break;
        case Source::NoneStored:
            Log("state file holds no Security-Server (a pre-0.3.12 file, or a list the file cannot hold): Security-Verify rebuilt from the SA as one mechanism");
            break;
        case Source::OtherSa:
            Log(std::format("state file's Security-Server names another SA pair than the one in the kernel (spi-s {} vs {}): Security-Verify rebuilt from the SA as one mechanism",
                            rsv.storedSpiPs ? std::to_string(*rsv.storedSpiPs) : "none", sa->spiPs));
            break;
        }
        // The public identity our requests are signed with (From) — restored
        // here so a call placed before the refresh 200 re-learns it does not
        // fall back to the barred temporary IMPU.
        ctx_.aor = ps->aor.value_or("");
        ctx_.route = route_; ctx_.ppi = ppi_; ctx_.securityServer = ss_;
        ctx_.portUc = PortUc; ctx_.portUs = PortUs;
        Log(std::format("RESUME via existing SA (cseq {})", ps->cseq.value_or(1)));

        portPs_ = sa->portPs;
        portPc_ = static_cast<int>(IntAfter(ss_, "port-c=").value_or(0));
        SetTransport(t);
        // Local refusals (TIME_WAIT after a restart) keep retrying; two
        // silences mean the flow is not coming back — fresh instead.
        if (!sip_.Connect(local_, PortUc, pcscf_, sa->portPs, transport_, 10, 2)) {
            Log(std::format("resume connect failed; {}", ResumeFailedNext()));
            return ResumeOutcome::Failed;
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
        reg_.securityServer = ss_;
        reg_.authNonce = ps->authNonce.value_or(""); reg_.authCnonce = ps->authCnonce.value_or(""); reg_.authResponse = ps->authResponse.value_or("");
        if (reg_.authNonce.empty()) Log("state file holds no challenge answer (pre-0.3.11): the refresh carries an empty digest");
        auto [okMsg, used] = Reregister(reg_.callid, reg_.ftag, reg_.cseq + 1);
        reg_.cseq = used;
        if (auto e = imsd::sip::GrantedExpires(okMsg)) reg_.expiry = *e;
        UpdateRouteAndPpi(okMsg);
        LogBindings(okMsg);
        PersistState(reg_, route_, ppi_, ctx_.aor, ctx_.transport, pcscf_);
        Log("REGISTERED (resumed + true-refreshed)");
        return ResumeOutcome::Done;
    }

    // The registrar's 200 OK echoes EVERY current binding for the implicit
    // set — the network's own view. Log them: a stale binding at a dead
    // SLAAC address is still tried by terminating routing until it expires.
    void LogBindings(const std::string& ok) {
        for (auto ct : imsd::sip::Headers(ok, "Contact"))
            Log(std::format("binding: {}", ct));
    }

    // A fresh registration. The attempt mints its own Contact user-part into
    // ctx_ for its REGISTERs; on any failure the stored binding's comes back,
    // so requests over the existing SA meanwhile (a reconnect, a refresh)
    // carry the binding the network knows. reg_ takes the new one on the 200.
    void FreshRegister() {
        saReplaced_ = false;
        try { FreshRegisterAttempt(); }
        catch (...) { ctx_.contactUser = reg_.contactUser; throw; }
    }

    void FreshRegisterAttempt() {
        Log("FRESH registration");
        // Oracle diff (journal/ims.md s53): stock's Contact user-part is a
        // fresh UUID per registration, not the IMSI. Minted before reg1 so
        // both REGISTERs and the stored binding carry the same URI.
        std::string contactUser = imsd::util::Uuid4(rng_);
        ctx_.contactUser = contactUser;
        Log(std::format("contact user-part: {}", contactUser));
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
        std::string reg1 = imsd::msg::BuildRegisterInitial(ctx_, callidReg, ftag, rng_.Token(16), spiUc, spiUs);
        sockaddr_storage pa; socklen_t pl = MakeAddr(pcscf_, pcscfPort_, pa);
        sendto(us, reg1.data(), reg1.size(), 0, reinterpret_cast<sockaddr*>(&pa), pl);
        std::string resp = AwaitInitialFinal(us, callidReg);
        if (auto st1 = imsd::sip::Status(resp); st1 != 401) {
            DumpRaw("imsd-register-fail.raw", resp);
            std::string what = std::format("expected 401, got {}", imsd::sip::RefusalSummary(resp));
            if (st1 && *st1 >= 300) throw RefusedError(what, *st1, imsd::sip::RetryAfter(resp).value_or(0));
            throw std::runtime_error(what);
        }
        auto nonceB64 = QuotedAfter(resp, "nonce=\"");
        if (!nonceB64) throw std::runtime_error("no nonce in 401");
        auto raw = imsd::util::FromBase64(*nonceB64);
        if (!raw || raw->size() < 32) throw std::runtime_error("bad nonce");
        std::vector<std::uint8_t> rand16(raw->begin(), raw->begin() + 16);
        std::vector<std::uint8_t> autn16(raw->begin() + 16, raw->begin() + 32);
        // Every Security-Server instance is one comma-list (RFC 3261
        // §7.3.1); the joined value is what Security-Verify echoes — RFC
        // 3329 §2.2 wants the whole list back, not the chosen entry.
        auto ssLines = imsd::sip::Headers(resp, "Security-Server");
        if (ssLines.empty()) throw std::runtime_error("no Security-Server");
        ss_.clear();
        for (std::string_view line : ssLines) {
            if (!ss_.empty()) ss_ += ',';
            ss_ += line;
        }
        Log(std::format("Security-Server: {}", ss_));
        // Algorithms from the mechanism we select against our offer (RFC
        // 3329 §2.3.1), SPIs/ports from the entry carrying them (O2 lists
        // six and marks one; ims ledger F11). An answer we cannot use is a
        // deferral on the throttle schedule, not a fatal: a fatal restarts
        // imsd every RestartSec, an initial REGISTER each time.
        auto ss = imsd::ipsec::ParseSecurityServer(ss_, "hmac-sha-1-96", ealgOffer_);
        if (!ss) throw ThrottledError("Security-Server has no ipsec-3gpp mechanism");
        const imsd::ipsec::SecurityMechanism& mech = ss->Selected();
        const imsd::ipsec::SecurityMechanism& carrier = ss->Carrier();
        Log(std::format("selected mechanism {} of {}{}: {}", ss->selected + 1, ss->mechanisms.size(), ss->offerMatched ? "" : " (none matches our offer; taking the P-CSCF's)", mech.text));
        if (ss->carrier != ss->selected) Log(std::format("SPIs/ports from mechanism {}: {}", ss->carrier + 1, carrier.text));
        if (carrier.spiPs == 0u) throw ThrottledError("fresh-SA throttle active (spi-s=0)");
        if (!carrier.spiPs || !carrier.spiPc || !carrier.portPs || !carrier.portPc) throw ThrottledError("Security-Server carries no spi-c/spi-s/port-c/port-s");
        std::string alg = mech.alg.empty() ? "hmac-sha-1-96" : mech.alg;   // absent: what we offered
        std::string ealg = imsd::ipsec::NormalizeEalg(mech.ealg);          // absent: the null cipher (RFC 3329)
        if (!imsd::ipsec::KernelAuth(alg)) throw ThrottledError(std::format("Security-Server alg={} unsupported", alg));
        if (!imsd::ipsec::KnownEalg(ealg)) throw ThrottledError(std::format("Security-Server ealg={} unsupported", ealg));
        if (!mech.prot.empty() && mech.prot != "esp") throw ThrottledError(std::format("Security-Server prot={} unsupported", mech.prot));
        if (!mech.mod.empty() && mech.mod != "trans") throw ThrottledError(std::format("Security-Server mod={} unsupported", mech.mod));
        std::uint32_t spiPs = *carrier.spiPs;
        std::uint32_t spiPc = *carrier.spiPc;
        int portPs = *carrier.portPs;
        int portPc = *carrier.portPc;
        Log(std::format("SA: alg={} ealg={}{} spi-c={} spi-s={} port-c={} port-s={}", alg, ealg, mech.ealg.empty() ? " (absent)" : "", spiPc, spiPs, portPc, portPs));

        auto aka = Authenticate(rand16, autn16);
        if (!aka) throw std::runtime_error("USIM AKA failed");

        // install the IPsec SA pair
        imsd::ipsec::SaParams sp;
        sp.local = local_; sp.pcscf = pcscf_;
        sp.ik = aka->ik; sp.ck = aka->ck;
        sp.spiUc = spiUc; sp.spiUs = spiUs; sp.spiPc = spiPc; sp.spiPs = spiPs;
        sp.portUc = PortUc; sp.portUs = PortUs; sp.portPs = portPs; sp.portPc = portPc;
        sp.alg = alg;
        sp.ealg = ealg;
        // imsd's previous pair out first, each entry by its key — not `ip xfrm …
        // flush`: another IPsec user's entries (a policy-based VPN's) stay (§12)
        {
            auto state = RunCapture({"ip", "xfrm", "state"});
            auto policy = RunCapture({"ip", "xfrm", "policy"});
            RunOwnXfrmDeletes(imsd::ipsec::OwnXfrmDeleteCommands(state.out, policy.out, local_));
        }
        saReplaced_ = true;   // from here on the old registration's SAs are gone
        for (auto& cmd : imsd::ipsec::BuildSetupCommands(sp)) {
            int rc = RunCmd(cmd);
            if (rc != 0) Log(std::format("{} -> rc {}", RedactKeys(cmd), rc));
        }

        ctx_.securityServer = ss_;
        portPs_ = portPs;
        portPc_ = portPc;
        // The fallback is for a P-CSCF that never served TCP. Once a TCP
        // registration has succeeded on this phone (the state file records
        // it), a silent connect is an outage, not a policy — two unanswered
        // SYNs during a radio gap must not become a second initial REGISTER
        // (the throttle trigger on KPN).
        bool tcpKnownGood = false;
        if (auto ps = LoadState(); ps && ps->callid.has_value()) {
            std::string t = ps->transport.value_or("TCP");
            tcpKnownGood = (t == "TCP");
            // A phone whose last registration was UDP goes straight to UDP:
            // a TCP probe on every boot would be a second initial REGISTER
            // per boot on a carrier that never answers it.
            if (sipPolicy_ == "auto" && t == "UDP" && transport_ == Transport::Tcp) {
                Log("state file records a UDP registration; skipping the TCP probe");
                SetTransport(Transport::Udp);
            }
        }
        bool autoTcp = (sipPolicy_ == "auto" && transport_ == Transport::Tcp && !tcpKnownGood);
        if (!sip_.Connect(local_, PortUc, pcscf_, portPs, transport_, 10, autoTcp ? 2 : 0)) {
            if (autoTcp) {
                // Two unanswered TCP connects: treat the P-CSCF as UDP-only
                // and register again over UDP — a new challenge and a new SA
                // pair (the SA setup takes this one out), same ports.
                Log("protected TCP connect unanswered; registering fresh over UDP");
                SetTransport(Transport::Udp);
                FreshRegisterAttempt();
                return;
            }
            throw std::runtime_error(std::format("protected {} connect failed", TransportName(transport_)));
        }
        Log(std::format("protected leg over {}", TransportName(transport_)));

        std::string cnonce = rng_.Token(16);
        std::string response = imsd::aka::DigestAkav1(*nonceB64, aka->res, cnonce, ctx_.id.regUri, ctx_.id.impi, ctx_.id.domain);
        std::string auth = imsd::msg::AuthAka(ctx_, *nonceB64, cnonce, response);
        std::string reg2 = imsd::msg::BuildRegisterProtected(ctx_, callidReg, ftag, rng_.Token(16), 2, spiUc, spiUs, auth);
        if (!sip_.Send(reg2)) throw std::runtime_error("send protected REGISTER failed");
        std::string ok = AwaitFinal(callidReg, 2, "protected REGISTER");
        if (auto st = imsd::sip::Status(ok); st != 200) {
            DumpRaw("imsd-register-fail.raw", ok);
            throw RefusedError(std::format("REGISTER refused: {}", imsd::sip::RefusalSummary(ok)), *st, imsd::sip::RetryAfter(ok).value_or(0));
        }
        reg_.callid = callidReg; reg_.ftag = ftag; reg_.cseq = 2;
        reg_.contactUser = contactUser;
        reg_.spiUc = spiUc; reg_.spiUs = spiUs;
        reg_.authNonce = *nonceB64; reg_.authCnonce = cnonce; reg_.authResponse = response;
        reg_.securityServer = ss_;
        reg_.expiry = imsd::sip::GrantedExpires(ok).value_or(0);
        DumpRaw("imsd-register-200.raw", ok);
        // The Route for everything over this registration is the P-CSCF it
        // was made at — never a previous one's, after a move from the
        // resumed peer — unless the 200 carries a Service-Route, which
        // UpdateRouteAndPpi puts in its place.
        route_ = std::format("<sip:{};lr>", imsd::util::HostPort(pcscf_, portPs));
        UpdateRouteAndPpi(ok);
        LogBindings(ok);
        ctx_.route = route_; ctx_.ppi = ppi_;
        PersistState(reg_, route_, ppi_, ctx_.aor, ctx_.transport, pcscf_);
        Log("REGISTERED (fresh)");
    }

    // Closes the flow of a REGISTER wait that failed. A call that arrived
    // under the wait (AwaitFinal dispatches requests) ends with it: a 486
    // while the flow still carries one, and the bus told.
    void CloseFlowAfterWait() {
        if (call_) {
            Log(std::format("call {} arrived under a registration that failed; ending it", call_->Uni()));
            Execute(call_->OnHangup());
            MaybeDropCall();
            if (call_) { Execute(call_->Abandon("registration failed")); MaybeDropCall(); }
        }
        sip_.Close();
    }

    // The final of the REGISTER (callid, cseq) just sent, within 12 s of
    // the send or of its last provisional. A request arriving on the client
    // flow meanwhile (a NOTIFY, an OPTIONS) is dispatched and answered, not
    // read as the reply; a provisional of our own transaction keeps the
    // wait going; a response to our reg-event SUBSCRIBE is handled as the
    // run loop handles it, any other transaction's is ignored. Throws
    // when nothing answers or the flow dies under a dispatched request.
    std::string AwaitFinal(const std::string& callid, long cseq, std::string_view what) {
        std::string want = std::format("{} REGISTER", cseq);
        double deadline = Mono() + 12.0;
        for (;;) {
            double left = deadline - Mono();
            auto m = left > 0 ? sip_.RecvMsg(left) : std::nullopt;
            if (!m) throw std::runtime_error(std::format("no reply to {}", what));
            auto st = imsd::sip::Status(*m);
            if (!st) {
                reply_ = [this](const std::string& r) { return sip_.Send(r); };
                replyPath_ = "registered-flow";
                DispatchRequest(*m);
                if (!sip_.Alive()) throw std::runtime_error(std::format("flow closed while awaiting the {} reply", what));
                continue;
            }
            if (!imsd::sip::ResponseOf(*m, callid, want)) {
                if (!OnSubscribeResponse(*m, *st)) Log(std::format("ignoring a response to another transaction while awaiting the {} reply", what));
                continue;
            }
            if (*st >= 200) return *m;
            Log(std::format("{} -> {} (provisional); waiting for the final", what, *st));
            deadline = Mono() + 12.0;
        }
    }

    // The final of the unprotected REGISTER (callid, CSeq 1) on the init
    // socket, within 8 s of the send or of its last provisional. The socket
    // is bound to the init port, not connected: what else it delivers — a
    // late final of an earlier attempt, a stray datagram — is logged with
    // its source port and skipped, never judged as the reply. Closes the
    // socket; throws when nothing answers.
    std::string AwaitInitialFinal(int us, const std::string& callid) {
        double deadline = Mono() + 8.0;
        char buf[65535];
        for (;;) {
            double left = deadline - Mono();
            if (left <= 0) break;
            long sec = static_cast<long>(left);
            long usec = static_cast<long>((left - static_cast<double>(sec)) * 1e6);
            if (sec == 0 && usec == 0) usec = 1000;
            timeval tv{sec, usec};
            setsockopt(us, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
            sockaddr_storage src; socklen_t sl = sizeof src;
            ssize_t n = recvfrom(us, buf, sizeof buf, 0, reinterpret_cast<sockaddr*>(&src), &sl);
            if (n <= 0) break;
            std::string m(buf, static_cast<std::size_t>(n));
            if (!imsd::sip::ResponseOf(m, callid, "1 REGISTER")) {
                int sp = src.ss_family == AF_INET6 ? ntohs(reinterpret_cast<sockaddr_in6*>(&src)->sin6_port) : ntohs(reinterpret_cast<sockaddr_in*>(&src)->sin_port);
                std::string_view sv = m;
                Log(std::format("ignoring a datagram on the init port from port {} that is not the unprotected REGISTER's reply: {}", sp, sv.substr(0, sv.find("\r\n"))));
                continue;
            }
            int st = *imsd::sip::Status(m);
            if (st >= 200) { close(us); return m; }
            Log(std::format("unprotected REGISTER -> {} (provisional); waiting for the final", st));
            deadline = Mono() + 8.0;
        }
        close(us);
        throw std::runtime_error("no 401 to unprotected REGISTER");
    }

    // A reregistration over the (resumed or live) SA, TS 24.229 5.1.1.4.2:
    // the same Call-ID and Contact, CSeq+1, the Authorization of the last
    // successful authentication, a Security-Client with new SPIs and a new
    // client port for the SA pairs a re-authentication would set up (the
    // server port stays, TS 33.203 7.4.0), the Security-Verify (the 401's
    // list, kept in the state file since 0.3.12 so a resumed registration
    // echoes it too; a resume from an older file sends the one entry
    // rebuilt from the kernel's SA). Returns (200 msg, used cseq). A 401
    // asks for re-authentication over temporary SAs on the proposed ports,
    // which imsd does not do: it is thrown as a refusal whose recovery is a
    // fresh registration (RegRecovery's 401 rule). Every refresh of the
    // pre-0.3.11 shape on record from O2 UK — an initial registration's
    // empty digest, the in-use SPIs — was refused 503 (2026-09-24 under
    // 0.3.9, 2026-10-08 under 0.3.10) while the fresh registrations that
    // followed were accepted; KPN accepted both shapes without a challenge.
    std::pair<std::string, long> Reregister(const std::string& callid, const std::string& ftag, long cseq) {
        std::string auth = reg_.authNonce.empty() ? imsd::msg::AuthEmpty(ctx_)
                                                  : imsd::msg::AuthAka(ctx_, reg_.authNonce, reg_.authCnonce, reg_.authResponse);
        std::uint32_t spiUc = 0, spiUs = 0;
        do spiUc = rng_.UInt(0x10000, 0xFFFFFFF); while (spiUc == reg_.spiUc || spiUc == reg_.spiUs);
        do spiUs = rng_.UInt(0x10000, 0xFFFFFFF); while (spiUs == reg_.spiUc || spiUs == reg_.spiUs || spiUs == spiUc);
        std::string reg = imsd::msg::BuildRegisterProtected(ctx_, callid, ftag, rng_.Token(16), cseq, spiUc, spiUs, auth, PortUcNext);
        if (!sip_.Send(reg)) throw std::runtime_error("send re-REGISTER failed");
        std::string m = AwaitFinal(callid, cseq, "re-REGISTER");
        auto st = imsd::sip::Status(m);
        if (st == 200) return {m, cseq};
        DumpRaw("imsd-register-fail.raw", m);
        if (st == 401) throw RefusedError(std::format("re-REGISTER challenged: {}; re-authentication asked, the next registration is a fresh one", imsd::sip::RefusalSummary(m)), 401, imsd::sip::RetryAfter(m).value_or(0));
        throw RefusedError(std::format("re-REGISTER refused: {}", imsd::sip::RefusalSummary(m)), *st, imsd::sip::RetryAfter(m).value_or(0));
    }

    void UpdateRouteAndPpi(const std::string& okMsg) {
        // an empty Service-Route value carries no route: ignored like a
        // missing header, so the fallback Route the caller set stands
        auto svcAll = imsd::sip::Headers(okMsg, "Service-Route");
        decltype(svcAll) svc;
        for (auto& v : svcAll) if (!v.empty()) svc.push_back(v);
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
            auto [msg, used] = Reregister(reg_.callid, reg_.ftag, reg_.cseq + 1);
            reg_.cseq = used;
            if (auto e = imsd::sip::GrantedExpires(msg)) reg_.expiry = *e;
            PersistState(reg_, route_, ppi_, ctx_.aor, ctx_.transport, pcscf_);
            Log(std::format("keepalive re-REGISTER ok (cseq {})", used));
            OnRegistered();
        } catch (const RefusedError& e) {
            // The network refused the refresh: the recovery policy counts it
            // — a refusal with a Retry-After of at least a second, or a 401
            // (the refresh throws a challenge as a refusal), makes the fresh
            // registration the next step, behind any wait the network set; a
            // refusal with neither gets one more refresh first, a second
            // refusal then escalates — and the flow is dropped so the next
            // step runs after the wait.
            Log(std::format("keepalive re-REGISTER failed: {}", e.what()));
            CloseFlowAfterWait();
            RecoveryFailed(imsd::engine::RegRecovery::Failure::Refused, e.retryAfter, "keepalive refresh refused", e.status);
        } catch (const std::exception& e) {
            // A dead flow or a silent P-CSCF: the recovery is the same as a
            // daemon restart's — drop the flow and let MaybeReconnect re-run
            // the connect + true-refresh sequence at once.
            Log(std::format("keepalive re-REGISTER failed: {}", e.what()));
            CloseFlowAfterWait();
        }
        lastRefresh_ = Mono();
        lastKa_ = Mono();
    }

    // The protected client flow can die under us while the SA and the
    // registration stay valid — a CSFB excursion resets it, the P-CSCF drops
    // idle flows — which is exactly the state a daemon restart resumes from
    // (s51: "restart-to-recover"). Do what the restart does, in place:
    // reconnect the TCP leg over the existing SA and true-refresh. When that
    // keeps failing — the network refuses the refresh, or nothing answers —
    // do the rest of what a restart does: register fresh. RegRecovery holds
    // the thresholds and the pacing (a dead PDN must not turn this into a
    // hot loop; a carrier that refuses fresh SAs must not be hammered).
    void MaybeReconnect() {
        if (call_ || sip_.Alive() || portPs_ == 0 || reg_.callid.empty()) return;
        auto plan = recovery_.Next(Boot());
        if (plan.at > Boot()) return;
        if (plan.step == imsd::engine::RegRecovery::Step::Fresh) FreshRecover();
        else ReconnectCycle();
    }

    // The policy's next step, as the journal line: what, and when.
    std::string NextStep() {
        auto plan = recovery_.Next(Boot());
        double in = plan.at - Boot();
        std::string what = plan.step == imsd::engine::RegRecovery::Step::Fresh ? "fresh registration" : "reconnect over the existing SA";
        if (in <= 0) return std::format("next: {} now", what);
        if (in >= 60) return std::format("next: {} in {:.0f} min", what, in / 60);
        return std::format("next: {} in {:.0f} s", what, in);
    }

    // ModemManager's word on the phone's own service, as ims-pdn-up.sh reads
    // it (`mmcli -K`). False only when the modem clearly has none: a state
    // short of registered, or packet service detached. Anything else —
    // ModemManager not answering, a state or packet-service value this
    // code does not know — counts as service: the network's silence, not
    // the phone's, is the default.
    bool PacketServiceUp() {
        auto r = RunCapture({"mmcli", "-m", "any", "-K"});
        auto field = [&](std::string_view key) -> std::string {
            std::size_t at = r.out.find(key);
            if (at == std::string::npos) return {};
            std::size_t end = r.out.find('\n', at);
            if (end == std::string::npos) end = r.out.size();
            std::size_t colon = r.out.find(':', at);
            if (colon == std::string::npos || colon > end) return {};
            std::string v = r.out.substr(colon + 1, end - colon - 1);
            while (!v.empty() && v.front() == ' ') v.erase(v.begin());
            while (!v.empty() && (v.back() == ' ' || v.back() == '\r')) v.pop_back();
            return v;
        };
        std::string state = field("modem.generic.state "), ps = field("modem.3gpp.packet-service-state ");
        for (const char* down : {"failed", "initializing", "locked", "disabled", "disabling", "enabling", "enabled", "searching"})
            if (state == down) return false;
        return ps != "detached";
    }

    // A cycle that failed while the modem reports no packet service is the
    // phone's silence, not the network's: not counted, retried on the
    // local-refusal pace like a connect() the kernel refused — a radio gap
    // across a refresh must not escalate to a fresh registration, which KPN
    // refuses while the old one is live, and drops it for.
    void CoverageGap(std::string_view what) {
        if (registered_) { registered_ = false; EmitStatus(true); }
        recovery_.OnLocalRefusal(Boot());
        Log(std::format("{} while the modem reports no packet service: not counted; {}", what, NextStep()));
    }

    void ReconnectCycle() {
        using F = imsd::engine::RegRecovery::Failure;
        Log("client flow dead; reconnecting");
        sip_.Close();
        if (!sip_.Connect(local_, PortUc, pcscf_, portPs_, transport_, /*tries=*/2)) {
            if (AddressGone()) return;
            if (sip_.LastConnectLocal()) {
                if (registered_) { registered_ = false; EmitStatus(true); }
                recovery_.OnLocalRefusal(Boot());
                return;
            }
            if (!PacketServiceUp()) { CoverageGap("P-CSCF silent"); return; }
            RecoveryFailed(F::Silent, 0, "P-CSCF silent");
            return;
        }
        try {
            RefreshPani();
            auto [msg, used] = Reregister(reg_.callid, reg_.ftag, reg_.cseq + 1);
            reg_.cseq = used;
            if (auto e = imsd::sip::GrantedExpires(msg)) reg_.expiry = *e;
            UpdateRouteAndPpi(msg);
            LogBindings(msg);
            PersistState(reg_, route_, ppi_, ctx_.aor, ctx_.transport, pcscf_);
            Log("reconnected + re-registered");
            OnRegistered();
        } catch (const RefusedError& e) {
            Log(std::format("reconnect re-REGISTER failed: {}", e.what()));
            CloseFlowAfterWait();
            RecoveryFailed(F::Refused, e.retryAfter, "refresh refused", e.status);
        } catch (const std::exception& e) {
            Log(std::format("reconnect re-REGISTER failed: {}", e.what()));
            CloseFlowAfterWait();
            // the binding terminated under the wait (the flow closed, or
            // under a call the wait timed out): that verdict named the next
            // step, this is no silence
            if (!recovery_.Reconnectable()) return;
            if (!PacketServiceUp()) { CoverageGap("refresh unanswered"); return; }
            RecoveryFailed(F::Silent, 0, "refresh unanswered");
        }
    }

    // A failed reconnect + refresh cycle: the status goes unregistered, the
    // policy decides whether the next step is another cycle or a fresh
    // registration, and when.
    void RecoveryFailed(imsd::engine::RegRecovery::Failure f, long retryAfter, std::string_view what, int status = 0) {
        if (registered_) { registered_ = false; EmitStatus(true); }
        bool wasReconnectable = recovery_.Reconnectable();
        recovery_.OnReconnectFailure(Boot(), f, retryAfter, status);
        KeepHolds();
        Log(std::format("{} ({} refused, {} silent cycles){}; {}", what, recovery_.refusals, recovery_.silentCycles,
                        wasReconnectable && !recovery_.Reconnectable() ? std::format(" — the old registration is gone ({})", status) : "", NextStep()));
    }

    // The rest of what a restart does, in place: a new Call-ID, a new
    // challenge, a new SA pair (FreshRegister takes the old ones out), then
    // the post-registration steps. A refused or throttled attempt waits on
    // the throttle schedule (a wait no Dial cuts short — on KPN a refused
    // attempt costs the old registration) and a silent one on the
    // short backoff. The attempt's flow is closed whichever way it failed;
    // past the SA setup the old registration's SAs are gone with it, so
    // nothing is retried until the next fresh attempt.
    void FreshRecover() {
        using F = imsd::engine::RegRecovery::Failure;
        Log(std::format("registering fresh (recovery attempt {})", recovery_.freshAttempts + 1));
        sip_.Close();
        saReplaced_ = false;   // this attempt's, whatever fails first
        // the subscription dialog is the registration's and goes with the
        // attempt; a retry the network scheduled (its Retry-After) stands
        // until a fresh registration replaces the old one; an attempt that
        // fails before the SA setup leaves the old registration and that
        // retry standing
        subCallid_.clear(); subPending_ = false; subRenewAt_ = 0;
        // a registration resumed on an older address (a pre-0.3.10 temporary
        // one) moves to the PDN's own address here, server ports included;
        // an attempt that fails before the SA setup leaves that registration
        // standing, so it moves back to it — the P-CSCF too, once the address
        // verdict below says the registration still stands
        if (pcscfTarget_.empty()) { RequestRestart(std::string(NoFreshPcscf)); return; }
        std::string fromPcscf = pcscf_;
        if (pcscf_ != pcscfTarget_) {
            Log(std::format("the fresh registration moves from P-CSCF {} to {}", pcscf_, pcscfTarget_));
            pcscf_ = pcscfTarget_;
        }
        std::string from = local_;
        if (local_ != pdnLocal_) {
            Log(std::format("the fresh registration moves from {} to the PDN's own address {}", local_, pdnLocal_));
            MoveTo(pdnLocal_);
        }
        // the fresh target's host route, imsd's own (BringUp's fresh path does the same)
        RunCmd({"ip", Is6(pcscf_) ? "-6" : "-4", "route", "replace", pcscf_, "dev", dev_});
        bool ok = false;
        try {
            RefreshPani();
            FreshRegister();
            subRetryAt_ = 0;   // a new registration: its subscription starts afresh
            OnRegistered();
            ok = true;
            Log("recovered by a fresh registration");
        } catch (const ThrottledError& e) {
            FreshAttemptFailed(F::Throttled, 0, std::format("fresh registration deferred: {}", e.what()));
        } catch (const RefusedError& e) {
            FreshAttemptFailed(F::Refused, e.retryAfter, std::format("fresh registration refused: {}", e.what()));
        } catch (const std::exception& e) {
            FreshAttemptFailed(F::Silent, 0, std::format("fresh registration failed: {}", e.what()));
        }
        if (!ok && !quit_.load() && recovery_.Reconnectable() && local_ != from) {
            if (OnLink(dev_, from)) {
                Log(std::format("back on {}: the old registration stands until the next fresh attempt", from));
                MoveTo(from);
            } else {
                recovery_.OldGone();
                KeepHolds();   // the verdict outlives a restart like the hold it goes with
                Log(std::format("{} has left {}: the registration on it is gone; staying on {}; {}", from, dev_, local_, NextStep()));
            }
        }
        // after the address verdict: a registration just declared gone has
        // no P-CSCF to go back to
        if (!ok && !quit_.load() && recovery_.Reconnectable() && pcscf_ != fromPcscf) {
            Log(std::format("back to P-CSCF {}: the old registration stands until the next fresh attempt", fromPcscf));
            pcscf_ = fromPcscf;
        }
    }

    // Our address for everything from here on: the flow, the server ports,
    // the requests' Via and Contact, the media leg.
    void MoveTo(const std::string& addr) {
        server_.CloseAll();
        local_ = addr; ctx_.local = addr;
        server_.Open(local_);
    }

    // After a failed fresh attempt, in recovery or at bring-up: the
    // attempt's flow closed (a refused protected REGISTER leaves it open),
    // the policy told, and its next step logged.
    void FreshAttemptFailed(imsd::engine::RegRecovery::Failure f, long retryAfter, const std::string& what) {
        CloseFlowAfterWait();
        if (!OnLink(dev_, local_)) Log(what);   // said before the address's own line: a refusal is always in the journal
        if (AddressGone()) return;
        bool wasReconnectable = recovery_.Reconnectable();
        recovery_.OnFreshFailure(Boot(), f, retryAfter, saReplaced_);
        KeepHolds();
        Log(std::format("{}{}; {}", what, wasReconnectable && !recovery_.Reconnectable() ? " — the old registration is gone" : "", NextStep()));
    }

    // After a failed recovery step: has our address left the link? An
    // older registration's address (a pre-0.3.10 temporary one that
    // expired) takes that registration with it: imsd moves to the PDN's
    // own address and the policy's fresh step runs there, every wait it
    // holds included. The PDN's own address leaving means the PDN went
    // away — no retry brings it back; a restart runs the PDN bring-up
    // again. True when the step that failed has nothing left to do.
    bool AddressGone() {
        if (OnLink(dev_, local_)) return false;
        if (local_ != pdnLocal_ && OnLink(dev_, pdnLocal_)) {
            if (registered_) { registered_ = false; EmitStatus(true); }
            std::string from = local_;
            recovery_.OldGone();
            MoveTo(pdnLocal_);
            KeepHolds();   // the caller returns before its own write
            Log(std::format("{} is no longer on {}: the registration on it is gone; on the PDN's own address {} now; {}", from, dev_, local_, NextStep()));
            return true;
        }
        RequestRestart(std::format("{} is no longer on {}", local_, dev_));
        return true;
    }

    // Leave through systemd (Restart=on-failure), which runs ims-pdn-up.sh
    // again and registers on the PDN's own address.
    void RequestRestart(const std::string& why) {
        quit_.store(true);
        auto ev = std::make_unique<Event>();
        ev->type = Event::Type::Fatal; ev->text = why;
        PostEvent(std::move(ev));
    }

    // Leave cleanly: nothing to do (§7.3); the
    // unit stays inactive until the next boot, a `systemctl start imsd` or a
    // start of imsd-ofonod (its Wants=imsd.service, the Phosh image).
    void RequestIdle(const std::string& why) {
        quit_.store(true);
        auto ev = std::make_unique<Event>();
        ev->type = Event::Type::Idle; ev->text = why;
        PostEvent(std::move(ev));
    }

    // After a registration succeeds — bring-up, a keepalive refresh, a
    // reconnect + refresh, or a fresh recovery: the status (a signal only
    // when it changes), the timers, the recovery policy, the reg-event
    // subscription.
    void OnRegistered() {
        recovery_.OnSuccess();
        closeFlowWhenIdle_ = false;   // this registration's flow stands, whatever a verdict said of the one before
        KeepHolds();
        bool was = registered_;
        registered_ = true;
        EmitStatus(!was);
        lastRefresh_ = Mono(); lastKa_ = Mono();
        // a refresh or a reconnect opens no subscription dialog while imsd
        // holds one for the registration or has a retry scheduled; a fresh
        // registration (or a start) has none yet, and a fresh attempt drops
        // the one it holds before it sends. A retry already scheduled (the
        // network's Retry-After, the backoff) is MaybeResubscribe's to send,
        // not a registration's to bring forward — nor to start its backoff
        // over: that resets with a subscription that starts afresh here, or
        // is accepted
        // Luma: the subscription is opt-in (REG_EVENT=1). T-Mobile US answers
        // it 500 and then closes the otherwise healthy protected TCP flow, so
        // retrying it as part of being registered would cycle the flow.
        if (!RegEventEnabled()) return;
        if (subCallid_.empty() && !subPending_ && subRetryAt_ == 0) { subRetryDelay_ = 60; SubscribeRegEvent(); }
    }

    // Subscribe to the reg event package (TS 24.229 5.1.1.3): one dialog per
    // registration, sent over the registration flow; the S-CSCF's immediate
    // NOTIFY (logged in DispatchRequest) is the network's own view of our
    // bindings. Failure is non-fatal — registration is unaffected.
    void SubscribeRegEvent() {
        subRetryAt_ = 0; subRenewAt_ = 0;
        subCallid_ = std::format("{}@{}", rng_.Token(16), local_);
        std::string sub = imsd::msg::BuildSubscribeReg(ctx_, subCallid_, rng_.Token(8), rng_.Token(16));
        if (sip_.Send(sub)) { Log("reg-event SUBSCRIBE sent"); subPending_ = true; subSentAt_ = Mono(); }
        else { Log("reg-event SUBSCRIBE send failed"); subCallid_.clear(); ScheduleResubscribe(0); }
    }

    // A response on the client flow that belongs to our reg-event
    // SUBSCRIBE, wherever the flow is read (the run loop, a REGISTER
    // wait). False when it is not ours.
    bool OnSubscribeResponse(const std::string& msg, int st) {
        if (subCallid_.empty() || imsd::sip::Header(msg, "Call-ID") != subCallid_) return false;
        if (!imsd::sip::Header(msg, "CSeq").value_or("").ends_with("SUBSCRIBE")) return false;
        if (st >= 300) {
            Log(std::format("reg-event SUBSCRIBE refused: {}", imsd::sip::RefusalSummary(msg)));
            DumpRaw("imsd-subscribe-fail.raw", msg);
            subPending_ = false; subCallid_.clear();
            ScheduleResubscribe(imsd::sip::RetryAfter(msg).value_or(0));
        } else {
            Log(std::format("reg-event SUBSCRIBE -> {}", st));
            if (st >= 200) {
                DumpRaw("imsd-subscribe-200.raw", msg); subPending_ = false; subRetryDelay_ = 60;
                if (auto e = imsd::sip::GrantedExpires(msg)) ArmSubscriptionRenewal(*e);
            }
        }
        return true;
    }

    // The subscription is part of being registered (TS 24.229 5.1.1.3): a
    // refused or unanswered SUBSCRIBE is retried, 60 s doubling to 10 min
    // (a Retry-After wins), and a subscription the S-CSCF ends is renewed.
    // Until now one failure left the daemon deaf to the network's view of
    // its binding — O2 UK 2026-09-24: SUBSCRIBE 500, the registration
    // killed 8 s later, nothing heard.
    void ScheduleResubscribe(long retryAfter) {
        double wait = std::max(subRetryDelay_, static_cast<double>(std::min(retryAfter, imsd::engine::RegRecovery::RetryAfterMax)));
        subRetryDelay_ = std::min(subRetryDelay_ * 2, 600.0);
        subRetryAt_ = Mono() + wait;
        Log(std::format("reg-event: next SUBSCRIBE in {:.0f} s", wait));
    }
    void MaybeResubscribe() {
        if (!RegEventEnabled()) return;
        if (subPending_ && Mono() - subSentAt_ > 32) {   // Timer F: no final at all
            Log("reg-event SUBSCRIBE unanswered");
            subPending_ = false; subCallid_.clear();
            ScheduleResubscribe(0);
        }
        if (call_ || !registered_ || !sip_.Alive()) return;
        if (!subCallid_.empty() && !subPending_ && subRenewAt_ > 0 && Mono() >= subRenewAt_) {
            // the granted lifetime is running out: a new dialog (the S-CSCF
            // closes the previous one — KPN with noresource), on the same
            // registration
            Log("reg-event: the subscription nears its expiry; subscribing anew");
            subCallid_.clear(); subRenewAt_ = 0;
            SubscribeRegEvent();
            return;
        }
        if (subRetryAt_ == 0 || Mono() < subRetryAt_) return;
        SubscribeRegEvent();
    }

    // The network granted the subscription `expires` seconds (a SUBSCRIBE
    // 2xx, a NOTIFY's Subscription-State): subscribe anew 10 min before the
    // end, or halfway through a shorter grant.
    void ArmSubscriptionRenewal(long expires) {
        double ahead = expires > 1200 ? expires - 600.0 : expires / 2.0;
        subRenewAt_ = Mono() + std::max(ahead, 30.0);
        Log(std::format("reg-event: subscription granted {} s; renewed in {:.0f} s", expires, subRenewAt_ - Mono()));
    }

    // The network's view of our binding (RFC 3680 reginfo), read from any
    // reg-event NOTIFY — a subscription an earlier process left behind
    // carries the same account (O2, 2026-09-24: the previous dialog's NOTIFY
    // reached a restarted daemon whose own SUBSCRIBE had just got 500). A
    // binding the registrar terminated needs a new registration: the
    // policy decides when (TS 24.229 5.4.1.5, 5.1.1.7 — now for deactivated
    // and expired, after retry-after for probation, on the refusal schedule
    // for rejected and unregistered). Active or not listed: nothing to do.
    // Subscription state is per dialog: only our live dialog's end is ours
    // to renew, unless the binding went with it.
    void OnRegEventNotify(const std::string& msg, std::string_view body) {
        using S = imsd::sip::RegInfoContact::State;
        auto c = imsd::sip::RegInfoContactState(body, imsd::msg::ContactUser(ctx_));
        bool bindingGone = false;
        if (c.state == S::Terminated) {
            bindingGone = true;
            if (registered_) { registered_ = false; EmitStatus(true); }
            // a call lives on the client flow: the flow closes when the
            // call is dropped, not under it — the call goes on, or the
            // network releases it, and the fresh step follows the call as
            // every other recovery step does
            if (call_) closeFlowWhenIdle_ = true;
            else
                sip_.Close();
            subCallid_.clear(); subPending_ = false; subRetryAt_ = 0; subRenewAt_ = 0;
            recovery_.OnBindingTerminated(Boot(), c.event, c.retryAfter);
            KeepHolds();
            Log(std::format("reg-event: our binding is terminated ({}); {}", c.event, NextStep()));
        } else if (c.state == S::Active) {
            Log("reg-event: our binding is active");
        }
        auto ss = imsd::sip::Header(msg, "Subscription-State");
        std::string_view callid = imsd::sip::Header(msg, "Call-ID").value_or("");
        bool ours = !subCallid_.empty() && callid == subCallid_;
        if (ss && ss->starts_with("active") && ours)
            if (auto e = IntAfter(*ss, "expires=")) ArmSubscriptionRenewal(*e);
        if (ss && ss->starts_with("terminated") && !ours && !bindingGone) {
            Log(std::format("reg-event: an earlier subscription ({}) ended ({}); ours ({}) stands", callid, *ss, subCallid_.empty() ? "none" : subCallid_));
        } else if (ss && ss->starts_with("terminated") && !bindingGone) {
            // RFC 6665 §4.1.3: renew after deactivated, timeout, giveup and
            // probation (after its retry-after, on the 60 s retry floor); not after rejected
            // or noresource. KPN's S-CSCF ends a registration's previous
            // subscription with noresource when a new one replaces it (dev
            // phone 2026-09-25, Call-IDs compared); the 2026-09-24 reading
            // that it ends every subscription came from opening a new one
            // at every refresh and taking that closure for our own
            std::string reason;
            if (auto r = ss->find("reason="); r != std::string_view::npos) {
                std::string_view v = ss->substr(r + 7);
                reason = std::string(v.substr(0, v.find_first_of(";, \t\r")));
            }
            subCallid_.clear(); subPending_ = false; subRenewAt_ = 0;
            if (reason == "deactivated" || reason == "timeout" || reason == "giveup" || reason == "probation") {
                Log(std::format("reg-event: subscription ended ({}); renewing", *ss));
                ScheduleResubscribe(IntAfter(*ss, "retry-after=").value_or(0));
            } else {
                Log(std::format("reg-event: subscription ended ({}); the next refresh subscribes anew", *ss));
            }
        }
    }

    // ---- command handling -------------------------------------------------
    // A Dial while a call exists: refused, unless it is an emergency dial,
    // which preempts whatever exists (TS 22.101) — released, and abandoned
    // if it lingers awaiting a CANCEL's 487 (the late final is ignored by
    // Call-ID mismatch). False when the dial was refused.
    bool ClearForDial(const Cmd& c, bool emergency) {
        if (!call_) return true;
        if (!emergency) {
            Log(std::format("dial({}) refused: call in progress", c.arg));
            CallInfo bad{c.uni, c.arg, "terminated", "error", NowEpoch(), 0};
            PostAdded(bad);
            PostState(c.uni, "terminated", "error");
            PostDeleted(c.uni);
            return false;
        }
        Log(std::format("emergency dial: preempting call {}", call_->Uni()));
        Execute(call_->OnHangup());
        MaybeDropCall();
        if (call_) { Execute(call_->Abandon("preempted by emergency dial")); MaybeDropCall(); }
        return true;
    }

    void HandleCmd(const Cmd& c) {
        if (c.kind == Cmd::Kind::Stop) { quit_.store(true); return; }
        if (c.kind == Cmd::Kind::Dial) {
            bool emergency = imsd::msg::IsEmergency(c.arg, emergencyExtra_);
            if (!ClearForDial(c, emergency)) return;
            // a dead client flow fails the INVITE instantly — give the
            // recovery path one immediate shot first: user action cuts the
            // backoff short, never a wait the network imposed (a
            // Retry-After, the schedule after a refused or throttled fresh
            // attempt, the one after a binding the network rejected or
            // unregistered, probation's retry-after — the policy keeps those).
            // The shot's REGISTER wait dispatches
            // requests, so an incoming call may have arrived under it: the
            // same rule again.
            if (!sip_.Alive()) {
                recovery_.OnDial(); MaybeReconnect();
                if (!ClearForDial(c, emergency)) return;
            }
            RefreshPani();
            if (emergency)
                Log(std::format("EMERGENCY dial ({}): urn:service:sos over the live registration, "
                                "fallback plain INVITE — stage 1, no emergency registration/PDN; "
                                "carrier-side behavior UNVERIFIED", c.arg));
            call_.emplace(ctx_, rng_, c.uni, c.arg, rtpPort_, precond_, emergency, codecs_);
            CallInfo info{c.uni, c.arg, "dialing", "outgoing", NowEpoch(), 0};
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
        } else if (c.kind == Cmd::Kind::Dtmf) {
            if (call_ && call_->Uni() == c.uni) Execute(call_->OnDtmf(c.arg));
            else Log(std::format("SendDtmf({}, '{}') ignored: no such call", c.uni, c.arg));
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
            else if (auto ev = imsd::sip::Header(msg, "Event"); ev && ev->starts_with("reg")) {
                Log(std::format("reg-event NOTIFY ({} bytes):\n{}", body.size(), body));
                if (reply_) reply_(imsd::msg::BuildResponse200(ctx_, msg));
                OnRegEventNotify(msg, body);
                return;
            }
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
        call_.emplace(ctx_, rng_, uni, imsd::engine::IncomingInvite{msg}, rtpPort_, codecs_);
        inviteReply_ = reply_;
        inviteReplyPath_ = replyPath_;
        initialInviteCseq_ = std::string(imsd::sip::Header(msg, "CSeq").value_or(""));
        Log(std::format("incoming call {} from {}", uni, call_->Number()));
        // Validate the offer BEFORE the UI hears of the call. A refused
        // INVITE (488: nothing we can play) is answered and dropped without
        // ever announcing it — announcing first produced a 24 ms
        // added/terminated/deleted burst that left Plasma Dialer stuck as a
        // lock-screen overlay with no call to show (field report 2026-09-01).
        // The journal keeps the record: caller and reason are logged above
        // and by the engine's teardown line.
        auto actions = call_->OnInvite();
        if (call_->Terminated()) {
            Execute(actions, /*announce=*/false);
            MaybeDropCall();
            return;
        }
        CallInfo info{uni, call_->Number(), "incoming", "incoming", NowEpoch(), 0,
                      "incoming"};
        PostAdded(info);
        Execute(actions);
        if (call_ && autoAnswer_) {
            Log("bounded synchronous auto-answer");
            Trace("Accept invoked (synchronous diagnostic)");
            Execute(call_->OnAccept());
        }
        MaybeDropCall();
    }

    // ---- action execution -------------------------------------------------
    // Returns false if a client-flow send failed (used to fail a dial's INVITE).
    // `announce` false suppresses the D-Bus state/deleted events — for a call
    // that was never announced (refused before ringing), so the bus never
    // sees a call it cannot show.
    bool Execute(const std::vector<imsd::engine::Action>& actions, bool announce = true) {
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
                case T::SendDtmf: SendDtmfToMedia(a.text); break;
                case T::State:
                    if (announce) PostState(call_->Uni(), a.state, a.reason);
                    break;
                case T::Deleted:
                    if (announce) PostDeleted(call_->Uni());
                    dropCall_ = true;
                    break;
                case T::SetDeadline: deadline_ = Mono() + a.seconds; break;
                case T::Log: Log(a.text); break;
            }
        }
        return sendOk;
    }

    void MaybeDropCall() {
        if (!dropCall_) return;
        call_.reset(); inviteReply_ = nullptr; dropCall_ = false;
        inviteReplyPath_.clear();
        initialInviteCseq_.clear();
        callTraceStart_ = 0;
        if (closeFlowWhenIdle_) {
            closeFlowWhenIdle_ = false;
            Log("the call is over: closing the flow of the binding the network terminated under it");
            sip_.Close();
        }
    }

    void StartMedia(const imsd::engine::MediaLeg& leg) {
        if (!leg.Valid()) { Log("SDP answer missing media endpoint; no media leg"); return; }
        Log(std::format("media {} pt={} octet={} -> [{}]:{}, telephone-event pt {}{}", leg.codec, leg.payloadType, leg.octetAlign, leg.remoteIp, leg.remotePort,
                        leg.dtmfPt ? std::to_string(*leg.dtmfPt) : std::string("none"),
                        leg.dtmfRxPt && leg.dtmfRxPt != leg.dtmfPt ? std::format(" (theirs {})", *leg.dtmfRxPt) : std::string()));
        if (Is6(leg.remoteIp)) RunCmd({"ip", "-6", "route", "replace", leg.remoteIp, "dev", dev_});
        else
            RunCmd({"ip", "route", "replace", leg.remoteIp, "dev", dev_});
        std::string outBase = std::format("{}/dialer_{}", outDir_, call_ ? call_->Uni() : std::string("x"));
        std::vector<std::string> argv = {
            mediaBin_, local_, std::to_string(rtpPort_), leg.remoteIp,
            std::to_string(leg.remotePort), std::to_string(leg.payloadType),
            "86400", outBase};
        AudioSession session;
        if (geteuid() == 0) {
            session = ResolveAudioSession();
            Log(AudioSessionLine(session) + (session.name.empty() ? "; this call has no audio" : ""));
        }
        // the control pipe: imsd-media reads `dtmf <tones>` lines on its
        // stdin (§7.6); our end closes with the leg, no other child inherits
        // it, and it never blocks the engine thread — a leg that stopped
        // reading fails the write (EAGAIN once the pipe is full) instead
        int ctl[2] = {-1, -1};
        if (pipe(ctl) != 0) {
            Log(std::format("media control pipe: {}; this call has no DTMF", std::strerror(errno)));
        } else {
            fcntl(ctl[0], F_SETFD, FD_CLOEXEC);
            fcntl(ctl[1], F_SETFD, FD_CLOEXEC);
            fcntl(ctl[1], F_SETFL, O_NONBLOCK);
        }
        pid_t pid = fork();
        if (pid == 0) {
            if (ctl[0] >= 0) dup2(ctl[0], STDIN_FILENO);   // dup2 clears the close-on-exec
            if (geteuid() == 0) {
                // no session is said with an empty value: imsd-media reads a
                // missing variable as a caller older than 0.3.10
                setenv("AUDIO_USER", session.name.c_str(), 1);
            }
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
            setenv("CODEC", leg.codec.c_str(), 1);
            setenv("DTMF_PT", leg.dtmfPt ? std::to_string(*leg.dtmfPt).c_str() : "", 1);
            setenv("DTMF_RX_PT", leg.dtmfRxPt ? std::to_string(*leg.dtmfRxPt).c_str() : "", 1);
            std::vector<char*> c;
            for (auto& s : argv) c.push_back(const_cast<char*>(s.c_str()));
            c.push_back(nullptr);
            execvp(c[0], c.data());
            _exit(127);
        }
        if (ctl[0] >= 0) close(ctl[0]);
        if (pid < 0 && ctl[1] >= 0) { close(ctl[1]); ctl[1] = -1; }
        mediaPid_ = pid;
        mediaCtl_ = ctl[1];
    }

    // The engine's SendDtmf action: one `dtmf <tones>` line down the
    // control pipe, whole or not at all — a write of at most PIPE_BUF bytes
    // to a pipe is atomic, and the engine's DtmfMaxTones keeps the line
    // under it (a longer line is refused here too, never half-written to
    // be joined with the next). The leg may have died since the action was
    // decided (its exit is reaped on this thread, after the actions ran):
    // then the write fails with EPIPE — SIGPIPE is ignored in main() — and
    // the tones are lost with the call, which the reap ends.
    void SendDtmfToMedia(const std::string& tones) {
        if (mediaCtl_ < 0) { Log(std::format("DTMF '{}' not sent: no control pipe to the media leg", tones)); return; }
        const std::string line = std::format("dtmf {}\n", tones);
        if (line.size() > PIPE_BUF) { Log(std::format("DTMF of {} tones not sent: longer than the control pipe's atomic write", tones.size())); return; }
        const ssize_t n = write(mediaCtl_, line.data(), line.size());
        if (n != static_cast<ssize_t>(line.size())) Log(std::format("DTMF '{}' not sent: {}", tones, n < 0 ? std::strerror(errno) : "short write to the media leg"));
    }

    void CloseMediaCtl() {
        if (mediaCtl_ >= 0) close(mediaCtl_);
        mediaCtl_ = -1;
    }

    void StopMedia() {
        CloseMediaCtl();
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
        CloseMediaCtl();
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
        const gchar* uni = nullptr;
        const gchar* tones = nullptr;
        g_variant_get(params, "(&s&s)", &uni, &tones);
        TheEngine->SendDtmf(uni ? uni : "", tones ? tones : "");
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

// Reads stay open. The restricted emergency ABI is not polkit-gated either:
// an emergency call must never depend on a session being active. The bus
// policy limits who may send it, and the daemon validates the number and the
// call's provenance itself (DialEmergency, HangUpEmergency).
bool IsUngated(std::string_view method) {
    return method == "GetStatus" || method == "GetCalls" ||
           method == "DialEmergency" || method == "HangUpEmergency" ||
           method == "GetEmergencyCallState";
}
constexpr char ErrAccessDenied[] = "org.freedesktop.DBus.Error.AccessDenied";
const GDBusInterfaceVTable Vtable = { imsd::bus::Gated<HandleMethodCall, IsUngated, ErrAccessDenied, Log>, nullptr, nullptr, {} };

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
    // A write to the media leg's control pipe after the leg died must fail
    // with EPIPE, not end the daemon (the client flow's sends carry
    // MSG_NOSIGNAL; an inbound connection's reply is a plain write and is
    // covered by this too).
    signal(SIGPIPE, SIG_IGN);

    // The bring-up found nothing to do (IMSD_IDLE= in /run/imsd.env — the
    // script's arms: no SIM, a refused ims PDN): say so and
    // leave cleanly. Restart=on-failure leaves a clean exit alone, so the
    // unit stays inactive until the next boot, a manual start or a start of
    // imsd-ofonod (its Wants=imsd.service).
    if (std::string idle = EnvOr("IMSD_IDLE", ""); !idle.empty()) {
        Log(std::format("idle: {} — nothing to do; systemctl start imsd when that changes",
                        idle == "no-sim" ? "no SIM" : idle == "no-ims-pdn" ? "no ims PDN from the network" : idle));
        return 0;
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
    local = imsd::util::CanonicalAddr(local);
    std::string freshPcscf = FreshPcscfFromEnv();
    Log(std::format("LOCAL={} P-CSCF=[{}]:{}", local, freshPcscf.empty() ? "(none)" : freshPcscf, EnvOr("PCSCF_PORT", "5060")));
    // The daemon's own dead end, decided here, before the bus name, so the
    // exit is clean and the unit stays inactive (§7.3): nothing to register
    // to — no PCSCF=, nothing discovered — and no registration of its own to
    // resume (SaPeer finds no pair, or the holds' write-off for this PDN and
    // boot says it cannot). The script decides the dead ends only it can see
    // (no SIM, a refused ims PDN). A discovery that failed is no dead end:
    // exit 1, and the unit's restart runs it again.
    if (!session && freshPcscf.empty()) {
        auto policy = RunCapture({"ip", "xfrm", "policy"});
        auto holds = LoadHolds(local);
        auto peer = imsd::ipsec::SaPeer(policy.out);
        const bool writtenOff = holds && holds->oldGone;
        if (!peer || writtenOff) {
            // a pair the holds wrote off before this start is nothing to
            // resume: taken out of the kernel here, as the no-target bring-up
            // takes a dead pair out (§7.2 step 1), so no start of this boot
            // finds it — the holds keep the write-off
            if (peer && writtenOff) RemoveDeadPair(local);
            if (DiscoveryFailed()) { Log(std::format("{} — exiting for systemd restart", NoPcscfDiscoveryFailed)); return 1; }
            Log(std::format("idle: {} — nothing to do; systemctl start imsd when that changes", NoPcscfIdleText)); return 0;
        }
    }
    if (!session) LogAudioSession();
    imsd::bus::AuthorizeCalls = !session;

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
