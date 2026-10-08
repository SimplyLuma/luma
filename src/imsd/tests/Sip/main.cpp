// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types
// Imsd:Sip unit tests — header access, status-line parsing, granted-expires
// extraction, caller-identity extraction, and the TCP frame buffer (split
// delivery, keepalive pongs, pipelined messages). These pin the module's
// edge-case behavior; see the module header before "fixing" one.
import std;
import Imsd;

using namespace imsd::sip;

namespace {
    int Failures = 0;

    void Check(bool cond, std::string_view msg) {
        if (!cond) {
            std::println(std::cerr, "FAIL: {}", msg);
            ++Failures;
        }
    }

    const std::string Resp200 =
        "SIP/2.0 200 OK\r\n"
        "Via: SIP/2.0/TCP [2001:db8::1]:45061;branch=z9hG4bK1\r\n"
        "Via: SIP/2.0/TCP [2001:db8::2]:5060;branch=z9hG4bK2\r\n"
        "From: <sip:user@ims.mnc001.mcc001.3gppnetwork.org>;tag=abc\r\n"
        "To: <sip:user@ims.mnc001.mcc001.3gppnetwork.org>;tag=def\r\n"
        "Call-ID: 12345@host\r\n"
        "CSeq: 2 REGISTER\r\n"
        "contact: <sip:user@[2001:db8::1]:45061>;expires=600000\r\n"
        "P-Associated-URI: <sip:+31612345678@ims.mnc001.mcc001.3gppnetwork.org>\r\n"
        "Content-Length: 0\r\n"
        "\r\n";
}

int main() {
    // ---- Status
    Check(Status(Resp200) == 200, "Status parses 200");
    Check(Status("SIP/2.0 183 Session Progress\r\n\r\n") == 183, "Status parses 183");
    Check(!Status("INVITE sip:+31@x SIP/2.0\r\n\r\n").has_value(), "Status rejects a request start-line");
    Check(!Status("SIP/2.0 xx3\r\n").has_value(), "Status rejects non-digits");

    // ---- Header / Headers
    Check(Header(Resp200, "Call-ID") == "12345@host", "Header finds Call-ID");
    Check(Header(Resp200, "call-id") == "12345@host", "Header is case-insensitive (query)");
    Check(Header(Resp200, "Contact") == "<sip:user@[2001:db8::1]:45061>;expires=600000", "Header is case-insensitive (message)");
    Check(!Header(Resp200, "Route").has_value(), "Header absent -> nullopt");
    Check(Header(Resp200, "CSeq") == "2 REGISTER", "Header trims the value");
    Check(Headers(Resp200, "Via").size() == 2, "Headers returns every Via");
    Check(Headers(Resp200, "Via")[1] == "SIP/2.0/TCP [2001:db8::2]:5060;branch=z9hG4bK2", "Headers keeps order");
    // name must sit at line start: "P-Associated-URI" must not match "URI"
    Check(!Header(Resp200, "URI").has_value(), "Header anchors at line start");
    // bare "Name:" (only CR after the colon) matches with empty value
    Check(Header("X-Empty:\r\nX-Real: v\r\n\r\n", "X-Empty") == "", "bare header matches with empty value");

    // ---- GrantedExpires
    Check(GrantedExpires(Resp200) == 600000, "GrantedExpires from Contact expires=");
    Check(GrantedExpires("SIP/2.0 200 OK\r\nExpires: 3600\r\n\r\n") == 3600, "GrantedExpires falls back to Expires");
    Check(!GrantedExpires("SIP/2.0 200 OK\r\nExpires: abc\r\n\r\n").has_value(), "non-numeric Expires -> nullopt");
    Check(!GrantedExpires("SIP/2.0 200 OK\r\n\r\n").has_value(), "no lifetime -> nullopt");
    Check(GrantedExpires("SIP/2.0 200 OK\r\n" "Contact: <sip:a@b>\r\n" "Contact: <sip:c@d>;expires=7200\r\n\r\n") == 7200, "GrantedExpires scans all Contacts");

    // ---- FrameBuffer
    {
        FrameBuffer fb;
        fb.Append(Resp200);
        auto m = fb.TryExtract();
        Check(m == Resp200, "whole message extracts intact");
        Check(!fb.TryExtract().has_value(), "buffer empty after extract");
    }
    {
        // split mid-header and mid-body
        std::string body = "v=0\r\no=- 1 1 IN IP6 ::1\r\n";
        std::string msg = std::format("SIP/2.0 200 OK\r\nContent-Length: {}\r\n\r\n{}", body.size(), body);
        FrameBuffer fb;
        fb.Append(msg.substr(0, 20));
        Check(!fb.TryExtract().has_value(), "incomplete head -> nullopt");
        fb.Append(msg.substr(20, msg.size() - 20 - 5));
        Check(!fb.TryExtract().has_value(), "incomplete body -> nullopt");
        fb.Append(msg.substr(msg.size() - 5));
        Check(fb.TryExtract() == msg, "split message reassembles");
    }
    {
        // leading CRLF keepalive pongs are discarded (RFC 5626)
        FrameBuffer fb;
        fb.Append("\r\n\r\n");
        Check(!fb.TryExtract().has_value(), "pure keepalive -> nullopt");
        Check(fb.Size() == 0, "keepalive bytes discarded");
        fb.Append("\r\n");
        fb.Append(Resp200);
        Check(fb.TryExtract() == Resp200, "message after keepalive extracts");
    }
    {
        // two pipelined messages come out one at a time, in order
        std::string first = "SIP/2.0 100 Trying\r\nContent-Length: 0\r\n\r\n";
        FrameBuffer fb;
        fb.Append(first + Resp200);
        Check(fb.TryExtract() == first, "pipelined: first message");
        Check(fb.TryExtract() == Resp200, "pipelined: second message");
        Check(!fb.TryExtract().has_value(), "pipelined: then empty");
    }
    {
        // missing Content-Length counts as 0
        std::string msg = "SIP/2.0 200 OK\r\nCall-ID: x\r\n\r\n";
        FrameBuffer fb;
        fb.Append(std::format("{}LEFTOVER", msg));
        Check(fb.TryExtract() == msg, "no Content-Length -> body length 0");
        Check(fb.Size() == 8, "trailing bytes stay buffered");
    }

    // ---- CallerId: P-Asserted-Identity preferred, From fallback
    {
        std::string inv =
            "INVITE sip:me SIP/2.0\r\n"
            "From: \"Someone\" <sip:+31687654321@ims.example;user=phone>;tag=f\r\n"
            "P-Asserted-Identity: <tel:+31612345678>\r\n"
            "Content-Length: 0\r\n\r\n";
        Check(CallerId(inv) == "+31612345678", "tel: P-Asserted-Identity wins");
    }
    {
        std::string inv =
            "INVITE sip:me SIP/2.0\r\n"
            "P-Asserted-Identity: <sip:+31612345678@ims.example;user=phone>\r\n"
            "Content-Length: 0\r\n\r\n";
        Check(CallerId(inv) == "+31612345678", "sip: identity yields the user part");
    }
    {
        std::string inv =
            "INVITE sip:me SIP/2.0\r\n"
            "From: <sip:+31687654321@ims.example;user=phone>;tag=f\r\n"
            "Content-Length: 0\r\n\r\n";
        Check(CallerId(inv) == "+31687654321", "From fallback without P-Asserted-Identity");
    }
    {
        std::string inv =
            "INVITE sip:me SIP/2.0\r\n"
            "From: <sip:anonymous@anonymous.invalid>;tag=f\r\n"
            "Content-Length: 0\r\n\r\n";
        Check(CallerId(inv) == "anonymous", "anonymous caller passes through");
    }
    {
        std::string inv =
            "INVITE sip:me SIP/2.0\r\n"
            "From: tel:+31600000001;tag=f\r\n"
            "Content-Length: 0\r\n\r\n";
        Check(CallerId(inv) == "+31600000001", "bare (bracketless) tel URI");
        Check(CallerId("INVITE sip:me SIP/2.0\r\nContent-Length: 0\r\n\r\n").empty(), "no identity headers -> empty");
    }

    // ---- ViaResponsePort: RFC 3261 18.2.2 UDP response routing
    {
        std::string upd =
            "UPDATE sip:me SIP/2.0\r\n"
            "Via: SIP/2.0/UDP [2001:db8::105]:6000;branch=z9hG4bKx;_aluscr_\r\n"
            "Content-Length: 0\r\n\r\n";
        Check(ViaResponsePort(upd) == 6000, "v6 sent-by port extracted");
        std::string rp =
            "UPDATE sip:me SIP/2.0\r\n"
            "Via: SIP/2.0/UDP [2001:db8::105]:6000;rport;branch=z9hG4bKx\r\n"
            "Content-Length: 0\r\n\r\n";
        Check(!ViaResponsePort(rp).has_value(), "rport -> symmetric (nullopt)");
        std::string v4 =
            "UPDATE sip:me SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 192.0.2.1:5062;branch=z9hG4bKx\r\n"
            "Content-Length: 0\r\n\r\n";
        Check(ViaResponsePort(v4) == 5062, "v4 sent-by port extracted");
        std::string np =
            "UPDATE sip:me SIP/2.0\r\n"
            "Via: SIP/2.0/UDP [2001:db8::105];branch=z9hG4bKx\r\n"
            "Content-Length: 0\r\n\r\n";
        Check(!ViaResponsePort(np).has_value(), "portless v6 sent-by -> nullopt");
        Check(!ViaResponsePort("UPDATE sip:me SIP/2.0\r\n\r\n").has_value(), "no Via -> nullopt");
    }

    if (Failures == 0) std::println("Sip: all tests passed");
    return Failures;
}
