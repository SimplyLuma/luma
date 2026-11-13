// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types no-char-pointer
/*
imsd-pcscf — prints the P-CSCF list the network delivered in the ims PDN's
Protocol Configuration Options, read from the modem over QMI.

ModemManager owns the data call and never asks the modem for these fields;
a second WDS client gets them once it is bound to the same mux data port
(the binding ModemManager gives its own clients), so this is a reader on
the side: Bind Mux Data Port, Set IP Family 6, Get Current Settings, over
one AF_QIPCRTR socket, which is its own QMI client as far as the modem is
concerned — no proxy, no interference with ModemManager's. It never sends
Start Network or Stop Network: it changes nothing about the call. The
bytes are Imsd:Qmi's; this file is the socket, the service lookup and the
sequence.

Usage: imsd-pcscf --mux <id> [--ep-type 4] [--ep-iface 1] [--node 0]
                  [--profile <idx>] [--timeout <s>] [-v]
  --mux      the rmnet mux id of the ims netdev (`ip -d link show <dev>`)
  --ep-type, --ep-iface
             the modem's data endpoint: embedded (4), interface 1 for an
             IPA-attached modem (what ModemManager binds for the `ipa`
             driver)
  --node     the QRTR node of the modem's WDS service (0)
  --profile  the ims profile's 3GPP index: adds the profile's "PCSCF Address
             Using PCO" flag to the output (did the modem ask the network at
             all?)
  --timeout  the whole run's budget in seconds (10)
  -v         every request and answer in hex, on stderr
Output, one item per line:
  <ipv6 address>                      the P-CSCF list, PCO order
  v4 <address>                        an IPv4 P-CSCF (unusable on an IPv6 PDN)
  fqdn <name>                         a P-CSCF name
  flag pco-requested <0|1>            the call's TLV 0x22
  flag profile-pcscf-via-pco <0|1>    the profile's flag (with --profile)
Exit: 0 at least one IPv6 address; 2 none; 1 an error (text on stderr —
      `OutOfCall` means the bound client does not see a call on that mux).
*/

#include <errno.h>
#include <linux/qrtr.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

import std;
import Imsd;

namespace {
    bool Verbose = false;

    void Err(std::string_view s) { std::println(std::cerr, "imsd-pcscf: {}", s); }

    std::string Hex(std::span<const std::uint8_t> b) {
        std::string s;
        for (std::uint8_t x : b) s += std::format("{}{:02x}", s.empty() ? "" : " ", x);
        return s;
    }

    long NowMs() {
        return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
    }

    // Ask the control port for the WDS servers; the first one on `node`.
    // (The lookup is sent to node 1's control port — the local node on this
    // platform — and to our own node's when that differs, as the modem's
    // control port answers for the whole bus.)
    std::optional<sockaddr_qrtr> FindWds(std::uint32_t node, long deadlineMs) {
        int s = socket(AF_QIPCRTR, SOCK_DGRAM, 0);
        if (s < 0) { Err(std::format("socket(AF_QIPCRTR): {}", std::strerror(errno))); return std::nullopt; }
        qrtr_ctrl_pkt pkt{};
        pkt.cmd = QRTR_TYPE_NEW_LOOKUP;
        pkt.server.service = imsd::qmi::WdsService;
        sockaddr_qrtr ctrl{};
        ctrl.sq_family = AF_QIPCRTR;
        ctrl.sq_node = 1;
        ctrl.sq_port = QRTR_PORT_CTRL;
        (void)!sendto(s, &pkt, sizeof pkt, 0, reinterpret_cast<sockaddr*>(&ctrl), sizeof ctrl);
        sockaddr_qrtr me{};
        socklen_t ml = sizeof me;
        if (getsockname(s, reinterpret_cast<sockaddr*>(&me), &ml) == 0 && me.sq_node != 1) {
            ctrl.sq_node = me.sq_node;
            (void)!sendto(s, &pkt, sizeof pkt, 0, reinterpret_cast<sockaddr*>(&ctrl), sizeof ctrl);
        }
        std::optional<sockaddr_qrtr> found;
        while (true) {
            long left = deadlineMs - NowMs();
            if (left <= 0) break;
            pollfd p{s, POLLIN, 0};
            if (poll(&p, 1, static_cast<int>(std::min(left, 2000L))) <= 0) break;
            qrtr_ctrl_pkt in{};
            sockaddr_qrtr from{};
            socklen_t fl = sizeof from;
            ssize_t n = recvfrom(s, &in, sizeof in, 0, reinterpret_cast<sockaddr*>(&from), &fl);
            if (n < static_cast<ssize_t>(sizeof in)) continue;
            if (in.cmd != QRTR_TYPE_NEW_SERVER) continue;
            if (in.server.service == 0) break;   // end of the dump
            if (Verbose) Err(std::format("WDS server: instance {} at {}:{}", static_cast<unsigned>(in.server.instance), static_cast<unsigned>(in.server.node), static_cast<unsigned>(in.server.port)));
            if (in.server.service == imsd::qmi::WdsService && in.server.node == node && !found) {
                sockaddr_qrtr a{};
                a.sq_family = AF_QIPCRTR;
                a.sq_node = in.server.node;
                a.sq_port = in.server.port;
                found = a;
            }
        }
        close(s);
        return found;
    }

    // One QMI client: one socket, one transaction at a time.
    class Wds {
    public:
        Wds(sockaddr_qrtr peer, long deadlineMs) : peer_(peer), deadline_(deadlineMs) { fd_ = socket(AF_QIPCRTR, SOCK_DGRAM, 0); }
        ~Wds() { if (fd_ >= 0) close(fd_); }
        bool Open() const { return fd_ >= 0; }
        std::uint16_t NextTxn() { return ++txn_; }

        // Send a request and wait for its response (same transaction and
        // message id); indications and strays are skipped.
        std::optional<imsd::qmi::Response> Call(std::span<const std::uint8_t> req, std::uint16_t msg, std::string_view name) {
            if (Verbose) Err(std::format("--> {}: {}", name, Hex(req)));
            if (sendto(fd_, req.data(), req.size(), 0, reinterpret_cast<sockaddr*>(&peer_), sizeof peer_) < 0) {
                Err(std::format("{}: sendto: {}", name, std::strerror(errno)));
                return std::nullopt;
            }
            std::vector<std::uint8_t> buf(65536);
            while (true) {
                long left = deadline_ - NowMs();
                if (left <= 0) { Err(std::format("{}: no answer in time", name)); return std::nullopt; }
                pollfd p{fd_, POLLIN, 0};
                int pr = poll(&p, 1, static_cast<int>(left));
                if (pr <= 0) { Err(std::format("{}: no answer in time", name)); return std::nullopt; }
                ssize_t n = recv(fd_, buf.data(), buf.size(), 0);
                if (n <= 0) continue;
                auto r = imsd::qmi::ParseMessage(std::span<const std::uint8_t>(buf.data(), static_cast<std::size_t>(n)));
                if (!r) continue;
                if (r->type != imsd::qmi::TypeResponse || r->txn != txn_ || r->msg != msg) {
                    if (Verbose) Err(std::format("    (skipped type {} txn {} msg 0x{:04x})", r->type, r->txn, r->msg));
                    continue;
                }
                if (Verbose) Err(std::format("<-- {}: {}", name, Hex(std::span<const std::uint8_t>(buf.data(), static_cast<std::size_t>(n)))));
                return r;
            }
        }

    private:
        int fd_ = -1;
        sockaddr_qrtr peer_;
        long deadline_;
        std::uint16_t txn_ = 0;
    };

    void Usage() {
        std::println(std::cerr, "usage: imsd-pcscf --mux <id> [--ep-type 4] [--ep-iface 1] [--node 0] [--profile <idx>] [--timeout <s>] [-v]");
    }
}

int main(int argc, char** argv) {
    std::optional<int> mux;
    std::optional<int> profile;
    std::uint32_t epType = imsd::qmi::EndpointEmbedded;
    std::uint32_t epIface = imsd::qmi::EndpointInterfaceIpa;
    std::uint32_t node = 0;
    double timeout = 10.0;
    auto num = [&](int& i, const char* what) -> std::optional<long> {
        if (i + 1 >= argc) { Err(std::format("{} needs a value", what)); return std::nullopt; }
        std::string_view v(argv[++i]);
        long out = 0;
        auto [p, ec] = std::from_chars(v.data(), v.data() + v.size(), out);
        if (ec != std::errc{} || p != v.data() + v.size()) { Err(std::format("{}: not a number: {}", what, v)); return std::nullopt; }
        return out;
    };
    for (int i = 1; i < argc; i++) {
        std::string_view a(argv[i]);
        std::optional<long> v;
        if (a == "--mux") { if (!(v = num(i, "--mux"))) return 1; mux = static_cast<int>(*v); }
        else if (a == "--profile") { if (!(v = num(i, "--profile"))) return 1; profile = static_cast<int>(*v); }
        else if (a == "--ep-type") { if (!(v = num(i, "--ep-type"))) return 1; epType = static_cast<std::uint32_t>(*v); }
        else if (a == "--ep-iface") { if (!(v = num(i, "--ep-iface"))) return 1; epIface = static_cast<std::uint32_t>(*v); }
        else if (a == "--node") { if (!(v = num(i, "--node"))) return 1; node = static_cast<std::uint32_t>(*v); }
        else if (a == "--timeout") {
            if (i + 1 >= argc) { Err("--timeout needs a value"); return 1; }
            timeout = std::atof(argv[++i]);
            if (timeout <= 0) { Err("--timeout: not a positive number"); return 1; }
        }
        else if (a == "-v") Verbose = true;
        else { Usage(); return 1; }
    }
    if (!mux || *mux < 0 || *mux > 255) { Usage(); return 1; }
    if (profile && (*profile < 0 || *profile > 255)) { Err("--profile: 0..255"); return 1; }

    long deadline = NowMs() + static_cast<long>(timeout * 1000);
    auto peer = FindWds(node, deadline);
    if (!peer) { Err(std::format("no WDS service on qrtr node {}", node)); return 1; }
    Wds c(*peer, deadline);
    if (!c.Open()) { Err(std::format("socket(AF_QIPCRTR): {}", std::strerror(errno))); return 1; }

    auto step = [&](std::vector<std::uint8_t> req, std::uint16_t msg, std::string_view name) -> std::optional<imsd::qmi::Response> {
        auto r = c.Call(req, msg, name);
        if (!r) return std::nullopt;
        if (!r->Ok()) {
            Err(std::format("{}: error {} ({})", name, r->error, imsd::qmi::ErrorName(r->error)));
            return std::nullopt;
        }
        return r;
    };
    using namespace imsd::qmi;
    if (!step(BindMuxDataPort(c.NextTxn(), epType, epIface, static_cast<std::uint8_t>(*mux)), MsgBindMuxDataPort, "Bind Mux Data Port")) return 1;
    if (!step(SetIpFamily(c.NextTxn(), IpFamilyV6), MsgSetIpFamily, "Set IP Family")) return 1;
    if (Verbose) {
        if (auto r = step(GetPacketServiceStatus(c.NextTxn()), MsgGetPacketServiceStatus, "Get Packet Service Status"))
            Err(std::format("packet service status: {}", PacketServiceStatus(*r) ? std::to_string(*PacketServiceStatus(*r)) : "?"));
    }
    auto settings = c.Call(GetCurrentSettings(c.NextTxn(), RequestedSettingsAll), MsgGetCurrentSettings, "Get Current Settings");
    if (!settings) return 1;
    if (!settings->Ok()) {
        if (settings->error == ErrOutOfCall) Err(std::format("Get Current Settings: OutOfCall — the client bound to mux {} sees no call there (is that the ims netdev's mux id?)", *mux));
        else Err(std::format("Get Current Settings: error {} ({})", settings->error, ErrorName(settings->error)));
        return 1;
    }
    PcscfLists lists = ParsePcscfLists(*settings);
    for (const std::string& a : lists.ipv6) std::println("{}", a);
    for (const std::string& a : lists.ipv4) std::println("v4 {}", a);
    for (const std::string& n : lists.fqdn) std::println("fqdn {}", n);
    std::println("flag pco-requested {}", lists.viaPco ? 1 : 0);
    if (profile) {
        auto r = c.Call(GetProfileSettings(c.NextTxn(), static_cast<std::uint8_t>(*profile)), MsgGetProfileSettings, "Get Profile Settings");
        if (r && r->Ok()) {
            if (auto f = ProfilePcscfViaPco(*r)) std::println("flag profile-pcscf-via-pco {}", *f ? 1 : 0);
            else Err(std::format("Get Profile Settings: profile {} carries no PCSCF-via-PCO flag", *profile));
        } else if (r) {
            Err(std::format("Get Profile Settings: error {} ({})", r->error, ErrorName(r->error)));
        }
    }
    return lists.ipv6.empty() ? 2 : 0;
}
