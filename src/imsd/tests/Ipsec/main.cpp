// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
// Imsd:Ipsec unit tests — the `ip xfrm` setup command sequence (byte-exact
// argv) and the warm-SA reader, fed `ip xfrm state`/`policy` output in the
// exact shape captured from a registered FP6 (addresses/keys synthetic).
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
    // incomplete input -> nullopt
    Check(!ParseExistingSa("src a dst b\n\tspi 0x1 reqid 1 mode transport\n", "", "b", "a").has_value(), "missing pieces -> nullopt");

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
        Check(cmds.size() == 10, "flush x2 + 4 states + 4 policies");
        Check(Join(cmds[0]) == "ip xfrm state flush", "state flush first");
        Check(Join(cmds[1]) == "ip xfrm policy flush", "policy flush second");
        // reqid1 outbound state: src LOCAL dst P spi=spiPs, IK auth, CK enc
        Check(Join(cmds[2]) ==
              "ip xfrm state add src 2001:db8::db43 dst 2001:db8::105 "
              "proto esp spi 400 mode transport reqid 1 "
              "auth-trunc hmac(sha1) 0xabababababababababababababababab 96 "
              "enc cbc(aes) 0xcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd",
              "state add reqid1 outbound byte-exact");
        // first out policy: sport PORT_UC dport PORT_PS reqid 1
        Check(Join(cmds[6]) == "ip xfrm policy add src 2001:db8::db43/128 dst 2001:db8::105/128 " "sport 45061 dport 6000 dir out tmpl src 2001:db8::db43 " "dst 2001:db8::105 proto esp reqid 1 mode transport", "policy add out reqid1 byte-exact");
        // v4 uses /32
        SaParams p4 = p;
        p4.local = "10.0.0.2"; p4.pcscf = "10.0.0.1";
        auto c4 = BuildSetupCommands(p4);
        Check(c4[6][5] == "10.0.0.2/32", "v4 prefix length /32");
    }
    // null cipher -> cipher_null + empty key arg
    {
        SaParams p;
        p.local = "::2"; p.pcscf = "::1";
        p.ik = std::vector<std::uint8_t>(16, 0x01);
        p.ck = {};
        p.ealg = "null";
        auto cmds = BuildSetupCommands(p);
        auto& st = cmds[2];
        Check(st[st.size() - 3] == "enc" && st[st.size() - 2] == "cipher_null" && st[st.size() - 1].empty(), "null cipher: cipher_null + empty key");
    }

    if (Failures == 0) std::println("Ipsec: all tests passed");
    return Failures;
}
