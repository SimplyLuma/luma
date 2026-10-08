// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
/*
Imsd:Util — small self-contained primitives the rest of the engine builds on:
random SIP tokens, IPv6 detection + host/port formatting, hex/base64 decoding,
and MD5 (RFC 1321). None of the standard library ships these last three, and
the IMS AKAv1-MD5 digest (Imsd:Aka) needs MD5 over raw bytes plus base64 to
unwrap the RAND||AUTN nonce, so they live here rather than pulling in a crypto
dependency. Pure std C++ (no GLib, no I/O); behavior is pinned by tests/Util.
*/

export module Imsd:Util;
import std;

export namespace imsd::util {

    // The UE's protected client/server ports (TS 33.203 sec-agree). Single
    // source of truth: Imsd:Messages puts them in Security-Client and every
    // protected Via/Contact, Imsd:Ipsec selects on them in the SA policies,
    // and the daemon binds them. They are also mirrored in the packaged
    // firewall rules (packaging/ims-pdn-up.sh) — changing them here breaks
    // an installed package's nftables accept rules.
    constexpr int PortUc = 45061;
    constexpr int PortUs = 45062;

    // An address is IPv6 iff it contains a colon (matches the Python is6()).
    inline bool Is6(std::string_view addr) { return addr.contains(':'); }

    // "[addr]:port" for IPv6, "addr:port" for IPv4 — the SIP host:port form.
    inline std::string HostPort(std::string_view addr, int port) {
        return Is6(addr) ? std::format("[{}]:{}", addr, port)
                         : std::format("{}:{}", addr, port);
    }

    // A random token of `n` chars over [a-z0-9] — SIP branch/tag/Call-ID
    // material (the Python rand()). The generator is a parameter so tests can
    // pin output; the default constructor seeds from random_device.
    class Rng {
    public:
        Rng() {
            std::random_device rd;
            gen_.seed((static_cast<std::uint64_t>(rd()) << 32) ^ rd());
        }
        explicit Rng(std::uint64_t seed) { gen_.seed(seed); }

        std::string Token(std::size_t n) {
            static constexpr std::string_view Alphabet =
                "abcdefghijklmnopqrstuvwxyz0123456789";
            std::uniform_int_distribution<std::size_t> dist(0, Alphabet.size() - 1);
            std::string s;
            s.reserve(n);
            for (std::size_t i = 0; i < n; i++)
                s.push_back(Alphabet[dist(gen_)]);
            return s;
        }

        // A random integer in [lo, hi] — used for SPIs, RTP SSRC/seq/ts.
        std::uint32_t UInt(std::uint32_t lo, std::uint32_t hi) {
            std::uniform_int_distribution<std::uint32_t> dist(lo, hi);
            return dist(gen_);
        }

    private:
        std::mt19937_64 gen_;
    };

    // RFC 4122 version-4 UUID from the caller's Rng — the shape the stock
    // modem mints for its registered Contact user-part (oracle capture
    // 2026-07-21, journal/ims.md s53).
    inline std::string Uuid4(Rng& rng) {
        std::uint32_t a = rng.UInt(0, 0xFFFFFFFFu);
        std::uint32_t b = rng.UInt(0, 0xFFFFFFFFu);
        std::uint32_t c = rng.UInt(0, 0xFFFFFFFFu);
        std::uint32_t d = rng.UInt(0, 0xFFFFFFFFu);
        b = (b & 0xFFFF0FFFu) | 0x00004000u;   // version nibble = 4
        c = (c & 0x3FFFFFFFu) | 0x80000000u;   // variant bits = 10
        return std::format("{:08x}-{:04x}-{:04x}-{:04x}-{:04x}{:08x}", a, b >> 16, b & 0xFFFFu, c >> 16, c & 0xFFFFu, d);
    }

    // Lowercase hex of a byte span (no separators).
    inline std::string ToHex(std::span<const std::uint8_t> bytes) {
        std::string s;
        s.reserve(bytes.size() * 2);
        for (std::uint8_t b : bytes)
            s += std::format("{:02x}", b);
        return s;
    }

    // Decode a hex string (any case, even length) to bytes; nullopt on a bad
    // char or odd length. Colons are skipped so qmicli's "AB:CD:.." APDU form
    // decodes directly.
    inline std::optional<std::vector<std::uint8_t>> FromHex(std::string_view hex) {
        auto nibble = [](char c) -> int {
            if (c >= '0' && c <= '9') return c - '0';
            if (c >= 'a' && c <= 'f') return c - 'a' + 10;
            if (c >= 'A' && c <= 'F') return c - 'A' + 10;
            return -1;
        };
        std::vector<std::uint8_t> out;
        int hi = -1;
        for (char c : hex) {
            if (c == ':' || c == ' ') continue;
            int v = nibble(c);
            if (v < 0) return std::nullopt;
            if (hi < 0) {
                hi = v;
            } else {
                out.push_back(static_cast<std::uint8_t>((hi << 4) | v));
                hi = -1;
            }
        }
        if (hi >= 0)
            return std::nullopt;  // odd number of nibbles
        return out;
    }

    // Standard base64 decode (RFC 4648, '+' '/', '=' padding). Whitespace is
    // ignored; nullopt on an invalid character. The IMS 401 nonce is the
    // base64 of RAND(16)||AUTN(16)||... — this unwraps it.
    inline std::optional<std::vector<std::uint8_t>> FromBase64(std::string_view in) {
        auto val = [](char c) -> int {
            if (c >= 'A' && c <= 'Z') return c - 'A';
            if (c >= 'a' && c <= 'z') return c - 'a' + 26;
            if (c >= '0' && c <= '9') return c - '0' + 52;
            if (c == '+') return 62;
            if (c == '/') return 63;
            return -1;
        };
        std::vector<std::uint8_t> out;
        int acc = 0;
        int bits = 0;
        for (char c : in) {
            if (c == '=') break;
            if (c == '\r' || c == '\n' || c == ' ' || c == '\t') continue;
            int v = val(c);
            if (v < 0) return std::nullopt;
            acc = (acc << 6) | v;
            bits += 6;
            if (bits >= 8) {
                bits -= 8;
                out.push_back(static_cast<std::uint8_t>((acc >> bits) & 0xFF));
            }
        }
        return out;
    }

    // ---- MD5 (RFC 1321), streaming over arbitrary bytes. ------------------
    class Md5 {
    public:
        Md5() { Reset(); }

        void Update(std::span<const std::uint8_t> data) {
            std::size_t i = 0;
            std::size_t bufFill = static_cast<std::size_t>((count_ >> 3) & 0x3F);
            count_ += static_cast<std::uint64_t>(data.size()) << 3;
            if (bufFill) {
                std::size_t need = 64 - bufFill;
                if (data.size() < need) {
                    std::copy_n(data.begin(), data.size(), buf_.begin() + bufFill);
                    return;
                }
                std::copy_n(data.begin(), need, buf_.begin() + bufFill);
                Transform(buf_.data());
                i = need;
            }
            for (; i + 64 <= data.size(); i += 64)
                Transform(data.data() + i);
            std::copy(data.begin() + i, data.end(), buf_.begin());
        }

        void Update(std::string_view s) {
            Update(std::span<const std::uint8_t>(reinterpret_cast<const std::uint8_t*>(s.data()), s.size()));
        }

        std::array<std::uint8_t, 16> Digest() {
            std::array<std::uint8_t, 8> lenBytes{};
            for (int i = 0; i < 8; i++)
                lenBytes[i] = static_cast<std::uint8_t>((count_ >> (8 * i)) & 0xFF);
            static constexpr std::uint8_t pad[64] = {0x80};
            std::size_t bufFill = static_cast<std::size_t>((count_ >> 3) & 0x3F);
            std::size_t padLen = (bufFill < 56) ? (56 - bufFill) : (120 - bufFill);
            Update(std::span<const std::uint8_t>(pad, padLen));
            Update(std::span<const std::uint8_t>(lenBytes.data(), 8));
            std::array<std::uint8_t, 16> out{};
            for (int i = 0; i < 4; i++)
                for (int j = 0; j < 4; j++)
                    out[i * 4 + j] = static_cast<std::uint8_t>((state_[i] >> (8 * j)) & 0xFF);
            return out;
        }

        // Convenience: hex digest of a single buffer.
        static std::string HexDigest(std::span<const std::uint8_t> data) {
            Md5 m;
            m.Update(data);
            auto d = m.Digest();
            return ToHex(d);
        }
        static std::string HexDigest(std::string_view s) {
            return HexDigest(std::span<const std::uint8_t>(reinterpret_cast<const std::uint8_t*>(s.data()), s.size()));
        }

    private:
        std::array<std::uint32_t, 4> state_;
        std::uint64_t count_;  // bit count
        std::array<std::uint8_t, 64> buf_;

        void Reset() {
            state_ = {0x67452301u, 0xefcdab89u, 0x98badcfeu, 0x10325476u};
            count_ = 0;
            buf_.fill(0);
        }

        static std::uint32_t Rotl(std::uint32_t x, int c) {
            return (x << c) | (x >> (32 - c));
        }

        void Transform(const std::uint8_t* block) {
            static constexpr std::uint32_t K[64] = {
                0xd76aa478,0xe8c7b756,0x242070db,0xc1bdceee,0xf57c0faf,0x4787c62a,
                0xa8304613,0xfd469501,0x698098d8,0x8b44f7af,0xffff5bb1,0x895cd7be,
                0x6b901122,0xfd987193,0xa679438e,0x49b40821,0xf61e2562,0xc040b340,
                0x265e5a51,0xe9b6c7aa,0xd62f105d,0x02441453,0xd8a1e681,0xe7d3fbc8,
                0x21e1cde6,0xc33707d6,0xf4d50d87,0x455a14ed,0xa9e3e905,0xfcefa3f8,
                0x676f02d9,0x8d2a4c8a,0xfffa3942,0x8771f681,0x6d9d6122,0xfde5380c,
                0xa4beea44,0x4bdecfa9,0xf6bb4b60,0xbebfbc70,0x289b7ec6,0xeaa127fa,
                0xd4ef3085,0x04881d05,0xd9d4d039,0xe6db99e5,0x1fa27cf8,0xc4ac5665,
                0xf4292244,0x432aff97,0xab9423a7,0xfc93a039,0x655b59c3,0x8f0ccc92,
                0xffeff47d,0x85845dd1,0x6fa87e4f,0xfe2ce6e0,0xa3014314,0x4e0811a1,
                0xf7537e82,0xbd3af235,0x2ad7d2bb,0xeb86d391};
            static constexpr int S[64] = {
                7,12,17,22,7,12,17,22,7,12,17,22,7,12,17,22,
                5,9,14,20,5,9,14,20,5,9,14,20,5,9,14,20,
                4,11,16,23,4,11,16,23,4,11,16,23,4,11,16,23,
                6,10,15,21,6,10,15,21,6,10,15,21,6,10,15,21};
            std::uint32_t M[16];
            for (int i = 0; i < 16; i++)
                M[i] = static_cast<std::uint32_t>(block[i * 4]) |
                       (static_cast<std::uint32_t>(block[i * 4 + 1]) << 8) |
                       (static_cast<std::uint32_t>(block[i * 4 + 2]) << 16) |
                       (static_cast<std::uint32_t>(block[i * 4 + 3]) << 24);
            std::uint32_t a = state_[0];
            std::uint32_t b = state_[1];
            std::uint32_t c = state_[2];
            std::uint32_t d = state_[3];
            for (int i = 0; i < 64; i++) {
                std::uint32_t f;
                int g;
                if (i < 16)       { f = (b & c) | (~b & d);        g = i; }
                else if (i < 32)  { f = (d & b) | (~d & c);        g = (5 * i + 1) & 15; }
                else if (i < 48)  { f = b ^ c ^ d;                 g = (3 * i + 5) & 15; }
                else              { f = c ^ (b | ~d);              g = (7 * i) & 15; }
                f = f + a + K[i] + M[g];
                a = d; d = c; c = b;
                b = b + Rotl(f, S[i]);
            }
            state_[0] += a; state_[1] += b; state_[2] += c; state_[3] += d;
        }
    };
}
