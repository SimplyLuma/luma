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

    // ---- DatagramMessage (one UDP datagram = one message, RFC 3261 18.3)
    {
        std::string why;
        Check(!DatagramMessage("\r\n", &why).has_value() && why.empty(), "CRLF-only datagram is a pong, not a message, no problem");
        Check(!DatagramMessage("\r\n\r\n\r\n").has_value(), "CRLFCRLF-only datagram is a pong");
        std::string hdr = "SIP/2.0 200 OK\r\nCall-ID: a\r\n";
        auto m0 = DatagramMessage(hdr + "Content-Length: 0\r\n\r\n");
        Check(m0 && *m0 == hdr + "Content-Length: 0\r\n\r\n", "Content-Length 0, no body: whole datagram");
        auto m1 = DatagramMessage(hdr + "Content-Length: 4\r\n\r\nv=0\nJUNK");
        Check(m1 && *m1 == hdr + "Content-Length: 4\r\n\r\nv=0\n", "Content-Length that fits truncates the body");
        why.clear();
        Check(!DatagramMessage(hdr + "Content-Length: 40\r\n\r\nv=0\n", &why).has_value() && why.contains("truncated"), "Content-Length larger than the datagram: discarded, problem set");
        auto m2 = DatagramMessage("\r\n" + hdr + "\r\nv=0\n");
        Check(m2 && *m2 == hdr + "\r\nv=0\n", "no Content-Length: body is the rest of the datagram, leading CRLF stripped");
        why.clear();
        Check(!DatagramMessage("SIP/2.0 200 OK\r\nCall-ID: a\r\n", &why).has_value() && why.contains("terminator"), "no header terminator: discarded, problem set");
        auto m3 = DatagramMessage(hdr + "X-Note: Content-Length: 99\r\n\r\nv=0\n");
        Check(m3 && *m3 == hdr + "X-Note: Content-Length: 99\r\n\r\nv=0\n", "a header name inside a value does not match");
        auto m4 = DatagramMessage(hdr + "Content-Length: 99999999999999999999\r\n\r\nv=0\n", &why);
        Check(!m4.has_value(), "absurd Content-Length: discarded, no overflow");
    }

    // ---- RetryAfter / RefusalSummary (what a refused final is logged as)
    {
        std::string r503 =
            "SIP/2.0 503 Service Unavailable\r\n"
            "Via: SIP/2.0/TCP [2001:db8::db43]:45062;branch=z9hG4bKx\r\n"
            "Retry-After: 120\r\n"
            "Warning: 399 pcscf1.example \"S-CSCF unreachable\"\r\n"
            "Server: Mavenir-P-CSCF/4.2\r\n"
            "Content-Length: 0\r\n\r\n";
        Check(RetryAfter(r503) == 120, "Retry-After parsed");
        Check(RefusalSummary(r503) == "503 Service Unavailable; Retry-After: 120; Warning: 399 pcscf1.example \"S-CSCF unreachable\"; Server: Mavenir-P-CSCF/4.2", "summary: status line + reason headers in order");
        std::string r500 =
            "SIP/2.0 500 Server Internal Error\r\n"
            "Via: SIP/2.0/TCP [2001:db8::db43]:45062;branch=z9hG4bKx\r\n"
            "Content-Length: 0\r\n\r\n";
        Check(!RetryAfter(r500).has_value(), "no Retry-After -> nullopt");
        Check(RefusalSummary(r500) == "500 Server Internal Error", "summary without reason headers is the status line");
        std::string two =
            "SIP/2.0 403 Forbidden\r\n"
            "Reason: Q.850;cause=21;text=\"Call rejected\"\r\n"
            "Reason: SIP;cause=403\r\n"
            "Retry-After: soon\r\n"
            "Content-Length: 0\r\n\r\n";
        Check(!RetryAfter(two).has_value(), "non-numeric Retry-After -> nullopt");
        Check(RefusalSummary(two) == "403 Forbidden; Retry-After: soon; Reason: Q.850;cause=21;text=\"Call rejected\"; Reason: SIP;cause=403", "every instance of a reason header is kept");
        Check(RefusalSummary("NOTIFY sip:x SIP/2.0\r\nEvent: reg\r\n\r\n") == "NOTIFY sip:x SIP/2.0", "a request summarises to its request line");
        Check(RefusalSummary("") == "", "empty message -> empty summary");
    }

    // ---- ResponseOf (which datagram a REGISTER wait may judge)
    {
        Check(ResponseOf(Resp200, "12345@host", "2 REGISTER"), "the transaction's own final");
        Check(!ResponseOf(Resp200, "12345@host", "1 REGISTER"), "another CSeq is not it");
        std::string stale =
            "SIP/2.0 408 Request Timeout\r\n"
            "Via: SIP/2.0/UDP [2001:db8::db43]:5060;branch=z9hG4bKa;rport=5060\r\n"
            "Call-ID: earlier@[2001:db8::db43]\r\n"
            "CSeq: 1 REGISTER\r\n"
            "Content-Length: 0\r\n\r\n";
        Check(!ResponseOf(stale, "12345@host", "1 REGISTER"), "an earlier attempt's late final is not it");
        Check(ResponseOf(stale, "earlier@[2001:db8::db43]", "1 REGISTER"), "matched by Call-ID and CSeq");
        Check(!ResponseOf("NOTIFY sip:x SIP/2.0\r\nCall-ID: 12345@host\r\nCSeq: 2 REGISTER\r\n\r\n", "12345@host", "2 REGISTER"), "a request is never a response");
        Check(!ResponseOf("", "12345@host", "2 REGISTER"), "nothing is not it");
    }

    // ---- RegInfoContactState: our binding in a reg-event NOTIFY (RFC 3680)
    {
        using S = RegInfoContact::State;
        auto contact = [](std::string_view id, std::string_view state, std::string_view extra, std::string_view user) {
            return std::format(
                "\t\t<contact id=\"{}\" state=\"{}\"{}>\n"
                "\t\t\t<uri>sip:{}@[2a03:db8::1]:45062</uri>\n"
                "\t\t\t<unknown-param name=\"+sip.instance\">&quot;&lt;urn:gsma:imei:35901961-000000-0&gt;&quot; </unknown-param>\n"
                "\t\t</contact>\n", id, state, extra, user);
        };
        auto reginfo = [](std::string_view contacts) {
            return std::format(
                "<?xml version=\"1.0\"?>\n"
                "<reginfo xmlns=\"urn:ietf:params:xml:ns:reginfo\" version=\"1\" state=\"full\">\n"
                "\t<registration aor=\"sip:+447700900000@example.net\" id=\"2\" state=\"active\">\n{0}\t</registration>\n"
                "\t<registration aor=\"tel:+447700900000\" id=\"3\" state=\"active\">\n{0}\t</registration>\n"
                "</reginfo>\n", contacts);
        };
        const std::string me = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
        const std::string old = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
        // the first NOTIFY of a registration: our contact active in both registrations
        auto r0 = RegInfoContactState(reginfo(contact("3", "active", " event=\"created\" expires=\"10800\"", me)), me);
        Check(r0.state == S::Active, "our contact active -> Active");
        // O2 2026-09-24 10:43: the previous registration's contact deactivated by ours, ours created
        std::string both = contact("3", "terminated", " event=\"deactivated\"", old) + contact("4", "active", " event=\"created\" expires=\"10800\"", me);
        auto r1 = RegInfoContactState(reginfo(both), me);
        Check(r1.state == S::Active, "old contact terminated + ours active -> Active (the old one is not ours)");
        auto r1o = RegInfoContactState(reginfo(both), old);
        Check(r1o.state == S::Terminated && r1o.event == "deactivated" && r1o.retryAfter == 0, "seen from the old contact's side: Terminated/deactivated");
        // a network-initiated deregistration of our own binding
        auto r2 = RegInfoContactState(reginfo(contact("4", "terminated", " event=\"probation\" retry-after=\"120\"", me)), me);
        Check(r2.state == S::Terminated && r2.event == "probation" && r2.retryAfter == 120, "probation with retry-after");
        auto r3 = RegInfoContactState(reginfo(contact("4", "terminated", " event=\"rejected\"", me)), me);
        Check(r3.state == S::Terminated && r3.event == "rejected", "rejected");
        // active in one registration of the set beats terminated in another
        std::string mixed =
            "<reginfo version=\"2\" state=\"partial\">\n<registration aor=\"tel:+1\" id=\"3\" state=\"active\">\n" + contact("4", "terminated", " event=\"expired\"", me) +
            "</registration>\n<registration aor=\"sip:+1@x\" id=\"2\" state=\"active\">\n" + contact("4", "active", "", me) + "</registration>\n</reginfo>";
        Check(RegInfoContactState(mixed, me).state == S::Active, "active anywhere in the set -> Active");
        // not ours at all
        auto r4 = RegInfoContactState(reginfo(contact("9", "terminated", " event=\"unregistered\"", old)), me);
        Check(r4.state == S::NotListed && r4.event.empty(), "a document without our contact -> NotListed");
        Check(RegInfoContactState("", me).state == S::NotListed, "empty body -> NotListed");
        Check(RegInfoContactState("<reginfo><registration><contact id=\"1\" state=\"active\"><uri>sip:aaaaaaaa@x</uri></contact>", me).state == S::NotListed, "a user part that is only a prefix of ours does not match");
        Check(RegInfoContactState("<contact state=\"terminated\" event=\"expired\"><uri>sip:" + me + "@h</uri>", me).state == S::Terminated, "an unterminated element still parses");
    }

    if (Failures == 0) std::println("Sip: all tests passed");
    return Failures;
}
