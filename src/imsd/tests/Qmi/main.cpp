// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
// Imsd:Qmi unit tests — the request bytes imsd-pcscf sends (pinned to the
// bytes the dev-phone probe sent, which are protocol only), a Get Current
// Settings answer in the exact TLV shape the Fairphone 6's modem returned
// for the ims PDN (163 bytes; the addresses re-encoded to 2001:db8::/32),
// the OutOfCall answer an unbound client gets, and the readers' tolerance.
import std;
import Imsd;

using namespace imsd::qmi;

namespace {
    int Failures = 0;
    void Check(bool cond, std::string_view msg) {
        if (!cond) {
            std::println(std::cerr, "FAIL: {}", msg);
            ++Failures;
        }
    }

    std::vector<std::uint8_t> Hex(std::string_view s) { return *imsd::util::FromHex(s); }

    // The phone's answer to Get Current Settings (mask 0x7FFFF) on the bound
    // client, 2026-10-11: profile2 / ims / IPv6 / MTU 1280, TLV 0x22 = 1,
    // 0x24 empty, 0x2E = two P-CSCFs (PCO order), 0x2F empty. UE and
    // gateway and the two P-CSCFs re-encoded.
    const std::vector<std::uint8_t> Settings = Hex("02 04 00 2d 00 9c 00 02 04 00 00 00 00 00 10 08 00 70 72 6f 66 69 6c 65 32 11 01 00 02 14 03 00 69 6d 73 1d 01 00 00 1f 02 00 00 02 22 01 00 01 24 01 00 00 25 11 00 20 01 0d b8 11 11 22 22 33 33 44 44 55 55 db 43 40 26 11 00 20 01 0d b8 11 11 22 22 00 00 00 00 00 00 00 01 40 29 04 00 00 05 00 00 2a 01 00 00 2b 01 00 06 2c 01 00 00 2d 02 00 80 88 2e 21 00 02 20 01 0d b8 00 00 00 00 00 00 00 00 00 00 00 23 20 01 0d b8 00 00 00 00 00 00 00 00 00 00 01 05 2f 08 00 00 00 00 00 00 00 00 00");
    // An unbound client's answer: result 1, error 15 (OutOfCall).
    const std::vector<std::uint8_t> OutOfCall = Hex("02 04 00 2d 00 07 00 02 04 00 01 00 0f 00");
    // Synthetic (no recording carries them): an IPv4 list of one and an FQDN
    // list of one beside a one-entry IPv6 list.
    const std::vector<std::uint8_t> Synthetic = Hex("02 07 00 2d 00 3a 00 02 04 00 00 00 00 00 22 01 00 01 23 05 00 01 05 02 00 c0 24 10 00 01 0d 00 70 63 73 63 66 2e 65 78 61 6d 70 6c 65 2e 11 00 01 20 01 0d b8 00 00 00 00 00 00 00 00 00 00 01 05");
    // Synthetic: a 0x2E whose count says two but carries one.
    const std::vector<std::uint8_t> BadCount = Hex("02 08 00 2d 00 1b 00 02 04 00 00 00 00 00 2e 11 00 02 20 01 0d b8 00 00 00 00 00 00 00 00 00 00 00 23");
}

int main() {
    // ---- requests, byte for byte what the probe sent
    Check(BindMuxDataPort(1, EndpointEmbedded, EndpointInterfaceIpa, 1) == Hex("00 01 00 a2 00 0f 00 10 08 00 04 00 00 00 01 00 00 00 11 01 00 01"), "Bind Mux Data Port bytes");
    Check(SetIpFamily(2, IpFamilyV6) == Hex("00 02 00 4d 00 04 00 01 01 00 06"), "Set IP Family bytes");
    Check(GetPacketServiceStatus(3) == Hex("00 03 00 22 00 00 00"), "Get Packet Service Status bytes");
    Check(GetCurrentSettings(4, RequestedSettingsAll) == Hex("00 04 00 2d 00 07 00 10 04 00 ff ff 07 00"), "Get Current Settings bytes");
    Check(GetProfileSettings(5, 2) == Hex("00 05 00 2b 00 05 00 01 02 00 00 02"), "Get Profile Settings bytes");

    // ---- the recorded answer
    {
        auto r = ParseMessage(Settings);
        Check(r.has_value(), "settings parse");
        Check(r && r->type == TypeResponse && r->txn == 4 && r->msg == MsgGetCurrentSettings, "settings header");
        Check(r && r->Ok(), "settings result ok");
        Check(r && r->tlvs.size() == 17, "settings: 17 TLVs");
        Check(r && r->Find(0x2E) && r->Find(0x2E)->value.size() == 33, "settings: TLV 0x2E is 33 bytes");
        PcscfLists l = ParsePcscfLists(*r);
        Check(l.viaPco, "settings: P-CSCF via PCO requested");
        Check(l.ipv6.size() == 2 && l.ipv6[0] == "2001:db8::23" && l.ipv6[1] == "2001:db8::105", "settings: the IPv6 list in PCO order, canonical");
        Check(l.ipv4.empty() && l.fqdn.empty(), "settings: no IPv4 list, an empty FQDN list");
    }
    // ---- the unbound client's answer
    {
        auto r = ParseMessage(OutOfCall);
        Check(r && !r->Ok() && r->error == ErrOutOfCall, "OutOfCall parsed");
        Check(r && ErrorName(r->error) == "OutOfCall", "OutOfCall named");
        Check(ErrorName(ErrNoEffect) == "NoEffect" && ErrorName(999) == "?", "error names");
        Check(r && ParsePcscfLists(*r).ipv6.empty(), "no lists in an error answer");
    }
    // ---- the other lists, and tolerance
    {
        auto r = ParseMessage(Synthetic);
        Check(r.has_value(), "synthetic parse");
        PcscfLists l = ParsePcscfLists(*r);
        Check(l.ipv4.size() == 1 && l.ipv4[0] == "192.0.2.5", "IPv4 list: little-endian word to dotted quad");
        Check(l.fqdn.size() == 1 && l.fqdn[0] == "pcscf.example", "FQDN list: u16-length strings");
        Check(l.ipv6.size() == 1 && l.ipv6[0] == "2001:db8::105", "IPv6 list of one");
        auto b = ParseMessage(BadCount);
        Check(b && ParsePcscfLists(*b).ipv6.empty(), "a 0x2E whose count disagrees with its length reads as empty");
        Check(!ParseMessage(Hex("02 04 00 2d 00")).has_value(), "short buffer -> nullopt");
        Check(!ParseMessage(Hex("02 04 00 2d 00 07 00 02 09 00 01 00 0f 00")).has_value(), "a TLV overrunning the message -> nullopt");
        Check(!ParseMessage(Hex("02 04 00 2d 00 20 00 02 04 00 01 00 0f 00")).has_value(), "a header length past the buffer -> nullopt");
    }
    // ---- the two small readers
    {
        auto st = ParseMessage(Hex("02 03 00 22 00 0b 00 02 04 00 00 00 00 00 01 01 00 02"));
        Check(st && PacketServiceStatus(*st) == PacketServiceConnected, "packet service status: connected (the probe's answer)");
        auto pf = ParseMessage(Hex("02 05 00 2b 00 0b 00 02 04 00 00 00 00 00 1f 01 00 01"));
        Check(pf && ProfilePcscfViaPco(*pf) == true, "profile flag: PCSCF via PCO set");
        auto none = ParseMessage(Hex("02 05 00 2b 00 07 00 02 04 00 00 00 00 00"));
        Check(none && !ProfilePcscfViaPco(*none).has_value() && !PacketServiceStatus(*none).has_value(), "absent TLVs -> nullopt");
    }

    if (Failures == 0) std::println("Qmi: all tests passed");
    return Failures;
}
