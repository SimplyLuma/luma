// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
/*
Imsd:Qmi — the bytes of the QMI WDS exchanges imsd-pcscf has with the modem
to learn the ims PDN's P-CSCF list, and the readers of the answers.

Over QRTR a QMI message is {type u8, transaction u16, message u16, length
u16} followed by TLVs {type u8, length u16, value}, integers little endian.
The modem hands the P-CSCF addresses the network put in the PDN's Protocol
Configuration Options to a WDS client bound to the data port that carries the
call (Bind Mux Data Port, Set IP Family, Get Current Settings) — in the TLVs
libqmi models (0x22 the flag, 0x23 the IPv4 list, 0x24 the FQDN list) and in
one it does not: 0x2E, the IPv6 list, Qualcomm's layout as read off the
Fairphone 6 (a count byte, then 16 bytes per address, PCO order). The socket,
the service lookup and the sequence are the helper's
(implementations/pcscf.cpp); this partition only shapes bytes and reads
answers, pinned by tests/Qmi on a response in the exact shape the modem
returned, addresses re-encoded to the documentation prefix.
*/

export module Imsd:Qmi;
import std;
import :Util;

export namespace imsd::qmi {

    constexpr std::uint32_t WdsService = 1;
    constexpr std::uint8_t TypeRequest = 0x00;
    constexpr std::uint8_t TypeResponse = 0x02;
    constexpr std::uint8_t TypeIndication = 0x04;

    constexpr std::uint16_t MsgGetPacketServiceStatus = 0x0022;
    constexpr std::uint16_t MsgGetProfileSettings = 0x002B;
    constexpr std::uint16_t MsgGetCurrentSettings = 0x002D;
    constexpr std::uint16_t MsgSetIpFamily = 0x004D;
    constexpr std::uint16_t MsgBindMuxDataPort = 0x00A2;

    // Get Current Settings "Requested Settings": every bit libqmi defines
    // (0..18). The P-CSCF ones are 10 (address), 11 (server address list)
    // and 12 (domain name list); the whole mask is what the probe used.
    constexpr std::uint32_t RequestedSettingsAll = 0x7FFFF;
    // The data endpoint of an IPA-attached modem as ModemManager binds it:
    // type embedded (4), interface number 1.
    constexpr std::uint32_t EndpointEmbedded = 4;
    constexpr std::uint32_t EndpointInterfaceIpa = 1;
    constexpr std::uint8_t IpFamilyV6 = 6;
    constexpr std::uint8_t ProfileType3gpp = 0;
    constexpr std::uint8_t PacketServiceConnected = 2;

    constexpr std::uint16_t ErrOutOfCall = 15;
    constexpr std::uint16_t ErrNoEffect = 26;

    struct Tlv {
        std::uint8_t type = 0;
        std::vector<std::uint8_t> value;
    };

    namespace detail {
        inline void Put16(std::vector<std::uint8_t>& b, std::uint16_t v) {
            b.push_back(static_cast<std::uint8_t>(v & 0xFF));
            b.push_back(static_cast<std::uint8_t>(v >> 8));
        }
        inline void Put32(std::vector<std::uint8_t>& b, std::uint32_t v) {
            Put16(b, static_cast<std::uint16_t>(v & 0xFFFF));
            Put16(b, static_cast<std::uint16_t>(v >> 16));
        }
        inline std::uint16_t Get16(std::span<const std::uint8_t> b, std::size_t at) {
            return static_cast<std::uint16_t>(b[at] | (b[at + 1] << 8));
        }
        inline std::uint32_t Get32(std::span<const std::uint8_t> b, std::size_t at) {
            return static_cast<std::uint32_t>(Get16(b, at)) | (static_cast<std::uint32_t>(Get16(b, at + 2)) << 16);
        }
        // RFC 5952 text of 16 address bytes (via the canonicaliser: the
        // partition has no inet_ntop)
        inline std::string Ip6Text(std::span<const std::uint8_t> a) {
            std::string s;
            for (std::size_t i = 0; i < 16; i += 2) s += std::format("{}{:x}", i ? ":" : "", (a[i] << 8) | a[i + 1]);
            return imsd::util::CanonicalIp6(s).value_or(s);
        }
    }

    inline std::vector<std::uint8_t> TlvBytes(std::uint8_t type, std::span<const std::uint8_t> value) {
        std::vector<std::uint8_t> b;
        b.push_back(type);
        detail::Put16(b, static_cast<std::uint16_t>(value.size()));
        b.insert(b.end(), value.begin(), value.end());
        return b;
    }
    inline std::vector<std::uint8_t> Tlv8(std::uint8_t type, std::uint8_t v) {
        std::array<std::uint8_t, 1> a{v};
        return TlvBytes(type, a);
    }
    inline std::vector<std::uint8_t> Tlv32(std::uint8_t type, std::uint32_t v) {
        std::vector<std::uint8_t> a;
        detail::Put32(a, v);
        return TlvBytes(type, a);
    }

    // A request: the header, then the TLVs as given.
    inline std::vector<std::uint8_t> Request(std::uint16_t txn, std::uint16_t msg, std::span<const std::uint8_t> tlvs) {
        std::vector<std::uint8_t> b;
        b.push_back(TypeRequest);
        detail::Put16(b, txn);
        detail::Put16(b, msg);
        detail::Put16(b, static_cast<std::uint16_t>(tlvs.size()));
        b.insert(b.end(), tlvs.begin(), tlvs.end());
        return b;
    }

    // Bind Mux Data Port: TLV 0x10 {endpoint type u32, interface number
    // u32}, TLV 0x11 mux id u8 — the binding ModemManager gives its own
    // bearer clients, which is what lets this client read their call.
    inline std::vector<std::uint8_t> BindMuxDataPort(std::uint16_t txn, std::uint32_t epType, std::uint32_t epIface, std::uint8_t mux) {
        std::vector<std::uint8_t> ep;
        detail::Put32(ep, epType);
        detail::Put32(ep, epIface);
        std::vector<std::uint8_t> t = TlvBytes(0x10, ep);
        std::vector<std::uint8_t> m = Tlv8(0x11, mux);
        t.insert(t.end(), m.begin(), m.end());
        return Request(txn, MsgBindMuxDataPort, t);
    }
    // Set IP Family: TLV 0x01 u8 (4 or 6) — which family's settings the
    // client asks about.
    inline std::vector<std::uint8_t> SetIpFamily(std::uint16_t txn, std::uint8_t family) {
        return Request(txn, MsgSetIpFamily, Tlv8(0x01, family));
    }
    inline std::vector<std::uint8_t> GetPacketServiceStatus(std::uint16_t txn) {
        return Request(txn, MsgGetPacketServiceStatus, {});
    }
    // Get Current Settings: TLV 0x10 the requested-settings mask u32.
    inline std::vector<std::uint8_t> GetCurrentSettings(std::uint16_t txn, std::uint32_t mask) {
        return Request(txn, MsgGetCurrentSettings, Tlv32(0x10, mask));
    }
    // Get Profile Settings: TLV 0x01 {profile type u8 (0 = 3GPP), index u8}.
    inline std::vector<std::uint8_t> GetProfileSettings(std::uint16_t txn, std::uint8_t index) {
        std::array<std::uint8_t, 2> id{ProfileType3gpp, index};
        return Request(txn, MsgGetProfileSettings, TlvBytes(0x01, id));
    }

    struct Response {
        std::uint8_t type = 0;
        std::uint16_t txn = 0;
        std::uint16_t msg = 0;
        std::vector<Tlv> tlvs;
        bool hasResult = false;   // TLV 0x02 {result u16, error u16} present
        std::uint16_t result = 0;
        std::uint16_t error = 0;
        bool Ok() const { return hasResult && result == 0; }
        const Tlv* Find(std::uint8_t t) const {
            for (const Tlv& x : tlvs) if (x.type == t) return &x;
            return nullptr;
        }
    };

    // A received message: the header and every TLV; nullopt when the buffer
    // is shorter than its header says or a TLV overruns it.
    inline std::optional<Response> ParseMessage(std::span<const std::uint8_t> b) {
        if (b.size() < 7) return std::nullopt;
        Response r;
        r.type = b[0];
        r.txn = detail::Get16(b, 1);
        r.msg = detail::Get16(b, 3);
        std::size_t len = detail::Get16(b, 5);
        if (7 + len > b.size()) return std::nullopt;
        std::size_t off = 7;
        std::size_t end = 7 + len;
        while (off + 3 <= end) {
            Tlv t;
            t.type = b[off];
            std::size_t n = detail::Get16(b, off + 1);
            if (off + 3 + n > end) return std::nullopt;
            t.value.assign(b.begin() + static_cast<std::ptrdiff_t>(off + 3), b.begin() + static_cast<std::ptrdiff_t>(off + 3 + n));
            if (t.type == 0x02 && n == 4) {
                r.hasResult = true;
                r.result = detail::Get16(t.value, 0);
                r.error = detail::Get16(t.value, 2);
            }
            r.tlvs.push_back(std::move(t));
            off += 3 + n;
        }
        if (off != end) return std::nullopt;
        return r;
    }

    // The QMI protocol error names imsd-pcscf can meet (libqmi's nicks).
    inline std::string_view ErrorName(std::uint16_t e) {
        switch (e) {
        case 0: return "None";
        case 1: return "MalformedMessage";
        case 3: return "Internal";
        case 9: return "InvalidHandle";
        case 10: return "InvalidProfile";
        case 14: return "CallFailed";
        case 15: return "OutOfCall";
        case 17: return "MissingArgument";
        case 23: return "DeviceInUse";
        case 26: return "NoEffect";
        case 46: return "GeneralError";
        case 48: return "InvalidArgument";
        case 71: return "InvalidQmiCommand";
        case 82: return "AccessDenied";
        default: return "?";
        }
    }

    // What a Get Current Settings answer says about the P-CSCF.
    struct PcscfLists {
        bool viaPco = false;              // TLV 0x22: the modem asked for P-CSCF addresses in the PCO
        std::vector<std::string> ipv6;    // TLV 0x2E, PCO order, canonical
        std::vector<std::string> ipv4;    // TLV 0x23, dotted quads
        std::vector<std::string> fqdn;    // TLV 0x24
    };

    // Tolerant: a list whose count does not match its length is read as
    // empty, never as garbage.
    inline PcscfLists ParsePcscfLists(const Response& r) {
        PcscfLists out;
        if (const Tlv* f = r.Find(0x22); f && f->value.size() == 1) out.viaPco = f->value[0] != 0;
        if (const Tlv* t = r.Find(0x2E); t && !t->value.empty() && t->value.size() == 1 + 16 * static_cast<std::size_t>(t->value[0]))
            for (std::size_t i = 0; i < t->value[0]; i++)
                out.ipv6.push_back(detail::Ip6Text(std::span<const std::uint8_t>(t->value).subspan(1 + 16 * i, 16)));
        if (const Tlv* t = r.Find(0x23); t && !t->value.empty() && t->value.size() == 1 + 4 * static_cast<std::size_t>(t->value[0]))
            for (std::size_t i = 0; i < t->value[0]; i++) {
                std::uint32_t v = detail::Get32(t->value, 1 + 4 * i);   // little-endian u32, a.b.c.d = the high byte first
                out.ipv4.push_back(std::format("{}.{}.{}.{}", v >> 24, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF));
            }
        if (const Tlv* t = r.Find(0x24); t && !t->value.empty()) {
            std::vector<std::string> names;
            std::size_t off = 1;
            bool good = true;
            for (std::size_t i = 0; i < t->value[0] && good; i++) {
                if (off + 2 > t->value.size()) { good = false; break; }
                std::size_t n = detail::Get16(t->value, off);
                if (off + 2 + n > t->value.size()) { good = false; break; }
                names.emplace_back(reinterpret_cast<const char*>(t->value.data() + off + 2), n);
                off += 2 + n;
            }
            if (good && off == t->value.size()) out.fqdn = std::move(names);
        }
        return out;
    }

    // Get Packet Service Status: TLV 0x01 u8 (2 = connected) — whether the
    // bound client sees the port's call.
    inline std::optional<std::uint8_t> PacketServiceStatus(const Response& r) {
        const Tlv* t = r.Find(0x01);
        if (!t || t->value.size() != 1) return std::nullopt;
        return t->value[0];
    }

    // Get Profile Settings: TLV 0x1F "PCSCF Address Using PCO" u8 — whether
    // the profile has the modem ask the network at all.
    inline std::optional<bool> ProfilePcscfViaPco(const Response& r) {
        const Tlv* t = r.Find(0x1F);
        if (!t || t->value.size() != 1) return std::nullopt;
        return t->value[0] != 0;
    }
}
