// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
/*
Imsd:Ipsec — IMS security-association plumbing expressed as `ip xfrm` command
lines, plus a reader for an SA already installed in the kernel.

A registered IMS UE keeps two ESP SA pairs with the P-CSCF (TS 33.203):
reqid 1 carries the UE-initiated client flow (protected client port ⇄ P-CSCF
server port), reqid 2 the terminating server flow. Integrity is HMAC-SHA1-96
keyed with IK; confidentiality is the negotiated ealg keyed with CK. This
module turns the negotiated SPIs/ports/keys into the exact argv lists the
daemon shell hands to `ip`, and — for a warm resume — reconstructs the SPIs,
ports, and Security-Server value back out of `ip xfrm state`/`policy` text so
a refresh re-REGISTER can advertise the SPIs actually in use.

Pure std C++ (the shell runs the commands and captures the text). Command
shapes and the parser are pinned by tests/Ipsec.
*/

export module Imsd:Ipsec;
import std;
import :Util;

export namespace imsd::ipsec {

    // UE protected ports are fixed by the stack; P-CSCF ports come from the
    // Security-Server the network offered (fresh) or the kernel (resume).
    struct SaParams {
        std::string local;      // UE global IPv6/4 on the ims PDN
        std::string pcscf;      // P-CSCF address
        std::vector<std::uint8_t> ik;  // integrity key (auth)
        std::vector<std::uint8_t> ck;  // cipher key (enc)
        std::uint32_t spiUc = 0;  // UE client  inbound  SA (P->UE, reqid 1)
        std::uint32_t spiUs = 0;  // UE server  inbound  SA (P->UE, reqid 2)
        std::uint32_t spiPc = 0;  // P-CSCF client outbound SA (UE->P, reqid 2)
        std::uint32_t spiPs = 0;  // P-CSCF server outbound SA (UE->P, reqid 1)
        int portUc = imsd::util::PortUc;  // UE protected client port
        int portUs = imsd::util::PortUs;  // UE protected server port
        int portPs = 0;         // P-CSCF server port (UE connects its TCP here)
        int portPc = 0;         // P-CSCF client port
        std::string ealg = "aes-cbc";  // aes-cbc | des-ede3-cbc/3des | null
    };

    namespace detail {
        // ESP cipher argv triple for `ip xfrm state add ... <enc>`, matching
        // imscall.setup_sa: aes-cbc keyed with CK; 3DES uses CK||CK[:8] to
        // reach 24 bytes; anything else is the null cipher (empty key).
        inline std::vector<std::string> EncArgs(const SaParams& p) {
            if (p.ealg == "aes-cbc") return {"enc", "cbc(aes)", std::format("0x{}", imsd::util::ToHex(p.ck))};
            if (p.ealg == "des-ede3-cbc" || p.ealg == "3des") {
                std::vector<std::uint8_t> k = p.ck;
                k.insert(k.end(), p.ck.begin(), p.ck.begin() + 8);
                return {"enc", "cbc(des3_ede)", std::format("0x{}", imsd::util::ToHex(k))};
            }
            return {"enc", "cipher_null", ""};
        }

        inline std::vector<std::string> StateAdd(std::string_view src, std::string_view dst, std::uint32_t spi, int reqid, std::string_view ikx, const std::vector<std::string>& enc) {
            std::vector<std::string> a = {
                "ip", "xfrm", "state", "add",
                "src", std::string(src), "dst", std::string(dst),
                "proto", "esp", "spi", std::to_string(spi),
                "mode", "transport", "reqid", std::to_string(reqid),
                "auth-trunc", "hmac(sha1)", std::string(ikx), "96"};
            a.insert(a.end(), enc.begin(), enc.end());
            return a;
        }

        inline std::vector<std::string> PolicyAdd(std::string_view src, std::string_view dst, int plen, int sport, int dport, std::string_view dir, std::string_view tsrc, std::string_view tdst, int reqid) {
            return {
                "ip", "xfrm", "policy", "add",
                "src", std::format("{}/{}", src, plen),
                "dst", std::format("{}/{}", dst, plen),
                "sport", std::to_string(sport), "dport", std::to_string(dport),
                "dir", std::string(dir),
                "tmpl", "src", std::string(tsrc), "dst", std::string(tdst),
                "proto", "esp", "reqid", std::to_string(reqid),
                "mode", "transport"};
        }
    }

    // The full `ip xfrm` argv sequence to install a fresh SA pair: flush
    // state+policy, add the four ESP states, add the four transport policies.
    // Order matches imscall.setup_sa exactly.
    std::vector<std::vector<std::string>> BuildSetupCommands(const SaParams& p) {
        const std::string& local = p.local;
        const std::string& pcscf = p.pcscf;
        std::string ikx = std::format("0x{}", imsd::util::ToHex(p.ik));
        std::vector<std::string> enc = detail::EncArgs(p);
        int plen = imsd::util::Is6(local) ? 128 : 32;
        std::vector<std::vector<std::string>> cmds;
        cmds.push_back({"ip", "xfrm", "state", "flush"});
        cmds.push_back({"ip", "xfrm", "policy", "flush"});
        cmds.push_back(detail::StateAdd(local, pcscf, p.spiPs, 1, ikx, enc));
        cmds.push_back(detail::StateAdd(pcscf, local, p.spiUc, 1, ikx, enc));
        cmds.push_back(detail::StateAdd(local, pcscf, p.spiPc, 2, ikx, enc));
        cmds.push_back(detail::StateAdd(pcscf, local, p.spiUs, 2, ikx, enc));
        cmds.push_back(detail::PolicyAdd(local, pcscf, plen, p.portUc, p.portPs, "out", local, pcscf, 1));
        cmds.push_back(detail::PolicyAdd(pcscf, local, plen, p.portPs, p.portUc, "in",  pcscf, local, 1));
        cmds.push_back(detail::PolicyAdd(local, pcscf, plen, p.portUs, p.portPc, "out", local, pcscf, 2));
        cmds.push_back(detail::PolicyAdd(pcscf, local, plen, p.portPc, p.portUs, "in",  pcscf, local, 2));
        return cmds;
    }

    std::vector<std::vector<std::string>> BuildTeardownCommands() {
        return {{"ip", "xfrm", "state", "flush"}, {"ip", "xfrm", "policy", "flush"}};
    }

    // What a warm SA in the kernel tells us — enough for a refresh re-REGISTER
    // to re-bind without re-authenticating.
    struct ExistingSa {
        std::string securityServer;  // reconstructed Security-Server header value
        int portPs = 0;
        int portPc = 0;
        std::uint32_t spiUc = 0;     // our inbound client SPI (advertise on refresh)
        std::uint32_t spiUs = 0;     // our inbound server SPI
        std::uint32_t spiPs = 0;
        std::uint32_t spiPc = 0;
    };

    namespace detail {
        // The whitespace-delimited token immediately after `key` (which should
        // include its trailing space, e.g. "spi "); empty if `key` is absent.
        inline std::string_view TokenAfter(std::string_view text, std::string_view key) {
            std::size_t at = text.find(key);
            if (at == std::string_view::npos) return {};
            std::size_t start = at + key.size();
            std::size_t end = text.find_first_of(" \t\r\n", start);
            return text.substr(start, end == std::string_view::npos ? std::string_view::npos : end - start);
        }

        // Split into blocks, each starting at a line-anchored "src " and
        // running to just before the next one.
        inline std::vector<std::string_view> SrcBlocks(std::string_view text) {
            std::vector<std::string_view> blocks;
            std::size_t i = 0;
            std::optional<std::size_t> cur;
            std::size_t pos = 0;
            while (pos <= text.size()) {
                std::size_t nl = text.find('\n', pos);
                std::string_view line = text.substr(pos, nl == std::string_view::npos ? std::string_view::npos : nl - pos);
                if (line.starts_with("src ")) {
                    if (cur) blocks.push_back(text.substr(*cur, pos - *cur));
                    cur = pos;
                }
                if (nl == std::string_view::npos) break;
                pos = nl + 1;
            }
            if (cur) blocks.push_back(text.substr(*cur));
            (void)i;
            return blocks;
        }

        inline std::optional<std::uint32_t> ParseHex32(std::string_view tok) {
            if (tok.starts_with("0x") || tok.starts_with("0X")) tok.remove_prefix(2);
            if (tok.empty()) return std::nullopt;
            std::uint32_t v = 0;
            auto [p, ec] = std::from_chars(tok.data(), tok.data() + tok.size(), v, 16);
            if (ec != std::errc{} || p != tok.data() + tok.size()) return std::nullopt;
            return v;
        }

        inline std::optional<int> ParseDec(std::string_view tok) {
            int v = 0;
            auto [p, ec] = std::from_chars(tok.data(), tok.data() + tok.size(), v);
            if (ec != std::errc{} || p != tok.data() + tok.size()) return std::nullopt;
            return v;
        }
    }

    // Reconstruct SA facts from `ip xfrm state` + `ip xfrm policy` text.
    // Mapping (matches imscall.parse_existing_sa):
    //   state: (src==LOCAL,dst==P): reqid1->spiPs reqid2->spiPc
    //          (src==P,dst==LOCAL): reqid1->spiUc reqid2->spiUs
    //   policy: dir out, reqid1->portPs=dport, reqid2->portPc=dport
    // Returns nullopt unless spiPs, spiPc, portPs, portPc are all found.
    std::optional<ExistingSa> ParseExistingSa(std::string_view stateText, std::string_view policyText, std::string_view pcscf, std::string_view local) {
        ExistingSa sa;
        bool havePs = false;
        bool havePc = false;
        bool havePortPs = false;
        bool havePortPc = false;
        for (std::string_view b : detail::SrcBlocks(stateText)) {
            std::string_view src = detail::TokenAfter(b, "src ");
            std::string_view dst = detail::TokenAfter(b, "dst ");
            auto spi = detail::ParseHex32(detail::TokenAfter(b, "spi "));
            auto reqid = detail::ParseDec(detail::TokenAfter(b, "reqid "));
            if (!spi || !reqid) continue;
            if (src == local && dst == pcscf && *reqid == 1) { sa.spiPs = *spi; havePs = true; }
            if (src == local && dst == pcscf && *reqid == 2) { sa.spiPc = *spi; havePc = true; }
            if (src == pcscf && dst == local && *reqid == 1) sa.spiUc = *spi;
            if (src == pcscf && dst == local && *reqid == 2) sa.spiUs = *spi;
        }
        for (std::string_view b : detail::SrcBlocks(policyText)) {
            if (b.find("dir out") == std::string_view::npos) continue;
            auto dport = detail::ParseDec(detail::TokenAfter(b, "dport "));
            auto reqid = detail::ParseDec(detail::TokenAfter(b, "reqid "));
            if (!dport || !reqid) continue;
            if (*reqid == 1) { sa.portPs = *dport; havePortPs = true; }
            else if (*reqid == 2) { sa.portPc = *dport; havePortPc = true; }
        }
        if (!(havePs && havePc && havePortPs && havePortPc)) return std::nullopt;
        sa.securityServer = std::format("ipsec-3gpp; q=0.1; alg=hmac-sha-1-96; ealg=aes-cbc; " "spi-c={}; spi-s={}; port-c={}; port-s={}", sa.spiPc, sa.spiPs, sa.portPc, sa.portPs);
        return sa;
    }
}
