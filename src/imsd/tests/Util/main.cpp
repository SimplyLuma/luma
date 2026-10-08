// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
// Imsd:Util unit tests — MD5 against the RFC 1321 test suite, base64/hex
// decoding, host/port formatting, and the deterministic-seed RNG. MD5 and
// base64 are our own (the standard library ships neither) and the AKAv1-MD5
// digest depends on them exactly, so these are pinned hard.
import std;
import Imsd;

using namespace imsd::util;

namespace {
    int Failures = 0;
    void Check(bool cond, std::string_view msg) {
        if (!cond) {
            std::println(std::cerr, "FAIL: {}", msg);
            ++Failures;
        }
    }
}

int main() {
    // ---- MD5: the RFC 1321 appendix A.5 test suite
    Check(Md5::HexDigest("") == "d41d8cd98f00b204e9800998ecf8427e", "md5 empty");
    Check(Md5::HexDigest("a") == "0cc175b9c0f1b6a831c399e269772661", "md5 a");
    Check(Md5::HexDigest("abc") == "900150983cd24fb0d6963f7d28e17f72", "md5 abc");
    Check(Md5::HexDigest("message digest") == "f96b697d7cb7938d525a2f31aaf161d0", "md5 message digest");
    Check(Md5::HexDigest("abcdefghijklmnopqrstuvwxyz") == "c3fcd3d76192e4007dfb496cca67e13b", "md5 alphabet");
    Check(Md5::HexDigest("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789") == "d174ab98d277d9f5a5611c2c9f419d9f", "md5 alnum");
    Check(Md5::HexDigest("12345678901234567890123456789012345678901234567890" "123456789012345678901234567890") == "57edf4a22be3c955ac49da2e2107b67a", "md5 80 digits");
    // streaming across a block boundary must equal a single-shot digest
    {
        std::string big(200, 'x');
        Md5 m;
        m.Update(std::string_view(big).substr(0, 13));
        m.Update(std::string_view(big).substr(13, 100));
        m.Update(std::string_view(big).substr(113));
        Check(ToHex(m.Digest()) == Md5::HexDigest(big), "md5 streaming == oneshot");
    }
    // ---- base64 decode
    {
        auto d = FromBase64("aGVsbG8=");
        Check(d.has_value(), "b64 decodes");
        Check(std::string(d->begin(), d->end()) == "hello", "b64 hello");
    }
    {
        // 32-byte RAND||AUTN style payload round-trips through hex->b64->decode
        auto d = FromBase64("TWFuIGlzIGRpc3Rpbmd1aXNoZWQ=");
        Check(d && std::string(d->begin(), d->end()) == "Man is distinguished", "b64 longer");
    }
    Check(!FromBase64("****").has_value(), "b64 rejects junk");

    // ---- hex
    {
        auto d = FromHex("00ff10");
        Check(d && d->size() == 3 && (*d)[1] == 0xFF, "hex decodes");
        auto c = FromHex("A0:00:00:00:87");
        Check(c && c->size() == 5 && (*c)[0] == 0xA0, "hex skips colons (APDU form)");
        Check(!FromHex("abc").has_value(), "hex rejects odd length");
        Check(!FromHex("zz").has_value(), "hex rejects bad char");
        std::array<std::uint8_t, 3> b{0x0a, 0x84, 0xab};
        Check(ToHex(b) == "0a84ab", "tohex");
    }

    // ---- host/port + is6
    Check(Is6("2001:db8::1") && !Is6("10.0.0.1"), "is6");
    Check(HostPort("2001:db8::1", 45061) == "[2001:db8::1]:45061", "hostport v6");
    Check(HostPort("10.0.0.1", 5060) == "10.0.0.1:5060", "hostport v4");

    // ---- RNG: deterministic seed reproducibility + alphabet + length
    {
        Rng a(1234), b(1234);
        std::string ta = a.Token(20), tb = b.Token(20);
        Check(ta.size() == 20, "token length");
        Check(ta == tb, "same seed -> same token");
        for (char c : ta)
            Check((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9'), "token alphabet");
        Rng c(9999);
        Check(c.Token(20) != ta, "different seed -> different token");
    }

    // ---- Uuid4: RFC 4122 v4 shape
    {
        Rng r(42);
        std::string u = Uuid4(r);
        Check(u.size() == 36, "uuid length");
        Check(u[8] == '-' && u[13] == '-' && u[18] == '-' && u[23] == '-', "uuid dashes");
        Check(u[14] == '4', "uuid version nibble");
        Check(u[19] == '8' || u[19] == '9' || u[19] == 'a' || u[19] == 'b', "uuid variant nibble");
        for (std::size_t i = 0; i < u.size(); i++)
            if (i != 8 && i != 13 && i != 18 && i != 23) Check((u[i] >= '0' && u[i] <= '9') || (u[i] >= 'a' && u[i] <= 'f'), "uuid hex alphabet");
        Rng r2(42);
        Check(Uuid4(r2) == u, "same seed -> same uuid");
    }

    if (Failures == 0) std::println("Util: all tests passed");
    return Failures;
}
