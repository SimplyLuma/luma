// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
// Imsd:Ipsec unit tests — the `ip xfrm` setup command sequence (byte-exact
// argv), the warm-SA reader, fed `ip xfrm state`/`policy` output in the
// exact shape captured from a registered FP6 (addresses/keys synthetic), and
// the Security-Server parser, fed the operator lines as captured (O2 UK's
// six-mechanism Mavenir shape verbatim, KPN's single mechanism).
import std;
import Imsd;

using namespace imsd::ipsec;

namespace {
    int Failures = 0;
    void Check(bool cond, std::string_view msg) {
        if (!cond) {
            std::println(std::cerr, "FAIL: {}", msg);
            ++Failures;
        }
    }

    std::string Join(const std::vector<std::string>& v) {
        std::string s;
        for (const auto& t : v) { s += t; s += ' '; }
        if (!s.empty()) s.pop_back();
        return s;
    }

    // `ip xfrm state` in the shape of a registered FP6's kernel state
    // (P-CSCF ::105, UE ...db43).
    constexpr std::string_view State =
        "src 2001:db8::105 dst 2001:db8:1111:2222:3333:4444:5555:db43\n"
        "\tproto esp spi 0x0a84ab2f reqid 2 mode transport\n"
        "\treplay-window 0 \n"
        "\tauth-trunc hmac(sha1) 0x00112233445566778899aabbccddeeff 96\n"
        "\tenc cbc(aes) 0xffeeddccbbaa99887766554433221100\n"
        "src 2001:db8:1111:2222:3333:4444:5555:db43 dst 2001:db8::105\n"
        "\tproto esp spi 0x0526f2c1 reqid 2 mode transport\n"
        "\tenc cbc(aes) 0xffeeddccbbaa99887766554433221100\n"
        "src 2001:db8::105 dst 2001:db8:1111:2222:3333:4444:5555:db43\n"
        "\tproto esp spi 0x0071acd4 reqid 1 mode transport\n"
        "\tlastused 2026-07-19 20:42:01\n"
        "src 2001:db8:1111:2222:3333:4444:5555:db43 dst 2001:db8::105\n"
        "\tproto esp spi 0x0526f2c0 reqid 1 mode transport\n"
        "\tlastused 2026-07-19 20:42:01\n";

    constexpr std::string_view Policy =
        "src 2001:db8::105/128 dst 2001:db8:1111:2222:3333:4444:5555:db43/128 sport 33421 dport 45062 \n"
        "\tdir in priority 0 \n"
        "\ttmpl src 2001:db8::105 dst 2001:db8:1111:2222:3333:4444:5555:db43\n"
        "\t\tproto esp reqid 2 mode transport\n"
        "src 2001:db8:1111:2222:3333:4444:5555:db43/128 dst 2001:db8::105/128 sport 45062 dport 33421 \n"
        "\tdir out priority 0 \n"
        "\ttmpl src 2001:db8:1111:2222:3333:4444:5555:db43 dst 2001:db8::105\n"
        "\t\tproto esp reqid 2 mode transport\n"
        "src 2001:db8::105/128 dst 2001:db8:1111:2222:3333:4444:5555:db43/128 sport 6000 dport 45061 \n"
        "\tdir in priority 0 \n"
        "\ttmpl src 2001:db8::105 dst 2001:db8:1111:2222:3333:4444:5555:db43\n"
        "\t\tproto esp reqid 1 mode transport\n"
        "src 2001:db8:1111:2222:3333:4444:5555:db43/128 dst 2001:db8::105/128 sport 45061 dport 6000 \n"
        "\tdir out priority 0 \n"
        "\ttmpl src 2001:db8:1111:2222:3333:4444:5555:db43 dst 2001:db8::105\n"
        "\t\tproto esp reqid 1 mode transport\n";
}

int main() {
    // ---- warm-SA reader against real device output
    {
        auto sa = ParseExistingSa(State, Policy, "2001:db8::105", "2001:db8:1111:2222:3333:4444:5555:db43");
        Check(sa.has_value(), "existing SA parses");
        Check(sa && sa->portPs == 6000, "port-s (reqid1 out dport) = 6000");
        Check(sa && sa->portPc == 33421, "port-c (reqid2 out dport) = 33421");
        Check(sa && sa->spiUc == 0x0071acd4u, "spi_uc (P->UE reqid1)");
        Check(sa && sa->spiUs == 0x0a84ab2fu, "spi_us (P->UE reqid2)");
        Check(sa && sa->spiPs == 0x0526f2c0u, "spi_ps (UE->P reqid1)");
        Check(sa && sa->spiPc == 0x0526f2c1u, "spi_pc (UE->P reqid2)");
        Check(sa && sa->securityServer == "ipsec-3gpp; q=0.1; alg=hmac-sha-1-96; ealg=aes-cbc; " "spi-c=86438593; spi-s=86438592; port-c=33421; port-s=6000", "reconstructed Security-Server value");
    }
    // the environment's spelling must not matter: upper case, uncompressed,
    // bracketed — the kernel prints RFC 5952 form and both sides are
    // canonicalised before the compare
    {
        auto sa = ParseExistingSa(State, Policy, "2001:DB8::105", "2001:DB8:1111:2222:3333:4444:5555:DB43");
        Check(sa && sa->spiPs == 0x0526f2c0u && sa->portPs == 6000, "ParseExistingSa: upper-case spellings find the SAs");
        auto sa2 = ParseExistingSa(State, Policy, "[2001:0db8:0000:0000:0000:0000:0000:0105]", "2001:db8:1111:2222:3333:4444:5555:db43");
        Check(sa2 && sa2->spiPc == 0x0526f2c1u, "ParseExistingSa: uncompressed, bracketed P-CSCF finds the SAs");
        Check(!ParseExistingSa(State, Policy, "2001:db8::106", "2001:db8:1111:2222:3333:4444:5555:db43"), "ParseExistingSa: another P-CSCF still finds nothing");
        auto locals = SaLocals(State, "2001:DB8:0:0:0:0:0:105");
        Check(locals.size() == 1 && locals[0] == "2001:db8:1111:2222:3333:4444:5555:db43", "SaLocals: any spelling of the P-CSCF, canonical locals out");
    }
    // incomplete input -> nullopt
    Check(!ParseExistingSa("src a dst b\n\tspi 0x1 reqid 1 mode transport\n", "", "b", "a").has_value(), "missing pieces -> nullopt");
    {
        // a registration still on another address of the link, plus an SA to
        // another P-CSCF that is none of ours
        std::string two = std::format("{}src 2001:db8:1111:2222:aaaa:bbbb:cccc:dddd dst 2001:db8::105\n\tproto esp spi 0x00000001 reqid 1 mode transport\nsrc 2001:db8:1111:2222:eeee:ffff:0:1 dst 2001:db8::999\n\tproto esp spi 0x00000002 reqid 1 mode transport\n", State);
        auto locals = SaLocals(two, "2001:db8::105");
        Check(locals.size() == 2 && locals[0] == "2001:db8:1111:2222:3333:4444:5555:db43" && locals[1] == "2001:db8:1111:2222:aaaa:bbbb:cccc:dddd", "SaLocals: every local address with an SA to the P-CSCF, once each, in kernel order");
        Check(SaLocals(State, "2001:db8::999").empty(), "SaLocals: no SA to that P-CSCF -> none");
    }

    // ---- SaPeer: the P-CSCF the kernel's policies point at
    {
        auto peer = SaPeer(Policy);
        Check(peer && *peer == "2001:db8::105", "SaPeer: the dir-out policies' dst with reqid 1 and 2 agreeing");
        Check(!SaPeer("").has_value(), "SaPeer: no policies -> none");
        // a VPN's: tunnel mode, reqid 1, another destination — alone, and beside our pair
        constexpr std::string_view Vpn = "src 2001:db8::1/128 dst 2001:db8::2/128 \n\tdir out priority 0 \n\ttmpl src 2001:db8::1 dst 2001:db8::2\n\t\tproto esp reqid 1 mode tunnel\n";
        constexpr std::string_view Vpn4 = "src 192.0.2.10/32 dst 0.0.0.0/0 \n\tdir out priority 383615 ptype main \n\ttmpl src 192.0.2.10 dst 192.0.2.1\n\t\tproto esp reqid 1 mode tunnel\n";
        Check(!SaPeer(Vpn).has_value(), "SaPeer: a lone reqid-1 policy (a VPN's) is not ours");
        auto beside = SaPeer(std::string(Policy) + std::string(Vpn));
        Check(beside && *beside == "2001:db8::105", "SaPeer: our pair beside a VPN's reqid-1 tunnel policy -> ours");
        auto beside4 = SaPeer(std::string(Vpn4) + std::string(Policy));
        Check(beside4 && *beside4 == "2001:db8::105", "SaPeer: our pair beside an IPv4 VPN's policy to 0.0.0.0/0 -> ours");
        // a transport policy to another address beside the pair: one address still carries both reqids
        const std::string other2 = "src 2001:db8:1111:2222:3333:4444:5555:db43/128 dst 2001:db8::106/128 sport 1 dport 2 \n\tdir out priority 0 \n\ttmpl src 2001:db8:1111:2222:3333:4444:5555:db43 dst 2001:db8::106\n\t\tproto esp reqid 2 mode transport\n";
        const std::string other1 = "src 2001:db8:1111:2222:3333:4444:5555:db43/128 dst 2001:db8::106/128 sport 3 dport 4 \n\tdir out priority 0 \n\ttmpl src 2001:db8:1111:2222:3333:4444:5555:db43 dst 2001:db8::106\n\t\tproto esp reqid 1 mode transport\n";
        auto one = SaPeer(std::string(Policy) + other2);
        Check(one && *one == "2001:db8::105", "SaPeer: a reqid-2 policy to another address beside the pair -> the pair's");
        Check(SaPeer(std::string(Policy) + other2 + other1) == "2001:db8::105", "SaPeer: another transport-mode reqid 1 + 2 pair beside ours, on other ports -> ours (the protected ports are the fingerprint)");
        Check(!SaPeer(other2 + other1).has_value(), "SaPeer: a transport-mode reqid 1 + 2 pair on other ports alone -> none (not imsd's)");
        const std::string ours2 = "src 2001:db8:1111:2222:3333:4444:5555:db43/128 dst 2001:db8::106/128 sport 45062 dport 2 \n\tdir out priority 0 \n\ttmpl src 2001:db8:1111:2222:3333:4444:5555:db43 dst 2001:db8::106\n\t\tproto esp reqid 2 mode transport\n"
                                 "src 2001:db8:1111:2222:3333:4444:5555:db43/128 dst 2001:db8::106/128 sport 45061 dport 4 \n\tdir out priority 0 \n\ttmpl src 2001:db8:1111:2222:3333:4444:5555:db43 dst 2001:db8::106\n\t\tproto esp reqid 1 mode transport\n";
        Check(!SaPeer(std::string(Policy) + ours2).has_value(), "SaPeer: two addresses each carrying imsd's fingerprint -> none (not one pair to tell apart)");
        // the fingerprint alone is not a pair: a reqid-1 policy without its reqid-2 partner, and two fingerprinted policies naming different addresses
        const std::string lone1 = "src 2001:db8:1111:2222:3333:4444:5555:db43/128 dst 2001:db8::107/128 sport 45061 dport 4 \n\tdir out priority 0 \n\ttmpl src 2001:db8:1111:2222:3333:4444:5555:db43 dst 2001:db8::107\n\t\tproto esp reqid 1 mode transport\n";
        const std::string apart2 = "src 2001:db8:1111:2222:3333:4444:5555:db43/128 dst 2001:db8::108/128 sport 45062 dport 2 \n\tdir out priority 0 \n\ttmpl src 2001:db8:1111:2222:3333:4444:5555:db43 dst 2001:db8::108\n\t\tproto esp reqid 2 mode transport\n";
        Check(!SaPeer(lone1).has_value(), "SaPeer: a lone fingerprinted reqid-1 policy (no reqid-2 partner) -> none");
        Check(!SaPeer(lone1 + apart2).has_value(), "SaPeer: a fingerprinted reqid-1 policy to one address and a fingerprinted reqid-2 policy to another -> none (not one pair)");
        Check(SaPeer(std::string(Policy) + apart2) == "2001:db8::105", "SaPeer: a fingerprinted reqid-2 policy to another address beside our pair -> the pair's");
        Check(!SaPeer(std::string(Vpn) + "src 2001:db8::1/128 dst 2001:db8::2/128 \n\tdir out priority 0 \n\ttmpl src 2001:db8::1 dst 2001:db8::2\n\t\tproto esp reqid 2 mode tunnel\n").has_value(), "SaPeer: a tunnel-mode reqid 1 + 2 pair (a VPN's two child SAs) -> none");
        // transport mode is part of the fingerprint: a tunnel-mode reqid 1 + 2 pair carrying the protected ports is not ours
        const std::string tunnelOut1 = "src 2001:db8:1111:2222:3333:4444:5555:db43/128 dst 2001:db8::109/128 sport 45061 dport 4 \n\tdir out priority 0 \n\ttmpl src 2001:db8:1111:2222:3333:4444:5555:db43 dst 2001:db8::109\n\t\tproto esp reqid 1 mode tunnel\n";
        const std::string tunnelOut2 = "src 2001:db8:1111:2222:3333:4444:5555:db43/128 dst 2001:db8::109/128 sport 45062 dport 2 \n\tdir out priority 0 \n\ttmpl src 2001:db8:1111:2222:3333:4444:5555:db43 dst 2001:db8::109\n\t\tproto esp reqid 2 mode tunnel\n";
        Check(!SaPeer(tunnelOut1 + tunnelOut2).has_value(), "SaPeer: a tunnel-mode reqid 1 + 2 pair carrying the protected ports -> none (transport mode is part of the fingerprint)");
        Check(SaPeer(std::string(Policy) + tunnelOut1 + tunnelOut2) == "2001:db8::105", "SaPeer: the same beside our pair -> ours");
        Check(SaPeer(State).has_value() == false, "SaPeer: state text (no dir out) -> none");
    }

    // ---- OwnXfrmDeleteCommands: imsd's own entries out of the kernel by exact key; everything else stays
    {
        const std::string ue = "2001:db8:1111:2222:3333:4444:5555:db43";
        // a policy-based VPN's: a tunnel policy (v6, reqid 1), an IPv4 one to 0.0.0.0/0, a tunnel SA
        const std::string vpn = "src 2001:db8::1/128 dst 2001:db8::2/128 \n\tdir out priority 0 \n\ttmpl src 2001:db8::1 dst 2001:db8::2\n\t\tproto esp reqid 1 mode tunnel\n";
        const std::string vpn4 = "src 192.0.2.10/32 dst 0.0.0.0/0 \n\tdir out priority 383615 ptype main \n\ttmpl src 192.0.2.10 dst 192.0.2.1\n\t\tproto esp reqid 1 mode tunnel\n";
        const std::string tunState = "src 2001:db8::1 dst 2001:db8::2\n\tproto esp spi 0x00000777 reqid 7 mode tunnel\n\treplay-window 32 \n";
        // another transport-mode user's reqid 1 + 2 pair on other ports, with its SA between other addresses
        const std::string otherOut2 = "src " + ue + "/128 dst 2001:db8::106/128 sport 1 dport 2 \n\tdir out priority 0 \n\ttmpl src " + ue + " dst 2001:db8::106\n\t\tproto esp reqid 2 mode transport\n";
        const std::string otherOut1 = "src " + ue + "/128 dst 2001:db8::106/128 sport 3 dport 4 \n\tdir out priority 0 \n\ttmpl src " + ue + " dst 2001:db8::106\n\t\tproto esp reqid 1 mode transport\n";
        const std::string otherState = "src 2001:db8::7 dst 2001:db8::106\n\tproto esp spi 0x00000106 reqid 2 mode transport\n\treplay-window 0 \n";
        auto dels = OwnXfrmDeleteCommands(State, Policy, ue);
        Check(dels.size() == 8, "OwnXfrmDeleteCommands: our four states and four policies -> 8 deletes");
        Check(dels.size() == 8 && Join(dels[0]) == "ip xfrm state delete src 2001:db8::105 dst " + ue + " proto esp spi 0x0a84ab2f", "OwnXfrmDeleteCommands: a state by src, dst and SPI, byte-exact");
        Check(dels.size() == 8 && Join(dels[4]) == "ip xfrm policy delete src 2001:db8::105/128 dst " + ue + "/128 sport 33421 dport 45062 dir in", "OwnXfrmDeleteCommands: a policy by its selector and direction, byte-exact");
        Check(dels.size() == 8 && Join(dels[1]) == "ip xfrm state delete src " + ue + " dst 2001:db8::105 proto esp spi 0x0526f2c1" && Join(dels[2]) == "ip xfrm state delete src 2001:db8::105 dst " + ue + " proto esp spi 0x0071acd4" && Join(dels[3]) == "ip xfrm state delete src " + ue + " dst 2001:db8::105 proto esp spi 0x0526f2c0",
              "OwnXfrmDeleteCommands: the other three states byte-exact, in the kernel's order");
        Check(dels.size() == 8 && Join(dels[5]) == "ip xfrm policy delete src " + ue + "/128 dst 2001:db8::105/128 sport 45062 dport 33421 dir out" && Join(dels[6]) == "ip xfrm policy delete src 2001:db8::105/128 dst " + ue + "/128 sport 6000 dport 45061 dir in" && Join(dels[7]) == "ip xfrm policy delete src " + ue + "/128 dst 2001:db8::105/128 sport 45061 dport 6000 dir out",
              "OwnXfrmDeleteCommands: the other three policies byte-exact, in the kernel's order");
        auto besideVpn = OwnXfrmDeleteCommands(std::string(State) + tunState, std::string(Policy) + vpn + vpn4, ue);
        Check(besideVpn.size() == 8 && std::ranges::none_of(besideVpn, [](const auto& c) { auto j = Join(c); return j.contains("2001:db8::2") || j.contains("192.0.2.10") || j.contains("0x00000777"); }),
              "OwnXfrmDeleteCommands: a VPN's tunnel SA and two tunnel policies beside ours -> ours only, theirs untouched");
        Check(OwnXfrmDeleteCommands(otherState, otherOut2 + otherOut1, ue).empty(), "OwnXfrmDeleteCommands: another transport-mode reqid 1 + 2 pair on other ports, with its SA between other addresses -> nothing deleted");
        Check(OwnXfrmDeleteCommands(std::string(State) + otherState, std::string(Policy) + otherOut2 + otherOut1, ue).size() == 8, "OwnXfrmDeleteCommands: the same beside our pair -> ours only");
        Check(OwnXfrmDeleteCommands("", "", ue).empty(), "OwnXfrmDeleteCommands: nothing -> nothing");
        Check(OwnXfrmDeleteCommands(State, "", ue).size() == 4, "OwnXfrmDeleteCommands: policy-less states of imsd's on the UE's address are deleted (a setup whose policy add failed leaves them)");
        Check(OwnXfrmDeleteCommands(otherState, "", ue).empty(), "OwnXfrmDeleteCommands: a policy-less state between other addresses is left");
        // the fingerprint selects, not a pair: a lone transport-mode reqid-1 policy carrying the protected port between other addresses goes, with the reqid-1 state between them (the §12 residual)
        const std::string loneOther = "src 2001:db8::7/128 dst 2001:db8::106/128 sport 45061 dport 4 \n\tdir out priority 0 \n\ttmpl src 2001:db8::7 dst 2001:db8::106\n\t\tproto esp reqid 1 mode transport\n";
        const std::string loneOtherState = "src 2001:db8::7 dst 2001:db8::106\n\tproto esp spi 0x00000107 reqid 1 mode transport\n\treplay-window 0 \n";
        Check(OwnXfrmDeleteCommands(loneOtherState, loneOther, ue).size() == 2, "OwnXfrmDeleteCommands: a lone policy carrying the fingerprint between other addresses, and the reqid-1 state in its direction, are deleted (the fingerprint selects, not a pair)");
        // a reqid-1/2 transport-mode state on the UE's address goes whoever made it: the other user's SA between the UE address and its peer, its policies on other ports staying (the §12 residual)
        const std::string otherOnUe = "src " + ue + " dst 2001:db8::106\n\tproto esp spi 0x00000108 reqid 1 mode transport\n\treplay-window 0 \n";
        auto onUe = OwnXfrmDeleteCommands(otherOnUe, otherOut2 + otherOut1, ue);
        Check(onUe.size() == 1 && Join(onUe[0]) == "ip xfrm state delete src " + ue + " dst 2001:db8::106 proto esp spi 0x00000108", "OwnXfrmDeleteCommands: another transport-mode user's reqid-1 state on the UE's address is deleted whoever made it; its policies on other ports stay (the §12 residual)");
        Check(OwnXfrmDeleteCommands("src " + ue + " dst 2001:db8::106\n\tproto ah spi 0x00000109 reqid 1 mode transport\n\treplay-window 0 \n", otherOut2 + otherOut1, ue).empty(), "OwnXfrmDeleteCommands: an AH reqid-1 transport state on the UE's address is not selected (the delete names proto esp)");
        // the selector as `ip xfrm policy` prints it: a fingerprinted policy with a protocol (and a dev) keeps them in its delete, a missing port is no empty token
        const std::string protoOther = "src 2001:db8::7/128 dst 2001:db8::106/128 proto udp sport 45061 dport 4 \n\tdir out priority 0 \n\ttmpl src 2001:db8::7 dst 2001:db8::106\n\t\tproto esp reqid 1 mode transport\n";
        auto protoDels = OwnXfrmDeleteCommands("", protoOther, ue);
        Check(protoDels.size() == 1 && Join(protoDels[0]) == "ip xfrm policy delete src 2001:db8::7/128 dst 2001:db8::106/128 proto udp sport 45061 dport 4 dir out", "OwnXfrmDeleteCommands: a fingerprinted policy with a protocol in its selector is deleted by that selector (proto kept; the tmpl's 'proto esp' is not the selector's)");
        const std::string portlessOther = "src 2001:db8::7/128 dst 2001:db8::106/128 sport 45061 dev qmapmux0.0 \n\tdir out priority 0 \n\ttmpl src 2001:db8::7 dst 2001:db8::106\n\t\tproto esp reqid 1 mode transport\n";
        auto portlessDels = OwnXfrmDeleteCommands("", portlessOther, ue);
        Check(portlessDels.size() == 1 && Join(portlessDels[0]) == "ip xfrm policy delete src 2001:db8::7/128 dst 2001:db8::106/128 sport 45061 dev qmapmux0.0 dir out", "OwnXfrmDeleteCommands: a selector without a dport and with a dev -> no empty token, the dev kept");
        // the SA reader's ports come from our pair, never from a foreign transport policy that happens to carry ports
        auto saBeside = ParseExistingSa(State, std::string(Policy) + otherOut2, "2001:db8::105", ue);
        Check(saBeside && saBeside->portPc == 33421 && saBeside->portPs == 6000, "ParseExistingSa: a foreign transport policy's ports beside ours are not taken");
    }

    // ---- fresh setup command sequence
    {
        SaParams p;
        p.local = "2001:db8::db43";
        p.pcscf = "2001:db8::105";
        p.ik = std::vector<std::uint8_t>(16, 0xAB);
        p.ck = std::vector<std::uint8_t>(16, 0xCD);
        p.spiUc = 100; p.spiUs = 200; p.spiPc = 300; p.spiPs = 400;
        p.portUc = 45061; p.portUs = 45062; p.portPs = 6000; p.portPc = 33421;
        auto cmds = BuildSetupCommands(p);
        Check(cmds.size() == 8, "4 states + 4 policies, no flush (OwnXfrmDeleteCommands takes imsd's previous entries out)");
        // reqid1 outbound state: src LOCAL dst P spi=spiPs, IK auth, CK enc
        Check(Join(cmds[0]) ==
              "ip xfrm state add src 2001:db8::db43 dst 2001:db8::105 "
              "proto esp spi 400 mode transport reqid 1 "
              "auth-trunc hmac(sha1) 0xabababababababababababababababab 96 "
              "enc cbc(aes) 0xcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd",
              "state add reqid1 outbound byte-exact");
        // first out policy: sport PORT_UC dport PORT_PS reqid 1
        Check(Join(cmds[4]) == "ip xfrm policy add src 2001:db8::db43/128 dst 2001:db8::105/128 " "sport 45061 dport 6000 dir out tmpl src 2001:db8::db43 " "dst 2001:db8::105 proto esp reqid 1 mode transport", "policy add out reqid1 byte-exact");
        // v4 uses /32
        SaParams p4 = p;
        p4.local = "10.0.0.2"; p4.pcscf = "10.0.0.1";
        auto c4 = BuildSetupCommands(p4);
        Check(c4[4][5] == "10.0.0.2/32", "v4 prefix length /32");
    }
    // null cipher -> cipher_null + empty key arg
    {
        SaParams p;
        p.local = "::2"; p.pcscf = "::1";
        p.ik = std::vector<std::uint8_t>(16, 0x01);
        p.ck = {};
        p.ealg = "null";
        auto cmds = BuildSetupCommands(p);
        auto& st = cmds[0];
        Check(st[st.size() - 3] == "enc" && st[st.size() - 2] == "cipher_null" && st[st.size() - 1].empty(), "null cipher: cipher_null + empty key");
    }

    // md5 integrity as the P-CSCF selected it: hmac(md5), IK as the key
    {
        SaParams p;
        p.local = "::2"; p.pcscf = "::1";
        p.ik = std::vector<std::uint8_t>(16, 0x01);
        p.ck = {};
        p.alg = "hmac-md5-96"; p.ealg = "null";
        auto cmds = BuildSetupCommands(p);
        Check(cmds[0][17] == "hmac(md5)" && cmds[0][18] == "0x01010101010101010101010101010101" && cmds[0][19] == "96", "md5: auth-trunc hmac(md5) IK 96");
        Check(KernelAuth("hmac-sha-1-96") == "hmac(sha1)" && KernelAuth("hmac-md5-96") == "hmac(md5)" && !KernelAuth("hmac-sha-256-128"), "KernelAuth names");
        Check(KnownEalg("null") && KnownEalg("aes-cbc") && KnownEalg("des-ede3-cbc") && !KnownEalg("aes-gcm") && !KnownEalg("") && !KnownEalg("3des"), "KnownEalg set (canonical names only)");
    }

    // ---- Security-Server: O2 UK (alyx report 11, 2026-09-17) verbatim —
    // six mechanisms, the SPIs/ports on the fourth (sha1, no ealg); the
    // first `ealg=` in the header is the unselected 3DES entry.
    constexpr std::string_view O2 =
        "ipsec-3gpp;q=0.88;alg=hmac-md5-96;mod=trans,"
        "ipsec-3gpp;q=0.9;alg=hmac-md5-96;mod=trans;ealg=des-ede3-cbc,"
        "ipsec-3gpp;q=0.92;alg=hmac-md5-96;mod=trans;ealg=aes-cbc,"
        "ipsec-3gpp;q=0.94;alg=hmac-sha-1-96;mod=trans;spi-c=129427852;spi-s=133937015;port-c=6051;port-s=6060,"
        "ipsec-3gpp;q=0.96;alg=hmac-sha-1-96;mod=trans;ealg=des-ede3-cbc,"
        "ipsec-3gpp;q=0.98;alg=hmac-sha-1-96;mod=trans;ealg=aes-cbc";
    {
        // alyx's offer (EALG=null): the entry O2 marked is also the only one matching the offer
        auto ss = ParseSecurityServer(O2, "hmac-sha-1-96", "null");
        Check(ss.has_value() && ss->mechanisms.size() == 6, "O2/null: six mechanisms");
        Check(ss && ss->selected == 3 && ss->carrier == 3 && ss->offerMatched, "O2/null: the fourth mechanism (sha1, no ealg, the SPIs) selected, offer matched");
        if (ss) {
            const auto& m = ss->Selected();
            Check(m.name == "ipsec-3gpp" && m.mod == "trans" && m.prot.empty(), "O2/null: name, mod=trans, prot absent");
            Check(m.qMilli == 940, "O2/null: q=0.94");
            Check(m.alg == "hmac-sha-1-96", "O2/null: alg from the selected mechanism");
            Check(m.ealg.empty(), "O2/null: ealg absent on the selected mechanism (null), NOT the header's first ealg= (3DES)");
            Check(m.spiPc == 129427852u && m.spiPs == 133937015u, "O2/null: SPIs");
            Check(m.portPc == 6051 && m.portPs == 6060, "O2/null: ports");
            Check(m.text == "ipsec-3gpp;q=0.94;alg=hmac-sha-1-96;mod=trans;spi-c=129427852;spi-s=133937015;port-c=6051;port-s=6060", "O2/null: selected text verbatim");
            Check(ss->mechanisms[1].ealg == "des-ede3-cbc" && !ss->mechanisms[1].spiPs, "O2/null: the 3DES entry is parsed but carries no SPIs");
        }
        // the default offer (EALG=aes-cbc): the spec picks sha1+aes (q=0.98); the SPIs stay with the marked entry
        auto sa = ParseSecurityServer(O2, "hmac-sha-1-96", "aes-cbc");
        Check(sa && sa->selected == 5 && sa->offerMatched, "O2/aes: sha1+aes-cbc (q=0.98) selected for the aes offer");
        Check(sa && sa->carrier == 3 && sa->Carrier().spiPs == 133937015u && sa->Carrier().portPs == 6060, "O2/aes: SPIs/ports from the entry carrying them");
        Check(sa && sa->Selected().ealg == "aes-cbc" && sa->Selected().alg == "hmac-sha-1-96", "O2/aes: algorithms from the selected entry");
        // an offer O2 does not list (3des alias -> des-ede3-cbc): sha1+3des (q=0.96)
        auto s3 = ParseSecurityServer(O2, "hmac-sha-1-96", "3des");
        Check(s3 && s3->selected == 4 && s3->offerMatched && s3->carrier == 3, "O2/3des: the alias matches des-ede3-cbc (q=0.96), SPIs from the marked entry");
    }
    // ---- KPN: one mechanism, spaces after the separators, explicit ealg
    {
        auto ss = ParseSecurityServer("ipsec-3gpp; q=0.1; alg=hmac-sha-1-96; ealg=aes-cbc; spi-c=49889723; spi-s=49889722; port-c=33142; port-s=6000", "hmac-sha-1-96", "aes-cbc");
        Check(ss && ss->mechanisms.size() == 1 && ss->selected == 0 && ss->carrier == 0 && ss->offerMatched, "KPN: single mechanism selected, offer matched");
        Check(ss && ss->Selected().alg == "hmac-sha-1-96" && ss->Selected().ealg == "aes-cbc", "KPN: alg + ealg");
        Check(ss && ss->Selected().spiPc == 49889723u && ss->Selected().spiPs == 49889722u && ss->Selected().portPc == 33142 && ss->Selected().portPs == 6000, "KPN: SPIs + ports");
        Check(ss && ss->Selected().qMilli == 100, "KPN: q=0.1");
        auto nul = ParseSecurityServer("ipsec-3gpp; q=0.1; alg=hmac-sha-1-96; ealg=null; spi-c=33998503; spi-s=33998502; port-c=33021; port-s=6000", "hmac-sha-1-96", "null");
        Check(nul && nul->Selected().ealg == "null" && nul->offerMatched, "KPN: explicit ealg=null matches the null offer");
        // the P-CSCF answers a cipher we did not offer: still the only entry, installed as answered, flagged
        auto other = ParseSecurityServer("ipsec-3gpp; q=0.1; alg=hmac-sha-1-96; ealg=null; spi-c=33998503; spi-s=33998502; port-c=33021; port-s=6000", "hmac-sha-1-96", "aes-cbc");
        Check(other && other->selected == 0 && other->carrier == 0 && !other->offerMatched, "single entry not matching the offer: selected via the spi-s fallback, offerMatched=false");
        // the fresh-SA throttle answer: spi-s present and zero
        auto thr = ParseSecurityServer("ipsec-3gpp; q=0.1; alg=hmac-sha-1-96; ealg=aes-cbc; spi-c=1; spi-s=0; port-c=33102; port-s=6000", "hmac-sha-1-96", "aes-cbc");
        Check(thr && thr->Carrier().spiPs.has_value() && *thr->Carrier().spiPs == 0, "KPN throttle: spi-s=0 is present, not absent");
        // no SPIs anywhere: parsed, nothing to build from
        auto bare = ParseSecurityServer("ipsec-3gpp; q=0.1; alg=hmac-sha-1-96; ealg=null;", "hmac-sha-1-96", "null");
        Check(bare && bare->mechanisms.size() == 1 && bare->offerMatched && !bare->Carrier().spiPs && !bare->Carrier().portPs, "no spi-s: absent, not zero");
    }
    // ---- the resume's Security-Verify: the stored list only for the SA pair the kernel holds
    {
        using Source = ResumeSecurityVerify::Source;
        ExistingSa sa;
        sa.spiPc = 129427852u; sa.spiPs = 133937015u; sa.portPc = 6051; sa.portPs = 6060;
        sa.securityServer = "ipsec-3gpp; q=0.1; alg=hmac-sha-1-96; ealg=aes-cbc; spi-c=129427852; spi-s=133937015; port-c=6051; port-s=6060";
        auto ok = ChooseResumeSecurityVerify(O2, sa, "null");
        Check(ok.source == Source::Stored && ok.value == O2 && ok.mechanisms == 6, "resume: O2's six-entry list whose carrier is the kernel's pair -> the list verbatim, six mechanisms");
        auto okAes = ChooseResumeSecurityVerify(O2, sa, "aes-cbc");
        Check(okAes.source == Source::Stored && okAes.value == O2, "resume: the same under the aes offer (the carrier does not depend on the offer)");
        constexpr std::pair<std::string_view, std::string_view> off[] = {
            {"spi-c", "ipsec-3gpp;q=0.94;alg=hmac-sha-1-96;mod=trans;spi-c=129427853;spi-s=133937015;port-c=6051;port-s=6060"},
            {"spi-s", "ipsec-3gpp;q=0.94;alg=hmac-sha-1-96;mod=trans;spi-c=129427852;spi-s=133937016;port-c=6051;port-s=6060"},
            {"port-c", "ipsec-3gpp;q=0.94;alg=hmac-sha-1-96;mod=trans;spi-c=129427852;spi-s=133937015;port-c=6052;port-s=6060"},
            {"port-s", "ipsec-3gpp;q=0.94;alg=hmac-sha-1-96;mod=trans;spi-c=129427852;spi-s=133937015;port-c=6051;port-s=6061"},
        };
        for (const auto& [field, text] : off) {
            auto r = ChooseResumeSecurityVerify(text, sa, "null");
            Check(r.source == Source::OtherSa && r.value == sa.securityServer, std::format("resume: {} off by one -> the rebuilt entry", field));
        }
        auto offSpiS = ChooseResumeSecurityVerify(off[1].second, sa, "null");
        Check(offSpiS.storedSpiPs && *offSpiS.storedSpiPs == 133937016u, "resume: the stored spi-s is reported for the log line");
        auto none = ChooseResumeSecurityVerify("", sa, "null");
        Check(none.source == Source::NoneStored && none.value == sa.securityServer, "resume: nothing stored -> the rebuilt entry");
        auto junk = ChooseResumeSecurityVerify("not a list", sa, "null");
        Check(junk.source == Source::OtherSa && junk.value == sa.securityServer && !junk.storedSpiPs, "resume: an unparseable value -> the rebuilt entry, no spi-s to report");
        ExistingSa kpn;
        kpn.spiPc = 49889723u; kpn.spiPs = 49889722u; kpn.portPc = 33142; kpn.portPs = 6000;
        kpn.securityServer = "ipsec-3gpp; q=0.1; alg=hmac-sha-1-96; ealg=aes-cbc; spi-c=49889723; spi-s=49889722; port-c=33142; port-s=6000";
        auto k = ChooseResumeSecurityVerify(kpn.securityServer, kpn, "aes-cbc");
        Check(k.source == Source::Stored && k.value == kpn.securityServer && k.mechanisms == 1, "resume: KPN's one-entry list for its pair -> the list, one mechanism");
    }
    // ---- a state file another attempt left behind: the file's UE SPIs against the kernel pair's
    {
        Check(!FileNamesAnotherPair(0x0526f2c0L, 0x0526f2c1L, 0x0526f2c0, 0x0526f2c1), "file pair: the pair's own SPIs -> the file is this pair's");
        Check(FileNamesAnotherPair(0x11111111L, 0x22222222L, 0x0526f2c0, 0x0526f2c1), "file pair: other SPIs -> another attempt's");
        Check(FileNamesAnotherPair(0x0526f2c0L, 0x22222222L, 0x0526f2c0, 0x0526f2c1), "file pair: one SPI differs -> another attempt's");
        Check(!FileNamesAnotherPair(std::nullopt, std::nullopt, 0x0526f2c0, 0x0526f2c1), "file pair: a file without the keys -> no verdict");
        Check(!FileNamesAnotherPair(0x11111111L, 0x22222222L, 0, 0x0526f2c1), "file pair: a missing inbound state (0) -> no verdict");
        Check(!FileNamesAnotherPair(0L, 0L, 0x0526f2c0, 0x0526f2c1), "file pair: zeros in the file -> no verdict");
    }
    // ---- selection rules and syntax corners
    {
        // spec-shaped list: every entry carries the SPI set, md5 preferred by q — we offered sha1, so sha1 wins (audit A1)
        auto a1 = ParseSecurityServer("ipsec-3gpp;q=0.1;alg=hmac-sha-1-96;ealg=aes-cbc;spi-c=11;spi-s=12;port-c=13;port-s=14,ipsec-3gpp;q=0.2;alg=hmac-md5-96;ealg=aes-cbc;spi-c=11;spi-s=12;port-c=13;port-s=14", "hmac-sha-1-96", "aes-cbc");
        Check(a1 && a1->selected == 0 && a1->offerMatched && a1->Selected().alg == "hmac-sha-1-96", "every entry carries SPIs, md5 preferred: the offered sha1 is selected, not the highest q");
        // two sha1 entries with different ciphers, both carrying SPIs: the offered cipher wins over q (audit A3)
        auto a3 = ParseSecurityServer("ipsec-3gpp;q=0.9;alg=hmac-sha-1-96;ealg=des-ede3-cbc;spi-c=1;spi-s=2;port-c=3;port-s=4,ipsec-3gpp;q=0.8;alg=hmac-sha-1-96;ealg=aes-cbc;spi-c=5;spi-s=6;port-c=7;port-s=8", "hmac-sha-1-96", "aes-cbc");
        Check(a3 && a3->selected == 1 && a3->Carrier().spiPs == 6u, "offered cipher wins over a higher-q cipher we did not offer");
        // two matching entries: the higher q
        auto two = ParseSecurityServer("ipsec-3gpp;q=0.5;alg=hmac-sha-1-96;ealg=aes-cbc;spi-c=1;spi-s=2;port-c=3;port-s=4,ipsec-3gpp;q=0.9;alg=hmac-sha-1-96;ealg=aes-cbc;spi-c=5;spi-s=6;port-c=7;port-s=8", "hmac-sha-1-96", "aes-cbc");
        Check(two && two->selected == 1 && two->Selected().spiPs == 6u, "two matching entries: highest q selected");
        // nothing matches the offer (md5-only list): fallback = the highest-q carrier, flagged
        auto md5 = ParseSecurityServer("ipsec-3gpp;q=0.5;alg=hmac-md5-96;spi-c=1;spi-s=2;port-c=3;port-s=4,ipsec-3gpp;q=0.9;alg=hmac-md5-96;ealg=aes-cbc;spi-c=5;spi-s=6;port-c=7;port-s=8", "hmac-sha-1-96", "aes-cbc");
        Check(md5 && md5->selected == 1 && !md5->offerMatched && md5->carrier == 1, "no match: highest-q carrier, offerMatched=false");
        // no q at all, nothing matching: the first carrier
        auto noq = ParseSecurityServer("ipsec-3gpp;alg=hmac-md5-96;spi-c=1;spi-s=2;port-c=3;port-s=4,ipsec-3gpp;alg=hmac-md5-96;ealg=aes-cbc;spi-c=5;spi-s=6;port-c=7;port-s=8", "hmac-sha-1-96", "aes-cbc");
        Check(noq && noq->selected == 0 && !noq->offerMatched, "no q, no match: first carrier");
        // absent alg on the entry counts as the offered one
        auto noalg = ParseSecurityServer("ipsec-3gpp;ealg=aes-cbc;spi-c=1;spi-s=2;port-c=3;port-s=4", "hmac-sha-1-96", "aes-cbc");
        Check(noalg && noalg->offerMatched, "absent alg matches the offer");
        // only non-ipsec mechanisms: nothing for us
        Check(!ParseSecurityServer("tls;q=0.2,digest;d-alg=MD5", "hmac-sha-1-96", "aes-cbc").has_value(), "tls/digest only -> nullopt");
        // a quoted comma must not split the list; tokens are case-insensitive
        auto q = ParseSecurityServer("digest;d-alg=MD5;d-qop=\"auth,auth-int\",IPSEC-3GPP; ALG=HMAC-SHA-1-96; EALG=AES-CBC; SPI-C=11; SPI-S=12; PORT-C=13; PORT-S=14", "HMAC-SHA-1-96", "AES-CBC");
        Check(q && q->mechanisms.size() == 2 && q->mechanisms[0].name == "digest", "quoted comma stays inside the digest mechanism");
        Check(q && q->selected == 1 && q->offerMatched && q->Selected().name == "ipsec-3gpp" && q->Selected().alg == "hmac-sha-1-96" && q->Selected().ealg == "aes-cbc" && q->Selected().spiPs == 12u && q->Selected().portPs == 14, "case-insensitive names and values, lower-cased; offer compared case-insensitively");
        // prot/mod parsed for the caller to refuse
        auto tun = ParseSecurityServer("ipsec-3gpp;alg=hmac-sha-1-96;ealg=aes-cbc;prot=ah;mod=tun;spi-c=1;spi-s=2;port-c=3;port-s=4", "hmac-sha-1-96", "aes-cbc");
        Check(tun && tun->Selected().prot == "ah" && tun->Selected().mod == "tun", "prot/mod parsed");
        // spi-s beyond 32 bits, a port beyond 16 bits or zero: absent, not garbage
        auto bad = ParseSecurityServer("ipsec-3gpp;alg=hmac-sha-1-96;ealg=aes-cbc;spi-c=1;spi-s=4294967296;port-c=0;port-s=70000", "hmac-sha-1-96", "aes-cbc");
        Check(bad && !bad->Selected().spiPs && !bad->Selected().portPs && !bad->Selected().portPc && bad->Selected().spiPc == 1u, "out-of-range spi-s/port-s and port 0 are absent");
        // q syntax
        auto q1 = ParseSecurityServer("ipsec-3gpp;q=1;spi-s=1", "hmac-sha-1-96", "null");
        Check(q1 && q1->Selected().qMilli == 1000, "q=1 -> 1000");
        auto qx = ParseSecurityServer("ipsec-3gpp;q=x;spi-s=1", "hmac-sha-1-96", "null");
        Check(qx && !qx->Selected().qMilli, "q=x -> no q");
        Check(!ParseSecurityServer("", "hmac-sha-1-96", "null").has_value() && !ParseSecurityServer(" , ", "hmac-sha-1-96", "null").has_value(), "empty value -> nullopt");
        Check(NormalizeEalg("") == "null" && NormalizeEalg("3des") == "des-ede3-cbc" && NormalizeEalg("aes-cbc") == "aes-cbc", "NormalizeEalg");
    }

    if (Failures == 0) std::println("Ipsec: all tests passed");
    return Failures;
}
